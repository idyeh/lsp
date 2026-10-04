"""Candidate semester plan (SRS 4.7, 8.6).

The recommendation is advisory and deterministic: the same data always gives the
same plan. It never creates or confirms sessions; organisers do that explicitly.

Rules:
1. Every active core topic is included, whatever its interest score (SRS 8.12 #12).
2. Active optional topics are ranked by, in order:
   a. having at least one `lead` or `co_present` volunteer (those come first);
   b. interest score, highest first;
   c. number of `lead` volunteers, highest first;
   d. topic id, lowest first (a stable final tie-break).
3. The first `target_optional` ranked topics are recommended; the next
   ALTERNATIVES_COUNT are listed as alternatives.
4. Topics in the plan without a lead or any volunteers are flagged with warnings.
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.enums import TopicType
from app.services import settings as settings_service
from app.services import topics as topic_service
from app.services.results import TopicResult, compute_results

ALTERNATIVES_COUNT = 2


@dataclass(frozen=True)
class PlanWarning:
    topic_id: int | None
    type: str
    message: str  # English, with the topic title; this is what the API returns
    params: dict = field(default_factory=dict)  # numbers used by the general warnings, for translated display


@dataclass
class CandidatePlan:
    core: list[TopicResult]
    ranked_optionals: list[TopicResult]  # all eligible optional topics, best first
    target_optional: int
    warnings: list[PlanWarning] = field(default_factory=list)

    @property
    def recommended(self) -> list[TopicResult]:
        return self.ranked_optionals[: self.target_optional]

    @property
    def alternatives(self) -> list[TopicResult]:
        return self.ranked_optionals[self.target_optional : self.target_optional + ALTERNATIVES_COUNT]

    @property
    def core_topics(self) -> list[int]:
        return [r.topic.id for r in self.core]

    @property
    def recommended_optionals(self) -> list[int]:
        return [r.topic.id for r in self.recommended]

    @property
    def alternative_ids(self) -> list[int]:
        return [r.topic.id for r in self.alternatives]


def rank_optionals(optionals: list[TopicResult]) -> list[TopicResult]:
    return sorted(
        optionals,
        key=lambda r: (not r.has_presenter, -r.interest_score, -r.presenter_counts["lead"], r.topic.id),
    )


def _presenter_warnings(result: TopicResult) -> list[PlanWarning]:
    counts = result.presenter_counts
    title = result.topic.title
    if not any(counts.values()):
        return [PlanWarning(result.topic.id, "NO_PRESENTER", f"'{title}' has no presenter volunteers.")]
    if counts["lead"] == 0:
        return [PlanWarning(result.topic.id, "NO_LEAD_PRESENTER", f"'{title}' has no volunteer to lead.")]
    return []


def build_plan(
    core: list[TopicResult],
    optionals: list[TopicResult],
    target_total: int,
    target_core: int,
    target_optional: int,
) -> CandidatePlan:
    """Pure planning logic, separated from the database to keep it easy to test."""
    plan = CandidatePlan(
        core=sorted(core, key=lambda r: r.topic.id),
        ranked_optionals=rank_optionals(optionals),
        target_optional=target_optional,
    )
    for result in plan.core + plan.recommended:
        plan.warnings.extend(_presenter_warnings(result))
    if len(plan.core) != target_core:
        plan.warnings.append(
            PlanWarning(
                None,
                "CORE_COUNT_MISMATCH",
                f"There are {len(plan.core)} active core topics; the target is {target_core}.",
                {"found": len(plan.core), "target": target_core},
            )
        )
    planned = len(plan.core) + len(plan.recommended)
    if planned != target_total:
        plan.warnings.append(
            PlanWarning(
                None,
                "TARGET_TOTAL_MISMATCH",
                f"The plan has {planned} sessions; the target is {target_total}.",
                {"planned": planned, "target": target_total},
            )
        )
    return plan


def generate_candidate_plan(
    db: Session,
    target_total: int | None = None,
    target_core: int | None = None,
    target_optional: int | None = None,
) -> CandidatePlan:
    """Build a plan from the current active topics. Unspecified targets come from the settings."""
    config = settings_service.get_all(db)
    results = compute_results(db, topic_service.list_active(db))
    return build_plan(
        core=[r for r in results if r.topic.topic_type == TopicType.core],
        optionals=[r for r in results if r.topic.topic_type == TopicType.optional],
        target_total=config["target_session_count"] if target_total is None else target_total,
        target_core=config["target_core_count"] if target_core is None else target_core,
        target_optional=config["target_optional_count"] if target_optional is None else target_optional,
    )
