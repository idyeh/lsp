"""System settings stored as key/value text rows (SRS 7.8).

Missing rows fall back to DEFAULTS, so a fresh database needs no seeding.
The topic category list is one of these settings, which keeps it configurable
without code changes (SRS 4.2).
"""

import json
import logging
import re

from sqlalchemy.orm import Session

from app.errors import AppError
from app.models import SystemSetting

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    "software-engineering",
    "artificial-intelligence",
    "computer-vision",
    "robotics",
    "research-methods",
    "competition-experience",
    "generative-media",
    "interactive-film",
    "game-development",
    "open-topic",
]

# Voting starts closed: the organiser opens it once the topic pool is ready.
DEFAULTS: dict = {
    "semester_name": "Current semester",
    "voting_open": False,
    "target_session_count": 8,
    "target_core_count": 4,
    "target_optional_count": 4,
    "categories": DEFAULT_CATEGORIES,
}

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_CATEGORIES = 50


def category_label(slug: str) -> str:
    return slug.replace("-", " ").capitalize()


def normalise_category(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def _encode(key: str, value) -> str:
    if key == "categories":
        return json.dumps(value)
    if key == "voting_open":
        return "true" if value else "false"
    return str(value)


def _decode(key: str, raw: str):
    default = DEFAULTS[key]
    try:
        if key == "categories":
            value = json.loads(raw)
            if not isinstance(value, list):
                raise ValueError("categories must be a list")
            return value
        if key == "voting_open":
            return raw == "true"
        if isinstance(default, int):
            return int(raw)
        return raw
    except ValueError:
        logger.warning("Setting %r has an unreadable stored value; using the default.", key)
        return default


def get_all(db: Session) -> dict:
    values = dict(DEFAULTS)
    for row in db.query(SystemSetting).all():
        if row.key in DEFAULTS:
            values[row.key] = _decode(row.key, row.value)
    return values


def get(db: Session, key: str):
    row = db.get(SystemSetting, key)
    return DEFAULTS[key] if row is None else _decode(key, row.value)


def is_voting_open(db: Session) -> bool:
    return get(db, "voting_open")


def get_categories(db: Session) -> list[str]:
    return get(db, "categories")


def validate_category(db: Session, category: str) -> str:
    """Return the normalised category slug, or raise if it is not in the configured list."""
    slug = normalise_category(category)
    if slug not in get_categories(db):
        raise AppError(422, "INVALID_CATEGORY", f"'{category}' is not one of the configured categories.")
    return slug


def update(db: Session, changes: dict) -> dict:
    """Apply a partial update; keys must be known settings."""
    unknown = set(changes) - set(DEFAULTS)
    if unknown:
        raise AppError(422, "VALIDATION_ERROR", f"Unknown setting(s): {', '.join(sorted(unknown))}.")
    for key, value in changes.items():
        if value is None:
            raise AppError(422, "VALIDATION_ERROR", f"Setting '{key}' cannot be null.")
    if "categories" in changes:
        changes["categories"] = _clean_categories(changes["categories"])
    for key, value in changes.items():
        row = db.get(SystemSetting, key)
        if row is None:
            db.add(SystemSetting(key=key, value=_encode(key, value)))
        else:
            row.value = _encode(key, value)
    db.commit()
    return get_all(db)


def _clean_categories(categories: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in categories:
        slug = normalise_category(item)
        if not _SLUG.match(slug) or len(slug) > 64:
            raise AppError(
                422, "INVALID_CATEGORY", f"'{item}' is not a valid category (use letters, digits and hyphens)."
            )
        if slug not in cleaned:
            cleaned.append(slug)
    if not cleaned:
        raise AppError(422, "INVALID_CATEGORY", "At least one category is required.")
    if len(cleaned) > MAX_CATEGORIES:
        raise AppError(422, "INVALID_CATEGORY", f"At most {MAX_CATEGORIES} categories are allowed.")
    return cleaned
