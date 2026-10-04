from fastapi import APIRouter

from app.deps import CurrentUser, Db, Organiser
from app.schemas import SettingsOut, SettingsUpdate
from app.services import settings as settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsOut)
def get_settings(db: Db, user: CurrentUser):
    return settings_service.get_all(db)


@router.patch("", response_model=SettingsOut)
def update_settings(payload: SettingsUpdate, db: Db, organiser: Organiser):
    return settings_service.update(db, payload.model_dump(exclude_unset=True))
