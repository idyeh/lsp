"""Pydantic models: request validation and JSON responses (SRS section 8)."""

from datetime import date, datetime, time
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StringConstraints

from app.enums import (
    AttendanceIntention,
    PresenterRole,
    ProposalStatus,
    Role,
    SessionStatus,
    TopicStatus,
    TopicType,
)

MAX_KEYWORDS = 20
MIN_TEACHING_WEEK = 1
MAX_TEACHING_WEEK = 60
MAX_KEYWORD_LENGTH = 50


def parse_keywords(value: str | list[str] | None) -> list[str]:
    """Normalise keywords: split on commas, trim, drop blanks and case-insensitive duplicates."""
    if value is None:
        return []
    parts = value.split(",") if isinstance(value, str) else [p for item in value for p in item.split(",")]
    seen: set[str] = set()
    keywords: list[str] = []
    for part in parts:
        keyword = part.strip()
        if keyword and keyword.lower() not in seen:
            seen.add(keyword.lower())
            keywords.append(keyword)
    if len(keywords) > MAX_KEYWORDS:
        raise ValueError(f"At most {MAX_KEYWORDS} keywords are allowed.")
    if any(len(k) > MAX_KEYWORD_LENGTH for k in keywords):
        raise ValueError(f"Each keyword may have at most {MAX_KEYWORD_LENGTH} characters.")
    return keywords


Keywords = Annotated[list[str], AfterValidator(parse_keywords)]


