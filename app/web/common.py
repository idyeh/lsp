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
from app.models import User
from app.services import settings as settings_service

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Human-readable labels for the values stored in the database.
LABELS = {
    "interest": {3: "Very interested", 2: "Interested", 1: "Slightly interested", 0: "No preference"},
    "presentation": {"none": "Not presenting", "maybe": "Maybe", "co_present": "Co-present", "lead": "Lead"},
    "attendance": {
        "attend": "Attend",
        "probably_attend": "Probably attend",
        "probably_absent": "Probably absent",
        "not_sure": "Not sure",
    },
    "session_status": {
        "proposed": "Provisional",
        "confirmed": "Confirmed",
        "completed": "Completed",
        "cancelled": "Cancelled",
    },
    "presenter_role": {"lead": "Lead", "co_presenter": "Co-presenter"},
    "selection": {"confirmed": "Confirmed", "proposed": "Provisional", "not_selected": "Not selected"},
}
templates.env.globals["labels"] = LABELS
templates.env.filters["category_label"] = settings_service.category_label
templates.env.filters["titlecase"] = lambda value: str(value).replace("_", " ").capitalize()


# --- sessions, flash messages and CSRF ----------------------------------------


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = request.session["csrf_token"] = secrets.token_urlsafe(32)
    return token


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session["flash"] = [*request.session.get("flash", []), [level, message]]


async def csrf_form(request: Request) -> FormData:
    """Parse the submitted form and verify its CSRF token. Used by every state-changing page."""
    form = await request.form()
    expected = request.session.get("csrf_token")
    supplied = form.get("csrf_token")
    if not expected or not isinstance(supplied, str) or not hmac.compare_digest(expected, supplied):
        raise AppError(403, "CSRF_FAILED", "Your form expired or was invalid. Please go back, reload the page and try again.")
    return form


Form = Annotated[FormData, Depends(csrf_form)]


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)  # 303: the browser follows with GET after a POST


@contextmanager
def action(request: Request, success: str | None = None) -> Iterator[None]:
    """Run a state-changing step; show a flash message instead of failing the page on a rule violation."""
    try:
        yield
    except AppError as exc:
        flash(request, exc.message, "error")
    except ValidationError as exc:
        first = exc.errors()[0]
        field = str(first["loc"][0]).replace("_", " ").capitalize() if first["loc"] else "Input"
        flash(request, f"{field}: {first['msg']}", "error")
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
    context.update(
        user=user,
        csrf_token=csrf_token(request),
        flashes=request.session.pop("flash", []),
        settings=settings_service.get_all(db) if db is not None else None,
        path=request.url.path,
    )
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def render_error(request: Request, status_code: int, message: str):
    if status_code >= 500:  # do not touch the database while handling a server failure
        return render(request, None, "error.html", None, status_code, error_status=status_code, message=message)
    with request.app.state.sessionmaker() as db:
        user = optional_user(request, db)
        return render(request, db, "error.html", user, status_code, error_status=status_code, message=message)
