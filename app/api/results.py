from fastapi import APIRouter

from app.deps import CurrentUser, Db, Organiser
from app.models import Topic
from app.schemas import PlanRequest, PlanTopicOut, PlanWarningOut, CandidatePlanOut, ResultOut, VolunteerOut
from app.services import planning, topics
from app.services.results import TopicResult, compute_results, volunteers_by_topic

router = APIRouter(tags=["results and planning"])


def result_out(result: TopicResult) -> ResultOut:
    return ResultOut(
        topic_id=result.topic.id,
        title=result.topic.title,
        topic_type=result.topic.topic_type,
        category=result.topic.category,
        interest_score=result.interest_score,
        interest_breakdown=result.interest_breakdown,
        presenter_counts=result.presenter_counts,
        selection_status=result.selection_status,
    )


def _plan_topic_out(result: TopicResult) -> PlanTopicOut:
    return PlanTopicOut(
        topic_id=result.topic.id,
        title=result.topic.title,
        interest_score=result.interest_score,
        presenter_counts=result.presenter_counts,
        has_presenter=result.has_presenter,
    )


@router.get("/results/topics", response_model=list[ResultOut])
def topic_results(db: Db, user: CurrentUser):
    """Aggregate results only; no individual choices are included."""
    return [result_out(r) for r in compute_results(db, topics.list_pool(db))]


@router.get("/results/topics/{topic_id}/presenters", response_model=list[VolunteerOut])
def topic_presenters(topic_id: int, db: Db, organiser: Organiser):
    topic: Topic = topics.get_topic(db, topic_id, organiser)
    return [
        VolunteerOut(user_id=v.user_id, display_name=v.display_name, presentation_preference=v.presentation_preference)
        for v in volunteers_by_topic(db, [topic.id])[topic.id]
    ]


@router.post("/plans/candidate/generate", response_model=CandidatePlanOut)
def generate_candidate_plan(db: Db, organiser: Organiser, payload: PlanRequest | None = None):
    """Suggest a plan. Nothing is saved: no sessions are created or confirmed."""
    payload = payload or PlanRequest()
    plan = planning.generate_candidate_plan(db, payload.target_total, payload.target_core, payload.target_optional)
    return CandidatePlanOut(
        core_topics=plan.core_topics,
        recommended_optionals=plan.recommended_optionals,
        alternatives=plan.alternative_ids,
        warnings=[PlanWarningOut(topic_id=w.topic_id, type=w.type, message=w.message) for w in plan.warnings],
        ranking=[_plan_topic_out(r) for r in plan.ranked_optionals],
    )
