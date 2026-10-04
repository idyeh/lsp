"""SQLAlchemy models, following SRS section 7.

Deviations from the SRS tables (both additive and nullable):
- TopicProposal.topic_id links an approved proposal to the Topic it created.
- The SRS entity "Session" is called SeminarSession in code to avoid clashing
  with the SQLAlchemy and web session classes; its table is still "sessions".

All timestamps are stored and exchanged in UTC.
"""

from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.database import Base
from app.enums import (
    AttendanceIntention,
    PresentationPreference,
    PresenterRole,
    ProposalStatus,
    Role,
    SessionStatus,
    TopicStatus,
    TopicType,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """SQLite drops time zones; store naive UTC and return aware UTC datetimes."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not accepted; use timezone-aware UTC.")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return None if value is None else value.replace(tzinfo=timezone.utc)


def enum_column(enum_cls):
    """Store the enum value as text, guarded by a CHECK constraint."""
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=32,
        values_callable=lambda e: [member.value for member in e],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(enum_column(Role), default=Role.student)
    year_group: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    @property
    def is_organiser(self) -> bool:
        return self.role == Role.organiser


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(64))
    topic_type: Mapped[TopicType] = mapped_column(enum_column(TopicType))
    keywords: Mapped[str | None] = mapped_column(Text)  # comma-separated
    status: Mapped[TopicStatus] = mapped_column(enum_column(TopicStatus), default=TopicStatus.draft)
    submitted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    @property
    def keyword_list(self) -> list[str]:
        return [k for k in (self.keywords or "").split(",") if k]


class TopicProposal(Base):
    __tablename__ = "topic_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    keywords: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus), default=ProposalStatus.pending
    )
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    topic_id: Mapped[int | None] = mapped_column(ForeignKey("topics.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    submitter: Mapped[User] = relationship(foreign_keys=[submitted_by])

    @property
    def keyword_list(self) -> list[str]:
        return [k for k in (self.keywords or "").split(",") if k]


class Preference(Base):
    __tablename__ = "preferences"
    __table_args__ = (
        UniqueConstraint("user_id", "topic_id", name="uq_preference_user_topic"),
        CheckConstraint("interest_level BETWEEN 0 AND 3", name="ck_preference_interest_level"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    interest_level: Mapped[int] = mapped_column(Integer, default=0)
    presentation_preference: Mapped[PresentationPreference] = mapped_column(
        enum_column(PresentationPreference), default=PresentationPreference.none
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship()


class SeminarSession(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    teaching_week: Mapped[int | None] = mapped_column(Integer)
    session_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[time | None] = mapped_column(Time)
    status: Mapped[SessionStatus] = mapped_column(
        enum_column(SessionStatus), default=SessionStatus.proposed
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    topic: Mapped[Topic] = relationship()
    presenters: Mapped[list["SessionPresenter"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="SessionPresenter.id"
    )


class SessionPresenter(Base):
    __tablename__ = "session_presenters"
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_presenter_session_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    presenter_role: Mapped[PresenterRole] = mapped_column(enum_column(PresenterRole))

    session: Mapped[SeminarSession] = relationship(back_populates="presenters")
    user: Mapped[User] = relationship()


class AttendanceIntent(Base):
    __tablename__ = "attendance_intents"
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_attendance_session_user"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    intention: Mapped[AttendanceIntention] = mapped_column(enum_column(AttendanceIntention))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship()


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
