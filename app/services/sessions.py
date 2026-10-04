"""Sessions, presenters and attendance intentions (SRS 4.8-4.10, 7.5-7.7).

Session lifecycle:

    proposed  -> confirmed | cancelled
    confirmed -> proposed | completed | cancelled
    completed -> confirmed            (to correct a mistake)
    cancelled -> proposed             (to reinstate)

A session needs a teaching week or a date before it can be confirmed or completed.
Attendance intentions are accepted only while a session is confirmed.
"""

from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.enums import AttendanceIntention, PresenterRole, SessionStatus
from app.errors import AppError
from app.models import AttendanceIntent, SeminarSession, SessionPresenter, Topic, User
from app.schemas import MAX_TEACHING_WEEK, MIN_TEACHING_WEEK, SessionCreate
from app.services import topics as topic_service

ALLOWED_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.proposed: {SessionStatus.confirmed, SessionStatus.cancelled},
    SessionStatus.confirmed: {SessionStatus.proposed, SessionStatus.completed, SessionStatus.cancelled},
    SessionStatus.completed: {SessionStatus.confirmed},
    SessionStatus.cancelled: {SessionStatus.proposed},
}
INITIAL_STATUSES = {SessionStatus.proposed, SessionStatus.confirmed}
NEEDS_TIMING = {SessionStatus.confirmed, SessionStatus.completed}


@dataclass
class SessionView:
    """A session together with its aggregate attendance and the viewer's own intention."""

    session: SeminarSession
    attendance: dict[str, int]
    my_intention: AttendanceIntention | None


def _invalid_state(message: str) -> AppError:
    return AppError(409, "INVALID_SESSION_STATE", message)


def _require_timing(status: SessionStatus, week: int | None, session_date: date | None) -> None:
    if status in NEEDS_TIMING and week is None and session_date is None:
        raise _invalid_state(f"A session needs a teaching week or a date before it can be {status.value}.")


def _schedulable_topic(db: Session, topic_id: int) -> Topic:
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise AppError(404, "TOPIC_NOT_FOUND", "Topic not found.")
    if topic.status not in topic_service.SCHEDULABLE:
        raise AppError(409, "INVALID_TOPIC_STATE", f"A {topic.status.value} topic cannot be scheduled.")
    return topic


# --- reading ----------------------------------------------------------------


def get_session(db: Session, session_id: int) -> SeminarSession:
    session = db.get(SeminarSession, session_id)
    if session is None:
        raise AppError(404, "SESSION_NOT_FOUND", "Session not found.")
    return session


def _sort_key(session: SeminarSession):
    """Order by teaching week, then date, then time; unscheduled sessions last."""
    return (
        session.teaching_week is None,
        session.teaching_week or 0,
        session.session_date is None,
        session.session_date or date.max,
        session.start_time or time.max,
        session.id,
    )


def _attendance_counts(db: Session, session_ids: list[int]) -> dict[int, dict[str, int]]:
    counts = {sid: {i.value: 0 for i in AttendanceIntention} for sid in session_ids}
    rows = db.execute(
        select(AttendanceIntent.session_id, AttendanceIntent.intention, func.count())
        .join(User, User.id == AttendanceIntent.user_id)
        .where(AttendanceIntent.session_id.in_(session_ids), User.is_active.is_(True))
        .group_by(AttendanceIntent.session_id, AttendanceIntent.intention)
    )
    for session_id, intention, count in rows:
        counts[session_id][intention.value] = count
    return counts


def build_views(db: Session, sessions: list[SeminarSession], viewer: User) -> list[SessionView]:
    ids = [s.id for s in sessions]
    counts = _attendance_counts(db, ids)
    mine = {
        row.session_id: row.intention
        for row in db.scalars(
            select(AttendanceIntent).where(AttendanceIntent.user_id == viewer.id, AttendanceIntent.session_id.in_(ids))
        )
    }
    return [SessionView(s, counts[s.id], mine.get(s.id)) for s in sessions]


