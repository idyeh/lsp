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
from app.i18n import N_

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

# Chinese names for the default categories. English names are derived from the slug.
DEFAULT_CATEGORY_LABELS = {
    "software-engineering": {"zh": "软件工程"},
    "artificial-intelligence": {"zh": "人工智能"},
    "computer-vision": {"zh": "计算机视觉"},
    "robotics": {"zh": "机器人"},
    "research-methods": {"zh": "研究方法"},
    "competition-experience": {"zh": "竞赛经验"},
    "generative-media": {"zh": "生成式媒体"},
    "interactive-film": {"zh": "互动电影"},
    "game-development": {"zh": "游戏开发"},
    "open-topic": {"zh": "开放话题"},
}

# Voting starts closed: the organiser opens it once the topic pool is ready.
DEFAULTS: dict = {
    "semester_name": "Current semester",
    "semester_name_zh": "",  # optional Chinese name; the English one is shown when empty
    "voting_open": False,
    "target_session_count": 8,
    "target_core_count": 4,
    "target_optional_count": 4,
    "categories": DEFAULT_CATEGORIES,
    "category_labels": {},  # slug -> {"en": ..., "zh": ...}, stored on top of DEFAULT_CATEGORY_LABELS
}

_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_CATEGORIES = 50


_ACRONYMS = {"ai", "ar", "vr", "llm", "nlp", "hci"}


def _derive_label(slug: str) -> str:
    """'game-development' -> 'Game development'; known acronyms stay upper-case ('creative-ai' -> 'Creative AI')."""
    words = slug.split("-")
    label = [w.upper() if w in _ACRONYMS else w for w in words]
    if words[0] not in _ACRONYMS:
        label[0] = label[0].capitalize()
    return " ".join(label)


def category_label(slug: str, language: str = "en", labels: dict | None = None) -> str:
    """Display name of a category: the configured label for the language, else the English label,
    else a name derived from the slug."""
    entry = (labels or {}).get(slug, {})
    return entry.get(language) or entry.get("en") or _derive_label(slug)


def add_missing_categories(db: Session, slugs: list[str], labels: dict[str, dict[str, str]] | None = None) -> list[str]:
    """Append categories that are not configured yet, and fill in labels that are not set yet.

    Existing categories and labels are never changed. Returns the slugs that were added.
    """
    existing = get_categories(db)
    added = [s for s in dict.fromkeys(normalise_category(s) for s in slugs) if s not in existing]
    effective = get(db, "category_labels")
    row = db.get(SystemSetting, "category_labels")
    stored = _decode("category_labels", row.value) if row else {}
    new_labels = False
    for slug, names in (labels or {}).items():
        for language, name in names.items():
            if name and language not in effective.get(slug, {}):
                stored.setdefault(slug, {})[language] = name
                new_labels = True
    changes: dict = {}
    if added:
        changes["categories"] = [*existing, *added]
    if new_labels:
        changes["category_labels"] = stored
    if changes:
        update(db, changes)
    return added


def _merge_labels(stored: dict) -> dict:
    merged = {slug: dict(names) for slug, names in DEFAULT_CATEGORY_LABELS.items()}
    for slug, names in stored.items():
        merged.setdefault(slug, {}).update(names)
    return merged


def normalise_category(value: str) -> str:
    return value.strip().lower().replace(" ", "-").replace("_", "-")


def _encode(key: str, value) -> str:
    if key in ("categories", "category_labels"):
        return json.dumps(value)
    if key == "voting_open":
        return "true" if value else "false"
    return str(value)


def _decode(key: str, raw: str):
    default = DEFAULTS[key]
    try:
        if key in ("categories", "category_labels"):
            value = json.loads(raw)
            if not isinstance(value, type(default)):
                raise ValueError(f"{key} has the wrong type")
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
    values["category_labels"] = _merge_labels(values["category_labels"])
    return values


def get(db: Session, key: str):
    row = db.get(SystemSetting, key)
    value = DEFAULTS[key] if row is None else _decode(key, row.value)
    return _merge_labels(value) if key == "category_labels" else value


def is_voting_open(db: Session) -> bool:
    return get(db, "voting_open")


def get_categories(db: Session) -> list[str]:
    return get(db, "categories")


def validate_category(db: Session, category: str) -> str:
    """Return the normalised category slug, or raise if it is not in the configured list."""
    slug = normalise_category(category)
    if slug not in get_categories(db):
        raise AppError(422, "INVALID_CATEGORY", N_("'{category}' is not one of the configured categories."), category=category)
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
    if "category_labels" in changes:
        changes["category_labels"] = _clean_labels(changes["category_labels"])
    for key, value in changes.items():
        row = db.get(SystemSetting, key)
        if row is None:
            db.add(SystemSetting(key=key, value=_encode(key, value)))
        else:
            row.value = _encode(key, value)
    db.commit()
    return get_all(db)


def _clean_labels(labels: dict) -> dict:
    """Keep only non-empty labels for known languages. These replace the stored labels as a whole."""
    cleaned: dict[str, dict[str, str]] = {}
    for slug, names in labels.items():
        slug = normalise_category(slug)
        if not _SLUG.match(slug) or len(slug) > 64:
            raise AppError(
                422,
                "INVALID_CATEGORY",
                N_("'{item}' is not a valid category (use letters, digits and hyphens)."),
                item=slug,
            )
        kept = {lang: text.strip()[:100] for lang, text in names.items() if lang in ("en", "zh") and text.strip()}
        if kept:
            cleaned[slug] = kept
    return cleaned


def _clean_categories(categories: list[str]) -> list[str]:
    cleaned: list[str] = []
    for item in categories:
        slug = normalise_category(item)
        if not _SLUG.match(slug) or len(slug) > 64:
            raise AppError(
                422,
                "INVALID_CATEGORY",
                N_("'{item}' is not a valid category (use letters, digits and hyphens)."),
                item=item,
            )
        if slug not in cleaned:
            cleaned.append(slug)
    if not cleaned:
        raise AppError(422, "INVALID_CATEGORY", N_("At least one category is required."))
    if len(cleaned) > MAX_CATEGORIES:
        raise AppError(422, "INVALID_CATEGORY", N_("At most {max} categories are allowed."), max=MAX_CATEGORIES)
    return cleaned
