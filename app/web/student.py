"""Pages for every logged-in user: topic pool and voting, results, proposals, semester plan."""

from fastapi import APIRouter, Request

from app.deps import CurrentUser, Db
from app.enums import AttendanceIntention, TopicStatus, TopicType
from app.errors import AppError
from app.schemas import ProposalCreate, ProposalUpdate, parse_keywords
from app.services import preferences, proposals, sessions, topics
from app.services import settings as settings_service
from app.services.preferences import PreferenceChange
from app.services.results import compute_results, volunteers_by_topic
from app.web.common import Form, action, blank_to_none, form_int, redirect, render, text

router = APIRouter()


@router.get("/topics")
def topic_pool(request: Request, db: Db, user: CurrentUser):
    pool = topics.list_pool(db)
    mine = {p.topic_id: p for p in preferences.list_my_preferences(db, user)}
    return render(
        request,
        db,
        "topics.html",
        user,
        core=[t for t in pool if t.topic_type == TopicType.core],
        optional=[t for t in pool if t.topic_type == TopicType.optional],
        mine=mine,
        voting_open=settings_service.is_voting_open(db),
    )


@router.post("/topics/preferences")
def save_preferences(request: Request, db: Db, user: CurrentUser, form: Form):
    """Save the whole voting form at once. Topics without form fields are left untouched."""
    with action(request, "Your preferences were saved."):
        changes = []
        for topic in topics.list_pool(db):
            if topic.status != TopicStatus.active or f"presenting_{topic.id}" not in form:
                continue
            interest = 0  # core topics have no interest selector
            if f"interest_{topic.id}" in form:
                interest = form_int(
                    form,
                    f"interest_{topic.id}",
                    AppError(422, "INVALID_INTEREST_LEVEL", "Interest level must be 0, 1, 2 or 3."),
                )
            changes.append(PreferenceChange(topic.id, interest, text(form, f"presenting_{topic.id}")))
        preferences.save_preferences(db, user, changes)
    return redirect("/topics")


@router.get("/results")
def results(request: Request, db: Db, user: CurrentUser):
    pool = compute_results(db, topics.list_pool(db))
    volunteers = volunteers_by_topic(db, [r.topic.id for r in pool]) if user.is_organiser else {}
    return render(
        request,
        db,
        "results.html",
        user,
        optional=[r for r in pool if r.topic.topic_type == TopicType.optional],
        core=[r for r in pool if r.topic.topic_type == TopicType.core],
        volunteers=volunteers,
    )


@router.get("/proposals")
def my_proposals(request: Request, db: Db, user: CurrentUser):
    return render(
        request,
        db,
        "proposals.html",
        user,
        proposals=proposals.list_proposals(db, user),
        categories=settings_service.get_categories(db),
    )


@router.post("/proposals")
def submit_proposal(request: Request, db: Db, user: CurrentUser, form: Form):
    with action(request, "Thank you! Your proposal was submitted for review."):
        data = ProposalCreate(
            title=text(form, "title"),
            description=text(form, "description"),
            category=blank_to_none(form, "category"),
            keywords=parse_keywords(text(form, "keywords")),
            rationale=blank_to_none(form, "rationale"),
        )
        proposals.create_proposal(db, user, data)
    return redirect("/proposals")


@router.post("/proposals/{proposal_id}/edit")
def edit_proposal(proposal_id: int, request: Request, db: Db, user: CurrentUser, form: Form):
    with action(request, "Proposal updated."):
        proposal = proposals.get_proposal(db, proposal_id, user)
        data = ProposalUpdate(
            title=text(form, "title"),
            description=text(form, "description"),
            category=blank_to_none(form, "category"),
            keywords=parse_keywords(text(form, "keywords")),
            rationale=blank_to_none(form, "rationale"),
        )
        proposals.update_proposal(db, proposal, data.model_dump())
    return redirect("/proposals")


@router.get("/plan")
def semester_plan(request: Request, db: Db, user: CurrentUser):
    return render(request, db, "plan.html", user, views=sessions.list_sessions(db, user))


@router.post("/sessions/{session_id}/attendance")
def record_attendance(session_id: int, request: Request, db: Db, user: CurrentUser, form: Form):
    with action(request, "Your expected attendance was saved."):
        try:
            intention = AttendanceIntention(text(form, "intention"))
        except ValueError:
            raise AppError(422, "VALIDATION_ERROR", "Please choose one of the attendance options.") from None
        sessions.record_attendance(db, user, session_id, intention)
    return redirect("/plan")
