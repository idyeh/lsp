"""Organiser pages (SRS 4.11). Each page is a thin form over the same services the API uses."""

from fastapi import APIRouter, Request

from app.deps import Db, Organiser
from app.enums import PresenterRole, ProposalStatus, Role, SessionStatus, TopicStatus
from app.errors import AppError
from app.schemas import (
    ProposalApprove,
    ProposalReject,
    ProposalUpdate,
    SessionCreate,
    SessionUpdate,
    SettingsUpdate,
    TopicCreate,
    TopicUpdate,
    parse_keywords,
)
from app.services import planning, proposals, sessions, topics, users
from app.services import settings as settings_service
from app.services.results import compute_results, volunteers_by_topic
from app.services.sessions import ALLOWED_TRANSITIONS
from app.web.common import Form, action, blank_to_none, flash, form_int, redirect, render, text

router = APIRouter(prefix="/organiser")

STATUS_ORDER = {s: i for i, s in enumerate(TopicStatus)}


def _not_a_number(what: str) -> AppError:
    return AppError(422, "VALIDATION_ERROR", f"{what} must be a number.")


# --- topics -------------------------------------------------------------------


@router.get("/topics")
def topic_list(request: Request, db: Db, organiser: Organiser):
    all_topics = sorted(topics.list_all_topics(db), key=lambda t: (STATUS_ORDER[t.status], t.title.lower(), t.id))
    return render(request, db, "organiser/topics.html", organiser, topics=all_topics)


def _topic_form_page(request, db, organiser, topic=None):
    categories = settings_service.get_categories(db)
    if topic and topic.category not in categories:
        categories = [*categories, topic.category]  # keep a category that was later removed from the list
    return render(request, db, "organiser/topic_form.html", organiser, topic=topic, categories=categories)


@router.get("/topics/new")
def new_topic_page(request: Request, db: Db, organiser: Organiser):
    return _topic_form_page(request, db, organiser)


@router.post("/topics/new")
def create_topic(request: Request, db: Db, organiser: Organiser, form: Form):
    ok = False
    with action(request, "Topic created."):
        data = TopicCreate(**_topic_fields(form))
        topics.create_topic(db, data, organiser)
        ok = True
    return redirect("/organiser/topics" if ok else "/organiser/topics/new")


@router.get("/topics/{topic_id}")
def edit_topic_page(topic_id: int, request: Request, db: Db, organiser: Organiser):
    return _topic_form_page(request, db, organiser, topics.get_topic(db, topic_id, organiser))


