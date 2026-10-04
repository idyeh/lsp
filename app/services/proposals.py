"""Student topic proposals and their review (SRS 4.3, 7.3)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.enums import ProposalStatus, TopicStatus, TopicType
from app.errors import AppError
from app.models import Topic, TopicProposal, User, utcnow
from app.schemas import ProposalCreate
from app.services import settings as settings_service
from app.services.topics import keywords_to_text


def _not_found() -> AppError:
    return AppError(404, "PROPOSAL_NOT_FOUND", "Proposal not found.")


def _require_pending(proposal: TopicProposal) -> None:
    if proposal.status != ProposalStatus.pending:
        raise AppError(
            409,
            "INVALID_PROPOSAL_STATE",
            f"The proposal is {proposal.status.value}; only pending proposals can be changed.",
        )


def create_proposal(db: Session, user: User, data: ProposalCreate) -> TopicProposal:
    category = settings_service.validate_category(db, data.category) if data.category else None
    proposal = TopicProposal(
        submitted_by=user.id,
        title=data.title,
        description=data.description,
        category=category,
        keywords=keywords_to_text(data.keywords),
        rationale=data.rationale,
    )
    db.add(proposal)
    db.commit()
    return proposal


def list_proposals(db: Session, viewer: User, status: ProposalStatus | None = None) -> list[TopicProposal]:
    """Organisers see every proposal; students only their own."""
    query = select(TopicProposal)
    if not viewer.is_organiser:
        query = query.where(TopicProposal.submitted_by == viewer.id)
    if status:
        query = query.where(TopicProposal.status == status)
    return list(db.scalars(query.order_by(TopicProposal.created_at.desc(), TopicProposal.id.desc())))


def get_proposal(db: Session, proposal_id: int, viewer: User) -> TopicProposal:
    proposal = db.get(TopicProposal, proposal_id)
    if proposal is None or (not viewer.is_organiser and proposal.submitted_by != viewer.id):
        raise _not_found()  # do not reveal other students' proposals
    return proposal


def update_proposal(db: Session, proposal: TopicProposal, changes: dict) -> TopicProposal:
    """Edit a pending proposal. Callers must already have checked who is allowed to edit."""
    _require_pending(proposal)
    for field in ("title", "description"):
        if field in changes and changes[field] is None:
            raise AppError(422, "VALIDATION_ERROR", f"'{field}' cannot be null.")
    if changes.get("category"):
        changes["category"] = settings_service.validate_category(db, changes["category"])
    if "keywords" in changes:
        changes["keywords"] = keywords_to_text(changes["keywords"] or [])
    for field, value in changes.items():
        setattr(proposal, field, value)
    db.commit()
    return proposal


def approve_proposal(
    db: Session,
    proposal: TopicProposal,
    reviewer: User,
    topic_type: TopicType = TopicType.optional,
    category: str | None = None,
) -> Topic:
    """Create an active topic from the proposal; the proposal is kept for traceability."""
    _require_pending(proposal)
    category = category or proposal.category
    if not category:
        raise AppError(422, "INVALID_CATEGORY", "A category is required to approve this proposal.")
    topic = Topic(
        title=proposal.title,
        description=proposal.description,
        category=settings_service.validate_category(db, category),
        topic_type=topic_type,
        keywords=proposal.keywords,
        status=TopicStatus.active,
        submitted_by=proposal.submitted_by,
        created_by=reviewer.id,
    )
    db.add(topic)
    db.flush()
    proposal.status = ProposalStatus.approved
    proposal.topic_id = topic.id
    proposal.reviewed_by = reviewer.id
    proposal.reviewed_at = utcnow()
    db.commit()
    return topic


def reject_proposal(db: Session, proposal: TopicProposal, reviewer: User, note: str | None) -> TopicProposal:
    _require_pending(proposal)
    proposal.status = ProposalStatus.rejected
    proposal.review_note = note
    proposal.reviewed_by = reviewer.id
    proposal.reviewed_at = utcnow()
    db.commit()
    return proposal


def archive_proposal(db: Session, proposal: TopicProposal, reviewer: User) -> TopicProposal:
    if proposal.status == ProposalStatus.archived:
        raise AppError(409, "INVALID_PROPOSAL_STATE", "The proposal is already archived.")
    proposal.status = ProposalStatus.archived
    if proposal.reviewed_by is None:  # keep the original approver/rejecter for traceability
        proposal.reviewed_by = reviewer.id
        proposal.reviewed_at = utcnow()
    db.commit()
    return proposal
