"""Topic pool management (SRS 4.2)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import TopicStatus, TopicType
from app.errors import AppError
from app.models import Topic, TopicTranslation, User
from app.schemas import TopicCreate
from app.services import settings as settings_service
from app.i18n import N_

# Statuses students may see. Drafts and archived topics are organiser-only.
STUDENT_VISIBLE = (TopicStatus.active, TopicStatus.closed)
# Statuses a topic must have to be scheduled as a session (SRS 8.12 rules 6 and 7).
SCHEDULABLE = (TopicStatus.active, TopicStatus.closed)


def _not_found() -> AppError:
    return AppError(404, "TOPIC_NOT_FOUND", N_("Topic not found."))


def keywords_to_text(keywords: list[str]) -> str | None:
    return ",".join(keywords) or None


def list_topics(
    db: Session,
    viewer: User,
    topic_type: TopicType | None = None,
    category: str | None = None,
    status: TopicStatus | None = None,
) -> list[Topic]:
    """Topics matching the filters; `status` defaults to active. Students cannot see drafts or archived."""
    status = status or TopicStatus.active
    if not viewer.is_organiser and status not in STUDENT_VISIBLE:
        return []
    query = select(Topic).where(Topic.status == status)
    if topic_type:
        query = query.where(Topic.topic_type == topic_type)
    if category:
        query = query.where(Topic.category == settings_service.normalise_category(category))
    return list(db.scalars(query.order_by(Topic.topic_type, Topic.title, Topic.id)))


def list_active(db: Session) -> list[Topic]:
    """All active topics, lowest id first (the input to planning)."""
    return list(db.scalars(select(Topic).where(Topic.status == TopicStatus.active).order_by(Topic.id)))


def list_pool(db: Session) -> list[Topic]:
    """Active and closed topics: what students see in the topic pool."""
    query = select(Topic).where(Topic.status.in_(STUDENT_VISIBLE))
    return list(db.scalars(query.order_by(Topic.topic_type, Topic.title, Topic.id)))


def list_all_topics(db: Session) -> list[Topic]:
    """Every topic in every status, for organisers."""
    return list(db.scalars(select(Topic).order_by(Topic.status, Topic.topic_type, Topic.title, Topic.id)))


def get_topic(db: Session, topic_id: int, viewer: User) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None or (not viewer.is_organiser and topic.status not in STUDENT_VISIBLE):
        raise _not_found()
    return topic


def apply_translations(topic: Topic, translations: dict[str, dict | None]) -> None:
    """Add, replace or (when the value is None) remove translations. Languages not listed are untouched."""
    existing = topic.translation_map
    for language, text in translations.items():
        current = existing.get(language)
        if text is None:
            if current is not None:
                topic.translations.remove(current)
        elif current is None:
            topic.translations.append(
                TopicTranslation(
                    language=language,
                    title=text["title"],
                    description=text["description"],
                    keywords=keywords_to_text(text.get("keywords") or []),
                )
            )
        else:
            current.title = text["title"]
            current.description = text["description"]
            current.keywords = keywords_to_text(text.get("keywords") or [])


def create_topic(db: Session, data: TopicCreate, actor: User, submitted_by: int | None = None) -> Topic:
    topic = Topic(
        title=data.title,
        description=data.description,
        category=settings_service.validate_category(db, data.category),
        topic_type=data.topic_type,
        keywords=keywords_to_text(data.keywords),
        status=data.status,
        submitted_by=submitted_by,
        created_by=actor.id,
    )
    apply_translations(topic, {lang: t.model_dump() for lang, t in data.translations.items() if t is not None})
    db.add(topic)
    db.commit()
    return topic


def update_topic(db: Session, topic: Topic, changes: dict) -> Topic:
    """Apply a partial update (`changes` holds only the fields the client sent)."""
    for field in ("title", "description", "category", "topic_type", "status"):
        if field in changes and changes[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"'{field}' cannot be null.")
    if "category" in changes and changes["category"] != topic.category:
        # Only re-validated when it changes, so a topic keeps a category later removed from the list.
        changes["category"] = settings_service.validate_category(db, changes["category"])
    if "keywords" in changes:
        changes["keywords"] = keywords_to_text(changes["keywords"] or [])
    apply_translations(topic, changes.pop("translations", None) or {})
    for field, value in changes.items():
        setattr(topic, field, value)
    db.commit()
    return topic
