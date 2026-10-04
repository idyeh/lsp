"""Aggregate voting results (SRS 4.4 and 4.6).

Preferences of deactivated users are not counted. All aggregates are computed
with one query per topic list, not per topic.
"""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.enums import PresentationPreference, SessionStatus
from app.models import Preference, SeminarSession, Topic, User

INTEREST_LEVELS = (3, 2, 1, 0)
VOLUNTEER_ORDER = (PresentationPreference.lead, PresentationPreference.co_present, PresentationPreference.maybe)

NOT_SELECTED = "not_selected"


def interest_score(n3: int, n2: int, n1: int) -> int:
    """InterestScore = 3*N3 + 2*N2 + N1 (SRS 4.4). 'No preference' (0) adds nothing."""
    return 3 * n3 + 2 * n2 + n1


@dataclass
class TopicResult:
    topic: Topic
    breakdown: dict[int, int] = field(default_factory=lambda: {level: 0 for level in INTEREST_LEVELS})
    presenter_counts: dict[str, int] = field(default_factory=lambda: {p.value: 0 for p in VOLUNTEER_ORDER})
    selection_status: str = NOT_SELECTED  # confirmed | proposed | not_selected

    @property
    def interest_score(self) -> int:
        return interest_score(self.breakdown[3], self.breakdown[2], self.breakdown[1])

    @property
    def has_presenter(self) -> bool:
        """At least one lead or co-presenter volunteer (a 'maybe' alone does not count)."""
        return self.presenter_counts["lead"] + self.presenter_counts["co_present"] > 0

    @property
    def interest_breakdown(self) -> dict[str, int]:
        """The shape used by the API (SRS 8.5): levels 3, 2 and 1 as string keys."""
        return {str(level): self.breakdown[level] for level in (3, 2, 1)}


@dataclass(frozen=True)
class Volunteer:
    user_id: int
    display_name: str
    year_group: str | None
    presentation_preference: str


def compute_results(db: Session, topics: list[Topic]) -> list[TopicResult]:
    """Results for the given topics, best interest score first (ties: lowest topic id first)."""
    results = {topic.id: TopicResult(topic) for topic in topics}
    if not results:
        return []

    counts = db.execute(
        select(
            Preference.topic_id,
            Preference.interest_level,
            Preference.presentation_preference,
            func.count(),
        )
        .join(User, User.id == Preference.user_id)
        .where(Preference.topic_id.in_(results), User.is_active.is_(True))
        .group_by(Preference.topic_id, Preference.interest_level, Preference.presentation_preference)
    )
    for topic_id, level, presentation, count in counts:
        result = results[topic_id]
        result.breakdown[level] += count
        if presentation != PresentationPreference.none:
            result.presenter_counts[presentation.value] += count

    sessions = db.execute(
        select(SeminarSession.topic_id, SeminarSession.status).where(
            SeminarSession.topic_id.in_(results), SeminarSession.status != SessionStatus.cancelled
        )
    )
    for topic_id, status in sessions:
        if status == SessionStatus.proposed:
            if results[topic_id].selection_status == NOT_SELECTED:
                results[topic_id].selection_status = "proposed"
        else:  # confirmed or completed
            results[topic_id].selection_status = "confirmed"

    return sorted(results.values(), key=lambda r: (-r.interest_score, r.topic.id))


def volunteers_by_topic(db: Session, topic_ids: list[int]) -> dict[int, list[Volunteer]]:
    """Active users who volunteered to present (lead, co_present or maybe), best commitment first."""
    rows = db.execute(
        select(Preference.topic_id, User.id, User.display_name, User.year_group, Preference.presentation_preference)
        .join(User, User.id == Preference.user_id)
        .where(
            Preference.topic_id.in_(topic_ids),
            User.is_active.is_(True),
            Preference.presentation_preference != PresentationPreference.none,
        )
    )
    grouped: dict[int, list[Volunteer]] = {topic_id: [] for topic_id in topic_ids}
    for topic_id, user_id, name, year_group, presentation in rows:
        grouped[topic_id].append(Volunteer(user_id, name, year_group, presentation.value))
    rank = {p.value: i for i, p in enumerate(VOLUNTEER_ORDER)}
    for volunteers in grouped.values():
        volunteers.sort(key=lambda v: (rank[v.presentation_preference], v.display_name, v.user_id))
    return grouped