def _text(max_length: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


Title = _text(200)
Description = _text(5000)
LongNote = _text(2000)
CategoryText = _text(64)
SessionNotes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
TeachingWeek = Annotated[int, Field(ge=MIN_TEACHING_WEEK, le=MAX_TEACHING_WEEK)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, validate_by_name=True, validate_by_alias=True)


# --- authentication ---------------------------------------------------------


class LoginRequest(BaseModel):
    username: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class UserOut(ORMModel):
    id: int
    username: str
    display_name: str
    role: Role


class LoginResponse(BaseModel):
    user: UserOut


# --- topics -----------------------------------------------------------------


class TopicOut(ORMModel):
    id: int
    title: str
    description: str
    category: str
    topic_type: TopicType
    keywords: list[str] = Field(validation_alias="keyword_list")
    status: TopicStatus


class TopicCreate(BaseModel):
    title: Title
    description: Description
    category: CategoryText
    topic_type: TopicType = TopicType.optional
    keywords: Keywords = []
    status: TopicStatus = TopicStatus.draft


class TopicUpdate(BaseModel):
    """Partial update: only the fields present in the request are changed."""

    title: Title | None = None
    description: Description | None = None
    category: CategoryText | None = None
    topic_type: TopicType | None = None
    keywords: Keywords | None = None
    status: TopicStatus | None = None


class TopicSummaryOut(TopicOut):
    interest_score: int
    interest_breakdown: dict[str, int]
    presenter_counts: dict[str, int]
    selection_status: str


# --- topic proposals --------------------------------------------------------


class ProposalCreate(BaseModel):
    title: Title
    description: Description
    category: CategoryText | None = None
    keywords: Keywords = []
    rationale: LongNote | None = None


class ProposalUpdate(BaseModel):
    title: Title | None = None
    description: Description | None = None
    category: CategoryText | None = None
    keywords: Keywords | None = None
    rationale: LongNote | None = None


class ProposalApprove(BaseModel):
    topic_type: TopicType = TopicType.optional
    category: CategoryText | None = None


class ProposalReject(BaseModel):
    review_note: LongNote | None = None


class ProposalOut(ORMModel):
    id: int
    submitted_by: int
    title: str
    description: str
    category: str | None
    keywords: list[str] = Field(validation_alias="keyword_list")
    rationale: str | None
    status: ProposalStatus
    reviewed_by: int | None
    review_note: str | None
    topic_id: int | None
    created_at: datetime
    reviewed_at: datetime | None


class ProposalDecisionOut(BaseModel):
    proposal_id: int
    status: ProposalStatus
    topic_id: int | None = None


# --- preferences ------------------------------------------------------------


class PreferenceIn(BaseModel):
    # Ranges and enum membership are checked in the service layer so that the
    # API and the web forms report the same INVALID_* error codes.
    interest_level: StrictInt
    presentation_preference: str


class PreferenceOut(ORMModel):
    topic_id: int
    interest_level: int
    presentation_preference: str


# --- results ----------------------------------------------------------------


class ResultOut(BaseModel):
    topic_id: int
    title: str
    topic_type: TopicType
    category: str
    interest_score: int
    interest_breakdown: dict[str, int]
    presenter_counts: dict[str, int]
    selection_status: str


class VolunteerOut(BaseModel):
    user_id: int
    display_name: str
    presentation_preference: str


# --- candidate plan ---------------------------------------------------------


class PlanRequest(BaseModel):
    target_total: Annotated[int, Field(ge=0, le=50)] | None = None
    target_core: Annotated[int, Field(ge=0, le=50)] | None = None
    target_optional: Annotated[int, Field(ge=0, le=50)] | None = None


class PlanWarningOut(BaseModel):
    topic_id: int | None
    type: str
    message: str


class PlanTopicOut(BaseModel):
    topic_id: int
    title: str
    interest_score: int
    presenter_counts: dict[str, int]
    has_presenter: bool


class CandidatePlanOut(BaseModel):
    core_topics: list[int]
    recommended_optionals: list[int]
    alternatives: list[int]
    warnings: list[PlanWarningOut]
    ranking: list[PlanTopicOut]  # every eligible optional topic, best first


# --- sessions ---------------------------------------------------------------


class SessionCreate(BaseModel):
    topic_id: int
    teaching_week: TeachingWeek | None = None
    session_date: date | None = None
    start_time: time | None = None
    status: SessionStatus = SessionStatus.proposed
    notes: SessionNotes | None = None


class SessionUpdate(BaseModel):
    """Partial update. Explicit nulls clear the nullable fields (week, date, time, notes)."""

    topic_id: int | None = None
    teaching_week: TeachingWeek | None = None
    session_date: date | None = None
    start_time: time | None = None
    status: SessionStatus | None = None
    notes: SessionNotes | None = None


class TopicBriefOut(ORMModel):
    id: int
    title: str
    category: str
    topic_type: TopicType


class SessionPresenterIn(BaseModel):
    user_id: int
    presenter_role: PresenterRole


class SessionPresenterOut(BaseModel):
    user_id: int
    display_name: str
    presenter_role: PresenterRole


class SessionOut(BaseModel):
    id: int
    topic: TopicBriefOut
    teaching_week: int | None
    session_date: date | None
    start_time: time | None
    status: SessionStatus
    notes: str | None
    presenters: list[SessionPresenterOut]
    attendance_summary: dict[str, int]
    my_attendance: AttendanceIntention | None


class AttendanceIn(BaseModel):
    intention: AttendanceIntention


class AttendanceOut(BaseModel):
    session_id: int
    intention: AttendanceIntention


class AttendanceEntryOut(BaseModel):
    user_id: int
    display_name: str
    intention: AttendanceIntention


# --- settings ---------------------------------------------------------------


class SettingsOut(BaseModel):
    semester_name: str
    voting_open: bool
    target_session_count: int
    target_core_count: int
    target_optional_count: int
    categories: list[str]


class SettingsUpdate(BaseModel):
    semester_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    voting_open: bool | None = None
    target_session_count: Annotated[int, Field(ge=0, le=50)] | None = None
    target_core_count: Annotated[int, Field(ge=0, le=50)] | None = None
    target_optional_count: Annotated[int, Field(ge=0, le=50)] | None = None
    categories: list[str] | None = None
