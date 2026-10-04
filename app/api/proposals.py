from fastapi import APIRouter

from app.deps import CurrentUser, Db, Organiser
from app.enums import ProposalStatus
from app.errors import AppError
from app.schemas import (
    ProposalApprove,
    ProposalCreate,
    ProposalDecisionOut,
    ProposalOut,
    ProposalReject,
    ProposalUpdate,
)
from app.services import proposals
from app.i18n import N_

router = APIRouter(prefix="/topic-proposals", tags=["topic proposals"])


@router.post("", response_model=ProposalOut, status_code=201)
def submit_proposal(payload: ProposalCreate, db: Db, user: CurrentUser):
    return proposals.create_proposal(db, user, payload)


@router.get("", response_model=list[ProposalOut])
def list_proposals(db: Db, user: CurrentUser, status: ProposalStatus | None = None):
    return proposals.list_proposals(db, user, status)


@router.patch("/{proposal_id}", response_model=ProposalOut)
def update_proposal(proposal_id: int, payload: ProposalUpdate, db: Db, user: CurrentUser):
    proposal = proposals.get_proposal(db, proposal_id, user)  # students only reach their own
    if proposal.submitted_by != user.id and not user.is_organiser:
        raise AppError(403, "PERMISSION_DENIED", N_("Only the submitter can edit this proposal."))
    return proposals.update_proposal(db, proposal, payload.model_dump(exclude_unset=True))


@router.post("/{proposal_id}/approve", response_model=ProposalDecisionOut)
def approve_proposal(proposal_id: int, db: Db, organiser: Organiser, payload: ProposalApprove | None = None):
    payload = payload or ProposalApprove()
    proposal = proposals.get_proposal(db, proposal_id, organiser)
    topic = proposals.approve_proposal(db, proposal, organiser, payload.topic_type, payload.category)
    return ProposalDecisionOut(proposal_id=proposal.id, status=proposal.status, topic_id=topic.id)


@router.post("/{proposal_id}/reject", response_model=ProposalDecisionOut)
def reject_proposal(proposal_id: int, db: Db, organiser: Organiser, payload: ProposalReject | None = None):
    payload = payload or ProposalReject()
    proposal = proposals.get_proposal(db, proposal_id, organiser)
    proposals.reject_proposal(db, proposal, organiser, payload.review_note)
    return ProposalDecisionOut(proposal_id=proposal.id, status=proposal.status)


@router.post("/{proposal_id}/archive", response_model=ProposalDecisionOut)
def archive_proposal(proposal_id: int, db: Db, organiser: Organiser):
    proposal = proposals.get_proposal(db, proposal_id, organiser)
    proposals.archive_proposal(db, proposal, organiser)
    return ProposalDecisionOut(proposal_id=proposal.id, status=proposal.status, topic_id=proposal.topic_id)
