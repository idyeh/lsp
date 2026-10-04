from fastapi import APIRouter, Response

from app.deps import CurrentUser, Db
from app.schemas import PreferenceIn, PreferenceOut
from app.services import preferences

router = APIRouter(tags=["preferences"])


@router.get("/preferences/me", response_model=list[PreferenceOut])
def my_preferences(db: Db, user: CurrentUser):
    return preferences.list_my_preferences(db, user)


@router.put("/topics/{topic_id}/preference", response_model=PreferenceOut)
def put_preference(topic_id: int, payload: PreferenceIn, db: Db, user: CurrentUser):
    return preferences.set_preference(
        db, user, topic_id, payload.interest_level, payload.presentation_preference
    )


@router.delete("/topics/{topic_id}/preference", status_code=204)
def delete_preference(topic_id: int, db: Db, user: CurrentUser):
    preferences.reset_preference(db, user, topic_id)
    return Response(status_code=204)
