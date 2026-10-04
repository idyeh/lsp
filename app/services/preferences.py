"""Interest and presentation preferences (SRS 4.4, 4.5, 7.4).

A user has at most one Preference row per topic: the database enforces it with a
unique constraint and every write here is an upsert. "No preference" is
represented either by a missing row (after a reset or an untouched topic) or by
a row holding 0 / none; both mean the same thing and are counted identically.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.enums import PresentationPreference, TopicStatus, TopicType
from app.errors import AppError
from app.models import Preference, Topic, User
from app.services import settings as settings_service
from app.services import topics as topic_service


@dataclass(frozen=True)
class PreferenceChange:
    topic_id: int
    interest_level: int
    presentation_preference: str


def _require_voting_open(db: Session) -> None:
    if not settings_service.is_voting_open(db):
        raise AppError(409, "VOTING_CLOSED", "Preferences can no longer be changed because voting is closed.")


def _votable_topic(db: Session, topic_id: int, user: User) -> Topic:
    topic = topic_service.get_topic(db, topic_id, user)
    if topic.status != TopicStatus.active:
        raise AppError(409, "INVALID_TOPIC_STATE", "This topic is not open for preferences.")
    return topic


def _validate(topic: Topic, change: PreferenceChange) -> PresentationPreference:
    level = change.interest_level
    if isinstance(level, bool) or level not in (0, 1, 2, 3):
        raise AppError(422, "INVALID_INTEREST_LEVEL", "Interest level must be 0, 1, 2 or 3.")
    try:
        presentation = PresentationPreference(change.presentation_preference)
    except ValueError:
        allowed = ", ".join(p.value for p in PresentationPreference)
        raise AppError(
            422, "INVALID_PRESENTATION_PREFERENCE", f"Presentation preference must be one of: {allowed}."
        ) from None
    if topic.topic_type == TopicType.core and level != 0:
        raise AppError(
            422, "INVALID_INTEREST_LEVEL", "Interest is only collected for optional topics; core topics always run."
        )
    return presentation


def _apply(db: Session, user_id: int, changes: dict[int, tuple[int, PresentationPreference]], skip_empty: bool):
    """Upsert the given {topic_id: (interest, presentation)} values in one transaction.

    If a concurrent request inserts the same row first, the unique constraint
    fires; the retry then finds that row and updates it instead.
    """
    for _attempt in range(2):
        existing = {
            p.topic_id: p
            for p in db.scalars(
                select(Preference).where(Preference.user_id == user_id, Preference.topic_id.in_(changes))
            )
        }
        for topic_id, (level, presentation) in changes.items():
            row = existing.get(topic_id)
            if row is None:
                if skip_empty and level == 0 and presentation == PresentationPreference.none:
                    continue
                db.add(
                    Preference(
                        user_id=user_id, topic_id=topic_id, interest_level=level, presentation_preference=presentation
                    )
                )
            else:
                row.interest_level = level
                row.presentation_preference = presentation
        try:
            db.commit()
            return
        except IntegrityError:
            db.rollback()
    raise AppError(409, "DUPLICATE_PREFERENCE", "Your preferences were changed at the same time; please try again.")


def set_preference(
    db: Session, user: User, topic_id: int, interest_level: int, presentation_preference: str
) -> Preference:
    """Create or replace the user's preference for one topic (stores 0 / none as given)."""
    _require_voting_open(db)
    topic = _votable_topic(db, topic_id, user)
    change = PreferenceChange(topic_id, interest_level, presentation_preference)
    presentation = _validate(topic, change)
    _apply(db, user.id, {topic.id: (interest_level, presentation)}, skip_empty=False)
    return db.scalar(select(Preference).where(Preference.user_id == user.id, Preference.topic_id == topic.id))


def save_preferences(db: Session, user: User, changes: list[PreferenceChange]) -> None:
    """Save several topics at once, all or nothing (used by the voting form).

    A topic left at 0 / none with no stored preference does not get a row.
    """
    _require_voting_open(db)
    validated: dict[int, tuple[int, PresentationPreference]] = {}
    for change in changes:
        topic = _votable_topic(db, change.topic_id, user)
        validated[topic.id] = (change.interest_level, _validate(topic, change))
    if validated:
        _apply(db, user.id, validated, skip_empty=True)


def reset_preference(db: Session, user: User, topic_id: int) -> None:
    """Remove the user's preference record for a topic (a no-op if there is none)."""
    _require_voting_open(db)
    topic = _votable_topic(db, topic_id, user)
    row = db.scalar(select(Preference).where(Preference.user_id == user.id, Preference.topic_id == topic.id))
    if row is not None:
        db.delete(row)
        db.commit()


def list_my_preferences(db: Session, user: User) -> list[Preference]:
    return list(db.scalars(select(Preference).where(Preference.user_id == user.id).order_by(Preference.topic_id)))
