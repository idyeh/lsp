from datetime import date

from fastapi import APIRouter, Query, Response

from app.deps import CurrentUser, Db, Organiser
from app.enums import SessionStatus
from app.schemas import (
    AttendanceEntryOut,
    AttendanceIn,
    AttendanceOut,
    SessionCreate,
    SessionOut,
    SessionPresenterIn,
    SessionPresenterOut,
    SessionUpdate,
    TopicBriefOut,
)
from app.services import sessions
from app.services.sessions import SessionView

router = APIRouter(prefix="/sessions", tags=["sessions"])


def session_out(view: SessionView) -> SessionOut:
    session = view.session
    return SessionOut(
        id=session.id,
        topic=TopicBriefOut.model_validate(session.topic),
        teaching_week=session.teaching_week,
        session_date=session.session_date,
        start_time=session.start_time,
        status=session.status,
        notes=session.notes,
        presenters=[
            SessionPresenterOut(user_id=p.user_id, display_name=p.user.display_name, presenter_role=p.presenter_role)
            for p in session.presenters
        ],
        attendance_summary=view.attendance,
        my_attendance=view.my_intention,
    )


@router.get("", response_model=list[SessionOut])
def list_sessions(
    db: Db,
    user: CurrentUser,
    status: SessionStatus | None = None,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
):
    return [session_out(v) for v in sessions.list_sessions(db, user, status, date_from, date_to)]


@router.post("", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate, db: Db, organiser: Organiser):
    session = sessions.create_session(db, organiser, payload)
    return session_out(sessions.get_session_view(db, session.id, organiser))


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Db, user: CurrentUser):
    return session_out(sessions.get_session_view(db, session_id, user))


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(session_id: int, payload: SessionUpdate, db: Db, organiser: Organiser):
    session = sessions.get_session(db, session_id)
    sessions.update_session(db, session, payload.model_dump(exclude_unset=True))
    return session_out(sessions.get_session_view(db, session_id, organiser))


@router.post("/{session_id}/presenters", response_model=SessionPresenterOut, status_code=201)
def assign_presenter(session_id: int, payload: SessionPresenterIn, db: Db, organiser: Organiser):
    session = sessions.get_session(db, session_id)
    presenter = sessions.assign_presenter(db, session, payload.user_id, payload.presenter_role)
    return SessionPresenterOut(
        user_id=presenter.user_id, display_name=presenter.user.display_name, presenter_role=presenter.presenter_role
    )


@router.delete("/{session_id}/presenters/{user_id}", status_code=204)
def remove_presenter(session_id: int, user_id: int, db: Db, organiser: Organiser):
    sessions.remove_presenter(db, sessions.get_session(db, session_id), user_id)
    return Response(status_code=204)


@router.put("/{session_id}/attendance/me", response_model=AttendanceOut)
def record_attendance(session_id: int, payload: AttendanceIn, db: Db, user: CurrentUser):
    row = sessions.record_attendance(db, user, session_id, payload.intention)
    return AttendanceOut(session_id=row.session_id, intention=row.intention)


@router.get("/{session_id}/attendance/summary", response_model=dict[str, int])
def attendance_summary(session_id: int, db: Db, user: CurrentUser):
    return sessions.attendance_summary(db, session_id)


@router.get("/{session_id}/attendance", response_model=list[AttendanceEntryOut])
def list_attendance(session_id: int, db: Db, organiser: Organiser):
    return [
        AttendanceEntryOut(user_id=row.user_id, display_name=row.user.display_name, intention=row.intention)
        for row in sessions.list_attendance(db, session_id)
    ]
