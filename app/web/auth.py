from fastapi import APIRouter, Request

from app.deps import CurrentUser, Db, OptionalUser, start_session
from app.errors import AppError
from app.services import users
from app.web.common import Form, flash, redirect, render, text

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
        flash(request, exc.message, "error")
        return redirect("/login")
    start_session(request, user)
    return redirect("/")


@router.post("/logout")
def logout(request: Request, form: Form):
    request.session.clear()
    return redirect("/login")
