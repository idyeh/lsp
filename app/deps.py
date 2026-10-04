"""FastAPI dependencies shared by the JSON API and the web UI."""

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import User
from app.security import password_fingerprint
from app.i18n import N_


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.sessionmaker()
    try:
        yield db
    finally:
        db.close()


def start_session(request: Request, user: User) -> None:
    """Log the user in: replace any previous session data and issue a fresh CSRF token."""
    request.session.clear()
    request.session["user_id"] = user.id
    request.session["pw"] = password_fingerprint(user.password_hash)
    request.session["csrf_token"] = secrets.token_urlsafe(32)


def optional_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User | None:
    """The logged-in user, or None.

    Deactivated users lose access immediately, and so does any session that predates
    the user's latest password change.
    """
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or request.session.get("pw") != password_fingerprint(user.password_hash):
        request.session.clear()
        return None
    return user


def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise AppError(401, "AUTH_REQUIRED", N_("Authentication required."))
    return user


def require_organiser(user: Annotated[User, Depends(current_user)]) -> User:
    if not user.is_organiser:
        raise AppError(403, "PERMISSION_DENIED", N_("Organiser permission is required."))
    return user


Db = Annotated[Session, Depends(get_db)]
OptionalUser = Annotated[User | None, Depends(optional_user)]
CurrentUser = Annotated[User, Depends(current_user)]
Organiser = Annotated[User, Depends(require_organiser)]