@router.post("/topics/{topic_id}")
def update_topic(topic_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    ok = False
    with action(request, "Topic saved."):
        topic = topics.get_topic(db, topic_id, organiser)
        changes = TopicUpdate(**_topic_fields(form)).model_dump(exclude_unset=True)
        topics.update_topic(db, topic, changes)
        ok = True
    return redirect("/organiser/topics" if ok else f"/organiser/topics/{topic_id}")


@router.post("/topics/{topic_id}/archive")
def archive_topic(topic_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Topic archived."):
        topics.update_topic(db, topics.get_topic(db, topic_id, organiser), {"status": TopicStatus.archived})
    return redirect("/organiser/topics")


def _topic_fields(form) -> dict:
    return {
        "title": text(form, "title"),
        "description": text(form, "description"),
        "category": text(form, "category"),
        "topic_type": text(form, "topic_type"),
        "keywords": parse_keywords(text(form, "keywords")),
        "status": text(form, "status"),
    }


# --- proposals ----------------------------------------------------------------


@router.get("/proposals")
def proposal_list(request: Request, db: Db, organiser: Organiser):
    everything = proposals.list_proposals(db, organiser)
    return render(
        request,
        db,
        "organiser/proposals.html",
        organiser,
        pending=[p for p in everything if p.status == ProposalStatus.pending],
        decided=[p for p in everything if p.status != ProposalStatus.pending],
        categories=settings_service.get_categories(db),
    )


@router.post("/proposals/{proposal_id}/edit")
def edit_proposal(proposal_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Proposal updated."):
        proposal = proposals.get_proposal(db, proposal_id, organiser)
        data = ProposalUpdate(
            title=text(form, "title"),
            description=text(form, "description"),
            category=blank_to_none(form, "category"),
            keywords=parse_keywords(text(form, "keywords")),
            rationale=blank_to_none(form, "rationale"),
        )
        proposals.update_proposal(db, proposal, data.model_dump())
    return redirect("/organiser/proposals")


@router.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Proposal approved; the topic is now in the pool."):
        proposal = proposals.get_proposal(db, proposal_id, organiser)
        data = ProposalApprove(topic_type=text(form, "topic_type") or "optional", category=blank_to_none(form, "category"))
        proposals.approve_proposal(db, proposal, organiser, data.topic_type, data.category)
    return redirect("/organiser/proposals")


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Proposal rejected."):
        proposal = proposals.get_proposal(db, proposal_id, organiser)
        data = ProposalReject(review_note=blank_to_none(form, "review_note"))
        proposals.reject_proposal(db, proposal, organiser, data.review_note)
    return redirect("/organiser/proposals")


@router.post("/proposals/{proposal_id}/archive")
def archive_proposal(proposal_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Proposal archived."):
        proposals.archive_proposal(db, proposals.get_proposal(db, proposal_id, organiser), organiser)
    return redirect("/organiser/proposals")


# --- settings -----------------------------------------------------------------


@router.get("/settings")
def settings_page(request: Request, db: Db, organiser: Organiser):
    return render(request, db, "organiser/settings.html", organiser)


@router.post("/settings/voting")
def toggle_voting(request: Request, db: Db, organiser: Organiser, form: Form):
    opening = text(form, "open") == "1"
    with action(request, "Voting is now open." if opening else "Voting is now closed."):
        settings_service.update(db, {"voting_open": opening})
    return redirect(text(form, "return_to") if text(form, "return_to") in ("/organiser/settings", "/organiser/planning") else "/organiser/settings")


@router.post("/settings")
def save_settings(request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Settings saved."):
        categories = [c for line in text(form, "categories").splitlines() for c in line.split(",") if c.strip()]
        changes = SettingsUpdate(
            semester_name=text(form, "semester_name"),
            target_session_count=form_int(form, "target_session_count", _not_a_number("Total sessions")),
            target_core_count=form_int(form, "target_core_count", _not_a_number("Core sessions")),
            target_optional_count=form_int(form, "target_optional_count", _not_a_number("Optional sessions")),
            categories=categories,
        )
        settings_service.update(db, changes.model_dump(exclude_unset=True))
    return redirect("/organiser/settings")


# --- users --------------------------------------------------------------------


@router.get("/users")
def user_list(request: Request, db: Db, organiser: Organiser):
    return render(request, db, "organiser/users.html", organiser, accounts=users.list_users(db))


@router.post("/users")
def create_user(request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Account created. Give the user their password in person or over a private channel."):
        try:
            role = Role(text(form, "role") or "student")
        except ValueError:
            raise AppError(422, "VALIDATION_ERROR", "Unknown role.") from None
        users.create_user(
            db,
            text(form, "username"),
            text(form, "display_name"),
            str(form.get("password", "")),
            role,
            blank_to_none(form, "year_group"),
        )
    return redirect("/organiser/users")


@router.post("/users/{user_id}/password")
def reset_password(user_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Password changed."):
        users.set_password(db, users.get_user(db, user_id), str(form.get("password", "")))
    return redirect("/organiser/users")


@router.post("/users/{user_id}/active")
def set_user_active(user_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    activate = text(form, "active") == "1"
    with action(request, "Account activated." if activate else "Account deactivated."):
        users.set_active(db, users.get_user(db, user_id), activate)
    return redirect("/organiser/users")


@router.post("/users/{user_id}/role")
def set_user_role(user_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Role changed."):
        try:
            role = Role(text(form, "role"))
        except ValueError:
            raise AppError(422, "VALIDATION_ERROR", "Unknown role.") from None
        users.set_role(db, users.get_user(db, user_id), role)
    return redirect("/organiser/users")


# --- planning and sessions ----------------------------------------------------


@router.get("/planning")
def planning_page(
    request: Request,
    db: Db,
    organiser: Organiser,
    target_total: int | None = None,
    target_core: int | None = None,
    target_optional: int | None = None,
):
    plan = planning.generate_candidate_plan(db, target_total, target_core, target_optional)
    config = settings_service.get_all(db)
    return render(
        request,
        db,
        "organiser/planning.html",
        organiser,
        plan=plan,
        warnings_by_topic={w.topic_id: w for w in plan.warnings if w.topic_id is not None},
        general_warnings=[w for w in plan.warnings if w.topic_id is None],
        views=sessions.list_sessions(db, organiser),
        schedulable=[t for t in topics.list_all_topics(db) if t.status in topics.SCHEDULABLE],
        targets={
            "target_total": config["target_session_count"] if target_total is None else target_total,
            "target_core": config["target_core_count"] if target_core is None else target_core,
            "target_optional": config["target_optional_count"] if target_optional is None else target_optional,
        },
    )


@router.post("/planning/sessions")
def create_planned_sessions(request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request):
        items = []
        for raw_id in form.getlist("topic_id"):
            topic_id = int(raw_id) if str(raw_id).isdigit() else None
            if topic_id is None:
                raise AppError(422, "VALIDATION_ERROR", "Unknown topic selected.")
            week = text(form, f"week_{topic_id}")
            items.append((topic_id, form_int(form, f"week_{topic_id}", _not_a_number("Teaching week")) if week else None))
        if not items:
            raise AppError(422, "VALIDATION_ERROR", "Select at least one topic to schedule.")
        created, skipped = sessions.create_proposed_sessions(db, organiser, items)
        if created:
            flash(request, f"Created {len(created)} provisional session(s).", "success")
        if skipped:
            names = ", ".join(t.title for t in skipped)
            flash(request, f"Already scheduled, so skipped: {names}.", "info")
    return redirect("/organiser/planning")


@router.post("/sessions/new")
def create_session(request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Session created."):
        data = SessionCreate(
            topic_id=text(form, "topic_id"),
            teaching_week=blank_to_none(form, "teaching_week"),
            session_date=blank_to_none(form, "session_date"),
            start_time=blank_to_none(form, "start_time"),
            status=text(form, "status") or "proposed",
            notes=blank_to_none(form, "notes"),
        )
        sessions.create_session(db, organiser, data)
    return redirect("/organiser/planning")


@router.post("/sessions/publish")
def publish_plan(request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request):
        confirmed, skipped = sessions.confirm_all_proposed(db)
        if confirmed:
            flash(request, f"Published: {len(confirmed)} session(s) are now confirmed.", "success")
        else:
            flash(request, "There were no provisional sessions to confirm.", "info")
        if skipped:
            names = ", ".join(s.topic.title for s in skipped)
            flash(request, f"Still provisional because they have no week or date: {names}.", "error")
    return redirect("/organiser/planning")


@router.get("/sessions/{session_id}")
def session_page(session_id: int, request: Request, db: Db, organiser: Organiser):
    view = sessions.get_session_view(db, session_id, organiser)
    session = view.session
    assigned = {p.user_id for p in session.presenters}
    volunteers = volunteers_by_topic(db, [session.topic_id])[session.topic_id]
    volunteer_ids = {v.user_id for v in volunteers}
    candidates = [u for u in users.list_users(db) if u.is_active and u.id not in assigned]
    return render(
        request,
        db,
        "organiser/session.html",
        organiser,
        view=view,
        statuses=[s for s in SessionStatus if s == session.status or s in ALLOWED_TRANSITIONS[session.status]],
        volunteers=[v for v in volunteers if v.user_id not in assigned],
        other_users=[u for u in candidates if u.id not in volunteer_ids],
        schedulable=[t for t in topics.list_all_topics(db) if t.status in topics.SCHEDULABLE],
        attendance=sessions.list_attendance(db, session_id),
    )


@router.post("/sessions/{session_id}/update")
def update_session(session_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Session saved."):
        session = sessions.get_session(db, session_id)
        changes = SessionUpdate(
            teaching_week=blank_to_none(form, "teaching_week"),
            session_date=blank_to_none(form, "session_date"),
            start_time=blank_to_none(form, "start_time"),
            status=text(form, "status") or session.status,
            notes=blank_to_none(form, "notes"),
        )
        sessions.update_session(db, session, changes.model_dump(exclude_unset=True))
    return redirect(f"/organiser/sessions/{session_id}")


@router.post("/sessions/{session_id}/topic")
def replace_topic(session_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Topic replaced; the previous presenters were cleared."):
        session = sessions.get_session(db, session_id)
        topic_id = form_int(form, "topic_id", AppError(422, "VALIDATION_ERROR", "Choose a topic."))
        sessions.update_session(db, session, {"topic_id": topic_id})
    return redirect(f"/organiser/sessions/{session_id}")


@router.post("/sessions/{session_id}/presenters")
def add_presenter(session_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Presenter assigned."):
        session = sessions.get_session(db, session_id)
        user_id = form_int(form, "user_id", AppError(422, "VALIDATION_ERROR", "Choose a person."))
        role = text(form, "presenter_role")
        if role not in ("lead", "co_presenter"):
            raise AppError(422, "VALIDATION_ERROR", "Choose a presenter role.")
        sessions.assign_presenter(db, session, user_id, PresenterRole(role))
    return redirect(f"/organiser/sessions/{session_id}")


@router.post("/sessions/{session_id}/presenters/{user_id}/remove")
def remove_presenter(session_id: int, user_id: int, request: Request, db: Db, organiser: Organiser, form: Form):
    with action(request, "Presenter removed."):
        sessions.remove_presenter(db, sessions.get_session(db, session_id), user_id)
    return redirect(f"/organiser/sessions/{session_id}")
