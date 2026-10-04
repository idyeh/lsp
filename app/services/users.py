"""User accounts. Accounts are created by organisers; there is no self-registration."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import Role
from app.errors import AppError
from app.models import User
from app.security import hash_password, verify_password

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256


def normalise_username(username: str) -> str:
    return username.strip().lower()


def _check_password(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise AppError(
            422,
            "WEAK_PASSWORD",
            f"Password must be between {MIN_PASSWORD_LENGTH} and {MAX_PASSWORD_LENGTH} characters.",
        )


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username == normalise_username(username)))
    valid = verify_password(user.password_hash if user else None, password)
    if not (user and valid and user.is_active):
        raise AppError(401, "AUTH_INVALID_CREDENTIALS", "Invalid username or password.")
    return user


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise AppError(404, "USER_NOT_FOUND", "User not found.")
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(User.display_name, User.id)))


def create_user(
    db: Session,
    username: str,
    display_name: str,
    password: str,
    role: Role = Role.student,
    year_group: str | None = None,
) -> User:
    username = normalise_username(username)
    display_name = display_name.strip()
    if not username or len(username) > 64 or any(c.isspace() for c in username):
        raise AppError(422, "VALIDATION_ERROR", "Username must be 1-64 characters without spaces.")
    if not display_name or len(display_name) > 100:
        raise AppError(422, "VALIDATION_ERROR", "Display name must be 1-100 characters.")
    year_group = (year_group or "").strip() or None
    if year_group and len(year_group) > 20:
        raise AppError(422, "VALIDATION_ERROR", "Year group must be at most 20 characters.")
    _check_password(password)
    user = User(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        role=Role(role),
        year_group=year_group,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "DUPLICATE_USER", f"Username '{username}' is already taken.") from None
    return user


def set_password(db: Session, user: User, password: str) -> None:
    _check_password(password)
    user.password_hash = hash_password(password)
    db.commit()


def _active_organiser_count(db: Session) -> int:
    return db.scalar(
        select(func.count(User.id)).where(User.role == Role.organiser, User.is_active.is_(True))
    )


def _is_last_active_organiser(db: Session, user: User) -> bool:
    return user.role == Role.organiser and user.is_active and _active_organiser_count(db) <= 1


def set_active(db: Session, user: User, is_active: bool) -> None:
    # Never lock everyone out of organiser functions: accounts are managed in the UI, not the database.
    if not is_active and _is_last_active_organiser(db, user):
        raise AppError(409, "LAST_ORGANISER", "The last active organiser cannot be deactivated.")
    user.is_active = is_active
    db.commit()


def set_role(db: Session, user: User, role: Role) -> None:
    if role != Role.organiser and _is_last_active_organiser(db, user):
        raise AppError(409, "LAST_ORGANISER", "The last active organiser cannot be made a student.")
    user.role = role
    db.commit()
