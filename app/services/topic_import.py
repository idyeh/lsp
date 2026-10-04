"""Import topics, and their translations, from JSON files.

Topics file: an object whose values are lists of topics, each shaped like the API's
topic input (title, description, category, topic_type, keywords, status), e.g.

    {"core_topics": [{...}], "elective_topics": [{...}]}

Translations file (optional): the topics' text in one other language, keyed by the
English title of each topic, plus optional names for categories in that language:

    {"language": "zh",
     "topics": {"English title": {"title": "...", "description": "...", "keywords": ["..."]}},
     "category_labels": {"software-engineering": "..."}}

Importing is safe to repeat: topics whose English title already exists are skipped (only a
missing translation is filled in), and categories or labels that already exist are untouched.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import AppError
from app.i18n import DEFAULT_LANGUAGE, LANGUAGES, describe_validation_error
from app.models import Topic, User
from app.schemas import TopicCreate, TopicTranslationIn
from app.services import settings as settings_service
from app.services import topics as topic_service


@dataclass
class TranslationFile:
    language: str
    topics: dict[str, TopicTranslationIn]
    category_labels: dict[str, str]


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already present
    translated: list[str] = field(default_factory=list)  # translation added to an existing topic
    untranslated: list[str] = field(default_factory=list)  # present but without a translation file entry
    unmatched_translations: list[str] = field(default_factory=list)  # entries matching no topic in the file
    categories_added: list[str] = field(default_factory=list)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AppError(404, "IMPORT_ERROR", f"File not found: {path}") from None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AppError(422, "IMPORT_ERROR", f"{path.name} is not valid JSON: {exc}") from None
    if not isinstance(data, dict):
        raise AppError(422, "IMPORT_ERROR", f"{path.name} must contain a JSON object.")
    return data


def load_topics(path: Path) -> list[TopicCreate]:
    items: list[TopicCreate] = []
    for key, entries in _read_json(path).items():
        if not isinstance(entries, list):
            continue
        for index, raw in enumerate(entries, start=1):
            try:
                items.append(TopicCreate.model_validate(raw))
            except ValidationError as exc:
                raise AppError(
                    422, "IMPORT_ERROR", f"{path.name}: {key} #{index}: {describe_validation_error('en', exc)}"
                ) from None
    return items


def load_translations(path: Path) -> TranslationFile:
    data = _read_json(path)
    language = data.get("language")
    if language not in LANGUAGES or language == DEFAULT_LANGUAGE:
        raise AppError(422, "IMPORT_ERROR", f"{path.name}: 'language' must be one of: "
                       f"{', '.join(code for code in LANGUAGES if code != DEFAULT_LANGUAGE)}.")
    topics: dict[str, TopicTranslationIn] = {}
    for title, raw in (data.get("topics") or {}).items():
        try:
            topics[title] = TopicTranslationIn.model_validate(raw)
        except ValidationError as exc:
            raise AppError(422, "IMPORT_ERROR", f"{path.name}: '{title}': {describe_validation_error('en', exc)}") from None
    labels = {slug: str(name) for slug, name in (data.get("category_labels") or {}).items()}
    return TranslationFile(language, topics, labels)


def import_topics(
    db: Session, actor: User, topics_path: Path, translations_path: Path | None = None
) -> ImportReport:
    """Import everything in the topics file, with translations from the second file when it exists."""
    items = load_topics(topics_path)
    translations = load_translations(translations_path) if translations_path and translations_path.exists() else None
    report = ImportReport()

    labels = {slug: {translations.language: name} for slug, name in translations.category_labels.items()} if translations else None
    report.categories_added = settings_service.add_missing_categories(db, [i.category for i in items], labels)

    existing = {t.title.casefold(): t for t in db.scalars(select(Topic))}
    in_file = {item.title for item in items}
    for item in items:
        translation = translations.topics.get(item.title) if translations else None
        topic = existing.get(item.title.casefold())
        if topic is not None:
            report.skipped.append(item.title)
            if translation and translations.language not in topic.translation_map:
                topic_service.apply_translations(topic, {translations.language: translation.model_dump()})
                db.commit()
                report.translated.append(item.title)
            continue
        data = item.model_copy(update={"translations": {translations.language: translation}}) if translation else item
        existing[item.title.casefold()] = topic_service.create_topic(db, data, actor)
        report.created.append(item.title)
    if translations:
        report.untranslated = [i.title for i in items if i.title not in translations.topics]
        report.unmatched_translations = [title for title in translations.topics if title not in in_file]
    return report
