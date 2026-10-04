from fastapi import APIRouter, Request

from app.deps import CurrentUser, Db, OptionalUser, start_session
from app.errors import AppError
from app.i18n import LANGUAGES, translate_error
from app.services import users
from app.web.common import (
    LANGUAGE_COOKIE,
    LANGUAGE_COOKIE_MAX_AGE,
    Form,
    flash,
    pick_language,
    redirect,
    render,
    safe_path,
    text,
)

router = APIRouter()


@router.get("/")
def home(user: CurrentUser):
    return redirect("/topics")


@router.get("/login")
def login_page(request: Request, db: Db, user: OptionalUser):
    if user:
        return redirect("/")
    return render(request, db, "login.html", None)


@router.post("/login")
def login(request: Request, db: Db, form: Form):
    try:
        user = users.authenticate(db, text(form, "username"), str(form.get("password", "")))
    except AppError as exc:
        flash(request, translate_error(pick_language(request), exc), "error")
        return redirect("/login")
    start_session(request, user)
    return redirect("/")


@router.post("/logout")
def logout(request: Request, form: Form):
    request.session.clear()
    return redirect("/login")


@router.post("/language")
def set_language(request: Request, form: Form):
    """Remember the chosen interface language in a cookie and return to the page the user was on."""
    response = redirect(safe_path(text(form, "next")))
    language = text(form, "lang")
    if language in LANGUAGES:
        response.set_cookie(
            LANGUAGE_COOKIE,
            language,
            max_age=LANGUAGE_COOKIE_MAX_AGE,
            samesite="lax",
            httponly=True,
            secure=request.app.state.settings.cookie_secure,
        )
    return response
