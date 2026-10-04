"""Helpers shared by the server-rendered pages."""

import hmac
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.deps import optional_user
from app.errors import AppError
from app.i18n import DEFAULT_LANGUAGE, LANGUAGES, N_, describe_validation_error, format_date, translate, translate_error
from app.models import User
from app.services import settings as settings_service

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

LANGUAGE_COOKIE = "lsp_lang"
LANGUAGE_COOKIE_MAX_AGE = 365 * 24 * 3600

# Human-readable labels for the values stored in the database (translated where they are shown).
LABELS = {
    "interest": {
        3: N_("Very interested"),
        2: N_("Interested"),
        1: N_("Slightly interested"),
        0: N_("No preference"),
    },
    "presentation": {
        "none": N_("Not presenting"),
        "maybe": N_("Maybe"),
        "co_present": N_("Co-present"),
        "lead": N_("Lead"),
    },
    "attendance": {
        "attend": N_("Attend"),
        "probably_attend": N_("Probably attend"),
        "probably_absent": N_("Probably absent"),
        "not_sure": N_("Not sure"),
    },
    "session_status": {
        "proposed": N_("Provisional"),
        "confirmed": N_("Confirmed"),
        "completed": N_("Completed"),
        "cancelled": N_("Cancelled"),
    },
    "presenter_role": {"lead": N_("Lead"), "co_presenter": N_("Co-presenter")},
    "selection": {
        "confirmed": N_("Confirmed"),
        "proposed": N_("Provisional"),
        "not_selected": N_("Not selected"),
    },
    "topic_type": {"core": N_("Core"), "optional": N_("Optional")},
    "topic_status": {
        "draft": N_("Draft"),
        "active": N_("Active"),
        "closed": N_("Closed"),
        "archived": N_("Archived"),
    },
    "proposal_status": {
        "pending": N_("Pending"),
        "approved": N_("Approved"),
        "rejected": N_("Rejected"),
        "archived": N_("Archived"),
    },
    "role": {"student": N_("Student"), "organiser": N_("Organiser")},
    "suggestion": {
        "core": N_("Core"),
        "recommended": N_("Recommended"),
        "alternative": N_("Alternative"),
    },
}
templates.env.globals["labels"] = LABELS
# Changes whenever a static file does, so browsers never keep showing an old copy after an update.
STATIC_DIR = Path(__file__).parent / "static"
templates.env.globals["asset_version"] = int(max(f.stat().st_mtime for f in STATIC_DIR.iterdir()))


# --- language ---------------------------------------------------------------------


def pick_language(request: Request) -> str:
    """The language cookie if the user chose one, else the browser's preference, else English."""
    cached = getattr(request.state, "language", None)
    if cached:
        return cached
    language = request.cookies.get(LANGUAGE_COOKIE)
    if language not in LANGUAGES:
        language = DEFAULT_LANGUAGE
        for part in request.headers.get("accept-language", "").split(","):
            tag = part.split(";")[0].strip().lower()
            if tag.startswith("zh") or tag.startswith("en"):
                language = tag[:2]
                break
    request.state.language = language
    return language


def safe_path(value: str | None, default: str = "/") -> str:
    """Only allow redirects to a path on this site (never to another host)."""
    if value and value.startswith("/") and not value.startswith("//") and not any(c in value for c in "\\\r\n"):
        return value
    return default


# --- sessions, flash messages and CSRF ----------------------------------------


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = request.session["csrf_token"] = secrets.token_urlsafe(32)
    return token


def flash(request: Request, message: str, level: str = "info", **params) -> None:
    """Queue a message for the next page, translated into the language the user is using now."""
    text_ = translate(pick_language(request), message, **params)
    request.session["flash"] = [*request.session.get("flash", []), [level, text_]]


async def csrf_form(request: Request) -> FormData:
    """Parse the submitted form and verify its CSRF token. Used by every state-changing page."""
    form = await request.form()
    expected = request.session.get("csrf_token")
    supplied = form.get("csrf_token")
    if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
        raise AppError(
            403,
            "CSRF_FAILED",
            N_("Your form expired or was invalid. Please go back, reload the page and try again."),
        )
    return form


Form = Annotated[FormData, Depends(csrf_form)]


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)  # 303: the browser follows with GET after a POST


@contextmanager
def action(request: Request, success: str | None = None) -> Iterator[None]:
    """Run a state-changing step; show a flash message instead of failing the page on a rule violation."""
    language = pick_language(request)
    try:
        yield
    except AppError as exc:
        flash(request, translate_error(language, exc), "error")
    except ValidationError as exc:
        flash(request, describe_validation_error(language, exc), "error")
    else:
        if success:
            flash(request, success, "success")


# --- reading form values --------------------------------------------------------


def text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def blank_to_none(form: FormData, name: str) -> str | None:
    return text(form, name) or None


def form_int(form: FormData, name: str, error: AppError) -> int:
    try:
        return int(text(form, name))
    except ValueError:
        raise error from None


# --- rendering -------------------------------------------------------------------


def render(
    request: Request, db: Session | None, template: str, user: User | None, status_code: int = 200, **context
):
    language = pick_language(request)
    settings = settings_service.get_all(db) if db is not None else None
    labels = settings["category_labels"] if settings else {}
    semester_name = None
    if settings:
        semester_name = (language == "zh" and settings["semester_name_zh"]) or settings["semester_name"]
    query = f"?{request.url.query}" if request.url.query else ""
    context.update(
        user=user,
        csrf_token=csrf_token(request),
        flashes=request.session.pop("flash", []),  # already translated when queued
        settings=settings,
        semester_name=semester_name,
        path=request.url.path,
        current_url=request.url.path + query,
        lang=language,
        html_lang="zh-CN" if language == "zh" else "en",
        languages=LANGUAGES,
        # Translation helpers used by the templates:
        _=lambda message, **params: translate(language, message, **params),
        L=lambda topic: topic.localized(language),  # a topic's title, description and keywords in this language
        CL=lambda slug: settings_service.category_label(slug, language, labels),
        fmt_date=lambda value: format_date(language, value),
    )
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def render_error(request: Request, status_code: int, message: str, **params):
    """An error page. `message` is an English template (translated here) with optional parameters."""
    if status_code >= 500:  # do not touch the database while handling a server failure
        return render(
            request, None, "error.html", None, status_code, error_status=status_code, message=message, params=params
        )
    with request.app.state.sessionmaker() as db:
        user = optional_user(request, db)
        return render(
            request, db, "error.html", user, status_code, error_status=status_code, message=message, params=params
        )