def list_sessions(
    db: Session,
    viewer: User,
    status: SessionStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[SessionView]:
    query = select(SeminarSession).options(
        joinedload(SeminarSession.topic),
        selectinload(SeminarSession.presenters).joinedload(SessionPresenter.user),
    )
    if status:
        query = query.where(SeminarSession.status == status)
    if date_from:
        query = query.where(SeminarSession.session_date >= date_from)
    if date_to:
        query = query.where(SeminarSession.session_date <= date_to)
    sessions = sorted(db.scalars(query).unique(), key=_sort_key)
    return build_views(db, sessions, viewer)


def get_session_view(db: Session, session_id: int, viewer: User) -> SessionView:
    return build_views(db, [get_session(db, session_id)], viewer)[0]


# --- creating and changing sessions ----------------------------------------


def create_session(db: Session, actor: User, data: SessionCreate) -> SeminarSession:
    _schedulable_topic(db, data.topic_id)
    if data.status not in INITIAL_STATUSES:
        raise _invalid_state("A new session must be proposed or confirmed.")
    _require_timing(data.status, data.teaching_week, data.session_date)
    session = SeminarSession(
        topic_id=data.topic_id,
        teaching_week=data.teaching_week,
        session_date=data.session_date,
        start_time=data.start_time,
        status=data.status,
        notes=data.notes,
        created_by=actor.id,
    )
    db.add(session)
    db.commit()
    return session


def create_proposed_sessions(
    db: Session, actor: User, items: list[tuple[int, int | None]]
) -> tuple[list[SeminarSession], list[Topic]]:
    """Create one proposed session per (topic_id, teaching_week) item.

    Topics that already have a non-cancelled session are skipped, so submitting the
    candidate plan twice cannot create duplicates. Nothing is created if any topic is
    not schedulable. Returns (created sessions, skipped topics).
    """
    for _topic_id, week in items:
        if week is not None and not MIN_TEACHING_WEEK <= week <= MAX_TEACHING_WEEK:
            raise AppError(422, "VALIDATION_ERROR", f"Teaching week must be between {MIN_TEACHING_WEEK} and {MAX_TEACHING_WEEK}.")
    topics = [_schedulable_topic(db, topic_id) for topic_id, _week in items]
    already_scheduled = set(
        db.scalars(
            select(SeminarSession.topic_id).where(
                SeminarSession.topic_id.in_([t.id for t in topics]),
                SeminarSession.status != SessionStatus.cancelled,
            )
        )
    )
    created: list[SeminarSession] = []
    skipped: list[Topic] = []
    for topic, (_topic_id, week) in zip(topics, items, strict=True):
        if topic.id in already_scheduled:
            skipped.append(topic)
            continue
        session = SeminarSession(topic_id=topic.id, teaching_week=week, created_by=actor.id)
        db.add(session)
        created.append(session)
        already_scheduled.add(topic.id)  # a topic listed twice in one call is only scheduled once
    db.commit()
    return created, skipped


def update_session(db: Session, session: SeminarSession, changes: dict) -> SeminarSession:
    """Apply a partial update. All checks run before anything is modified."""
    for field in ("topic_id", "status"):
        if field in changes and changes[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"'{field}' cannot be null.")

    new_status = changes.get("status", session.status)
    if new_status != session.status and new_status not in ALLOWED_TRANSITIONS[session.status]:
        raise _invalid_state(f"A {session.status.value} session cannot become {new_status.value}.")

    replacing_topic = "topic_id" in changes and changes["topic_id"] != session.topic_id
    if replacing_topic:
        if session.status != SessionStatus.proposed:
            raise _invalid_state("The topic can only be replaced while the session is proposed.")
        _schedulable_topic(db, changes["topic_id"])

    week = changes.get("teaching_week", session.teaching_week)
    session_date = changes.get("session_date", session.session_date)
    _require_timing(new_status, week, session_date)

    if replacing_topic:
        session.topic_id = changes["topic_id"]
        session.presenters.clear()  # presenters were assigned for the old topic
    for field in ("teaching_week", "session_date", "start_time", "status", "notes"):
        if field in changes:
            setattr(session, field, changes[field])
    db.commit()
    return session


def confirm_all_proposed(db: Session) -> tuple[list[SeminarSession], list[SeminarSession]]:
    """Publish the programme: confirm every proposed session that has a week or date.

    Returns (confirmed, skipped); skipped sessions have no timing yet and stay proposed.
    """
    proposed = db.scalars(select(SeminarSession).where(SeminarSession.status == SessionStatus.proposed))
    confirmed: list[SeminarSession] = []
    skipped: list[SeminarSession] = []
    for session in proposed:
        if session.teaching_week is None and session.session_date is None:
            skipped.append(session)
        else:
            session.status = SessionStatus.confirmed
            confirmed.append(session)
    db.commit()
    return confirmed, skipped


# --- presenters -------------------------------------------------------------


def assign_presenter(db: Session, session: SeminarSession, user_id: int, role: PresenterRole) -> SessionPresenter:
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AppError(404, "USER_NOT_FOUND", "User not found.")
    presenter = SessionPresenter(session_id=session.id, user_id=user.id, presenter_role=role)
    db.add(presenter)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise AppError(409, "DUPLICATE_PRESENTER", "This user is already a presenter for the session.") from None
    return presenter


def remove_presenter(db: Session, session: SeminarSession, user_id: int) -> None:
    presenter = db.scalar(
        select(SessionPresenter).where(SessionPresenter.session_id == session.id, SessionPresenter.user_id == user_id)
    )
    if presenter is None:
        raise AppError(404, "PRESENTER_NOT_FOUND", "This user is not a presenter for the session.")
    db.delete(presenter)
    db.commit()


# --- attendance intentions --------------------------------------------------


def record_attendance(
    db: Session, user: User, session_id: int, intention: AttendanceIntention
) -> AttendanceIntent:
    """Create or update the user's expected attendance; only for confirmed sessions."""
    session = get_session(db, session_id)
    if session.status == SessionStatus.cancelled:
        raise _invalid_state("This session is cancelled and no longer accepts attendance intentions.")
    if session.status != SessionStatus.confirmed:
        raise _invalid_state("Attendance can only be recorded for confirmed sessions.")
    for _attempt in range(2):  # the retry covers a concurrent first insert (unique constraint)
        row = db.scalar(
            select(AttendanceIntent).where(
                AttendanceIntent.session_id == session.id, AttendanceIntent.user_id == user.id
            )
        )
        if row is None:
            row = AttendanceIntent(session_id=session.id, user_id=user.id, intention=intention)
            db.add(row)
        else:
            row.intention = intention
        try:
            db.commit()
            return row
        except IntegrityError:
            db.rollback()
    raise AppError(409, "CONFLICT", "Your attendance was changed at the same time; please try again.")


def attendance_summary(db: Session, session_id: int) -> dict[str, int]:
    session = get_session(db, session_id)
    return _attendance_counts(db, [session.id])[session.id]


def list_attendance(db: Session, session_id: int) -> list[AttendanceIntent]:
    """Individual intentions of active users (organisers only; the caller enforces that)."""
    session = get_session(db, session_id)
    rows = db.scalars(
        select(AttendanceIntent)
        .join(User, User.id == AttendanceIntent.user_id)
        .where(AttendanceIntent.session_id == session.id, User.is_active.is_(True))
        .options(joinedload(AttendanceIntent.user))
        .order_by(User.display_name, User.id)
    )
    return list(rows)
