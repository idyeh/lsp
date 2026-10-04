"""Enumerations shared by the models, schemas and services.

Member names equal their values so the same string is used in the database,
the JSON API and the HTML forms.
"""

from enum import StrEnum


class Role(StrEnum):
    student = "student"
    organiser = "organiser"


class TopicType(StrEnum):
    core = "core"
    optional = "optional"


class TopicStatus(StrEnum):
    draft = "draft"  # created by an organiser, not yet visible to students
    active = "active"  # in the topic pool and open for preferences
    closed = "closed"  # visible and schedulable, but no longer accepts preferences
    archived = "archived"  # obsolete: hidden and cannot be scheduled


class ProposalStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    archived = "archived"


class PresentationPreference(StrEnum):
    none = "none"
    maybe = "maybe"
    co_present = "co_present"
    lead = "lead"


class SessionStatus(StrEnum):
    proposed = "proposed"  # provisional
    confirmed = "confirmed"
    completed = "completed"
    cancelled = "cancelled"


class PresenterRole(StrEnum):
    lead = "lead"
    co_presenter = "co_presenter"


class AttendanceIntention(StrEnum):
    attend = "attend"
    probably_attend = "probably_attend"
    probably_absent = "probably_absent"
    not_sure = "not_sure"
