from fastapi import APIRouter, Query

from app.deps import CurrentUser, Db, Organiser
from app.enums import TopicStatus, TopicType
from app.schemas import TopicCreate, TopicOut, TopicSummaryOut, TopicUpdate
from app.services import topics
from app.services.results import TopicResult, compute_results

router = APIRouter(prefix="/topics", tags=["topics"])


def topic_summary(result: TopicResult) -> TopicSummaryOut:
    return TopicSummaryOut(
        **TopicOut.model_validate(result.topic).model_dump(),
        interest_score=result.interest_score,
        interest_breakdown=result.interest_breakdown,
        presenter_counts=result.presenter_counts,
        selection_status=result.selection_status,
    )


@router.get("", response_model=list[TopicOut])
def list_topics(
    db: Db,
    user: CurrentUser,
    topic_type: TopicType | None = Query(None, alias="type"),
    category: str | None = None,
    status: TopicStatus | None = None,
):
    return topics.list_topics(db, user, topic_type, category, status)


@router.get("/{topic_id}", response_model=TopicSummaryOut)
def get_topic(topic_id: int, db: Db, user: CurrentUser):
    topic = topics.get_topic(db, topic_id, user)
    return topic_summary(compute_results(db, [topic])[0])


@router.post("", response_model=TopicOut, status_code=201)
def create_topic(payload: TopicCreate, db: Db, organiser: Organiser):
    return topics.create_topic(db, payload, organiser)


@router.patch("/{topic_id}", response_model=TopicOut)
def update_topic(topic_id: int, payload: TopicUpdate, db: Db, organiser: Organiser):
    topic = topics.get_topic(db, topic_id, organiser)
    return topics.update_topic(db, topic, payload.model_dump(exclude_unset=True))
