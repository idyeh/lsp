import pytest
from sqlalchemy import func, select

from app.enums import SessionStatus, TopicStatus
from app.models import AttendanceIntent, SeminarSession
from app.services import sessions as session_service


@pytest.fixture
def org(login, organiser):
    return login(organiser)


def new_session(org, topic, **fields):
    body = {"topic_id": topic.id, "teaching_week": 3, **fields}
    response = org.post("/api/sessions", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- scheduling rules -------------------------------------------------------


def test_organiser_creates_a_session_with_week_and_date(org, make_topic):
    topic = make_topic("Games")
    body = new_session(org, topic, teaching_week=10, session_date="2026-10-28", start_time="14:00:00")
    assert body["status"] == "proposed"
    assert (body["teaching_week"], body["session_date"], body["start_time"]) == (10, "2026-10-28", "14:00:00")
    assert body["topic"]["title"] == "Games"
    assert body["attendance_summary"] == {"attend": 0, "probably_attend": 0, "probably_absent": 0, "not_sure": 0}


def test_unknown_topic_is_not_found(org):
    assert org.post("/api/sessions", json={"topic_id": 999}).json()["error"]["code"] == "TOPIC_NOT_FOUND"


@pytest.mark.parametrize("status", [TopicStatus.draft, TopicStatus.archived])
def test_unapproved_or_archived_topics_cannot_be_scheduled(org, make_topic, status):
    topic = make_topic(status=status)
    response = org.post("/api/sessions", json={"topic_id": topic.id, "teaching_week": 1})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_TOPIC_STATE"


def test_closed_topics_can_be_scheduled(org, make_topic):
    topic = make_topic(status=TopicStatus.closed)
    assert org.post("/api/sessions", json={"topic_id": topic.id, "teaching_week": 1}).status_code == 201


def test_new_session_must_start_proposed_or_confirmed(org, make_topic):
    topic = make_topic()
    for status in ("completed", "cancelled"):
        response = org.post("/api/sessions", json={"topic_id": topic.id, "teaching_week": 1, "status": status})
        assert response.json()["error"]["code"] == "INVALID_SESSION_STATE"


def test_confirming_requires_a_week_or_date(org, make_topic):
    topic = make_topic()
    session = org.post("/api/sessions", json={"topic_id": topic.id}).json()  # proposed without timing is fine
    response = org.patch(f"/api/sessions/{session['id']}", json={"status": "confirmed"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_SESSION_STATE"
    assert org.patch(f"/api/sessions/{session['id']}", json={"status": "confirmed", "session_date": "2026-11-02"}).status_code == 200
    # ... and the timing of a confirmed session cannot be cleared entirely
    cleared = org.patch(f"/api/sessions/{session['id']}", json={"session_date": None})
    assert cleared.status_code == 409


def test_status_transitions(org, make_topic):
    sid = new_session(org, make_topic())["id"]
    url = f"/api/sessions/{sid}"
    assert org.patch(url, json={"status": "completed"}).status_code == 409  # proposed -> completed
    assert org.patch(url, json={"status": "confirmed"}).json()["status"] == "confirmed"
    assert org.patch(url, json={"status": "completed"}).json()["status"] == "completed"
    assert org.patch(url, json={"status": "cancelled"}).status_code == 409  # completed -> cancelled
    assert org.patch(url, json={"status": "confirmed"}).json()["status"] == "confirmed"  # correction
    assert org.patch(url, json={"status": "cancelled"}).json()["status"] == "cancelled"
    assert org.patch(url, json={"status": "proposed"}).json()["status"] == "proposed"  # reinstated


def test_update_dates_notes_and_clearing(org, make_topic):
    sid = new_session(org, make_topic(), session_date="2026-10-28", notes="Room 2")["id"]
    body = org.patch(f"/api/sessions/{sid}", json={"teaching_week": 5, "notes": None, "session_date": None}).json()
    assert (body["teaching_week"], body["session_date"], body["notes"]) == (5, None, None)


def test_replace_the_topic_of_a_proposed_session_clears_its_presenters(org, make_topic, make_user):
    first, second = make_topic("First"), make_topic("Second")
    sid = new_session(org, first)["id"]
    user = make_user("p1")
    org.post(f"/api/sessions/{sid}/presenters", json={"user_id": user.id, "presenter_role": "lead"})
    body = org.patch(f"/api/sessions/{sid}", json={"topic_id": second.id}).json()
    assert body["topic"]["title"] == "Second"
    assert body["presenters"] == []


def test_topic_cannot_be_replaced_once_confirmed_or_with_an_archived_topic(org, make_topic):
    first, archived, other = make_topic("First"), make_topic("Old", status=TopicStatus.archived), make_topic("Other")
    sid = new_session(org, first)["id"]
    assert org.patch(f"/api/sessions/{sid}", json={"topic_id": archived.id}).status_code == 409
    org.patch(f"/api/sessions/{sid}", json={"status": "confirmed"})
    response = org.patch(f"/api/sessions/{sid}", json={"topic_id": other.id})
    assert response.json()["error"]["code"] == "INVALID_SESSION_STATE"


def test_a_failed_update_changes_nothing(org, make_topic):
    sid = new_session(org, make_topic(), notes="keep me")["id"]
    org.patch(f"/api/sessions/{sid}", json={"notes": "changed", "status": "completed"})  # invalid transition
    assert org.get(f"/api/sessions/{sid}").json()["notes"] == "keep me"


def test_session_validation(org, make_topic):
    topic = make_topic()
    assert org.post("/api/sessions", json={"topic_id": topic.id, "teaching_week": 0}).status_code == 422
    assert org.post("/api/sessions", json={"topic_id": topic.id, "session_date": "not-a-date"}).status_code == 422
    assert org.post("/api/sessions", json={"topic_id": topic.id, "status": "nope"}).status_code == 422
    assert org.get("/api/sessions/999").json()["error"]["code"] == "SESSION_NOT_FOUND"


# --- listing ----------------------------------------------------------------


def test_everyone_sees_provisional_and_confirmed_sessions_in_order(login, student, org, make_topic):
    late = new_session(org, make_topic("Late"), teaching_week=9)
    early = new_session(org, make_topic("Early"), teaching_week=2)
    new_session(org, make_topic("Undated"), teaching_week=None)
    org.patch(f"/api/sessions/{early['id']}", json={"status": "confirmed"})

    rows = login(student).get("/api/sessions").json()
    assert [(r["topic"]["title"], r["status"]) for r in rows] == [
        ("Early", "confirmed"),
        ("Late", "proposed"),
        ("Undated", "proposed"),
    ]
    assert rows[0]["topic"]["category"] == "open-topic"  # category is part of the plan view
    assert late["id"] != early["id"]


def test_session_filters(login, student, org, make_topic):
    a = new_session(org, make_topic("A"), session_date="2026-10-01")
    b = new_session(org, make_topic("B"), session_date="2026-11-01")
    org.patch(f"/api/sessions/{b['id']}", json={"status": "confirmed"})
    client = login(student)
    assert [r["id"] for r in client.get("/api/sessions?status=confirmed").json()] == [b["id"]]
    assert [r["id"] for r in client.get("/api/sessions?from=2026-10-15").json()] == [b["id"]]
    assert [r["id"] for r in client.get("/api/sessions?to=2026-10-15").json()] == [a["id"]]
    assert client.get("/api/sessions?from=2026-10-01&to=2026-11-30").json().__len__() == 2


def test_bulk_creation_skips_topics_that_already_have_a_session(db, organiser, make_topic):
    one, two = make_topic("One"), make_topic("Two")
    created, skipped = session_service.create_proposed_sessions(db, organiser, [(one.id, 1), (two.id, None), (one.id, 2)])
    assert [s.topic_id for s in created] == [one.id, two.id]
    assert [t.id for t in skipped] == [one.id]
    created, skipped = session_service.create_proposed_sessions(db, organiser, [(one.id, 1), (two.id, 2)])
    assert created == [] and len(skipped) == 2
    assert db.scalar(select(func.count()).select_from(SeminarSession)) == 2


def test_bulk_creation_is_all_or_nothing(db, organiser, make_topic):
    good, bad = make_topic("Good"), make_topic("Bad", status=TopicStatus.archived)
    with pytest.raises(Exception) as exc:
        session_service.create_proposed_sessions(db, organiser, [(good.id, 1), (bad.id, 2)])
    assert exc.value.code == "INVALID_TOPIC_STATE"
    assert db.scalar(select(func.count()).select_from(SeminarSession)) == 0


def test_cancelled_sessions_do_not_block_rescheduling_a_topic(db, organiser, make_topic):
    topic = make_topic()
    (session,), _ = session_service.create_proposed_sessions(db, organiser, [(topic.id, 1)])
    session_service.update_session(db, session, {"status": SessionStatus.cancelled})
    created, skipped = session_service.create_proposed_sessions(db, organiser, [(topic.id, 2)])
    assert len(created) == 1 and skipped == []


def test_publishing_confirms_only_sessions_with_timing(db, organiser, make_topic):
    dated, undated = make_topic("Dated"), make_topic("Undated")
    session_service.create_proposed_sessions(db, organiser, [(dated.id, 4), (undated.id, None)])
    confirmed, skipped = session_service.confirm_all_proposed(db)
    assert [s.topic.title for s in confirmed] == ["Dated"]
    assert [s.topic.title for s in skipped] == ["Undated"]
    assert skipped[0].status == SessionStatus.proposed


# --- presenters -------------------------------------------------------------


def test_assign_and_remove_presenters(login, student, org, make_topic, make_user):
    sid = new_session(org, make_topic())["id"]
    lead, co = make_user("lead", display_name="Lena Lead"), make_user("co", display_name="Colin Co")
    r1 = org.post(f"/api/sessions/{sid}/presenters", json={"user_id": lead.id, "presenter_role": "lead"})
    assert r1.status_code == 201 and r1.json()["display_name"] == "Lena Lead"
    org.post(f"/api/sessions/{sid}/presenters", json={"user_id": co.id, "presenter_role": "co_presenter"})

    shown = login(student).get(f"/api/sessions/{sid}").json()["presenters"]
    assert [(p["display_name"], p["presenter_role"]) for p in shown] == [("Lena Lead", "lead"), ("Colin Co", "co_presenter")]

    assert org.delete(f"/api/sessions/{sid}/presenters/{lead.id}").status_code == 204
    assert [p["user_id"] for p in org.get(f"/api/sessions/{sid}").json()["presenters"]] == [co.id]
    assert org.delete(f"/api/sessions/{sid}/presenters/{lead.id}").json()["error"]["code"] == "PRESENTER_NOT_FOUND"


def test_a_presenter_cannot_be_assigned_twice(org, make_topic, make_user):
    sid = new_session(org, make_topic())["id"]
    user = make_user("p")
    body = {"user_id": user.id, "presenter_role": "lead"}
    assert org.post(f"/api/sessions/{sid}/presenters", json=body).status_code == 201
    again = org.post(f"/api/sessions/{sid}/presenters", json={**body, "presenter_role": "co_presenter"})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "DUPLICATE_PRESENTER"


def test_presenter_must_be_an_active_existing_user(db, org, make_topic, make_user):
    sid = new_session(org, make_topic())["id"]
    gone = make_user("gone")
    gone.is_active = False
    db.commit()
    for user_id in (gone.id, 9999):
        response = org.post(f"/api/sessions/{sid}/presenters", json={"user_id": user_id, "presenter_role": "lead"})
        assert response.status_code == 404 and response.json()["error"]["code"] == "USER_NOT_FOUND"
    assert org.post(f"/api/sessions/{sid}/presenters", json={"user_id": 1, "presenter_role": "keynote"}).status_code == 422


# --- attendance -------------------------------------------------------------


def confirmed_session(org, make_topic, **fields):
    session = new_session(org, make_topic(), **fields)
    org.patch(f"/api/sessions/{session['id']}", json={"status": "confirmed"})
    return session["id"]


def test_students_record_and_revise_attendance_for_confirmed_sessions(login, db, student, org, make_topic):
    sid = confirmed_session(org, make_topic)
    client = login(student)
    url = f"/api/sessions/{sid}/attendance/me"
    assert client.put(url, json={"intention": "probably_attend"}).json() == {"session_id": sid, "intention": "probably_attend"}
    assert client.put(url, json={"intention": "attend"}).json()["intention"] == "attend"
    assert db.scalar(select(func.count()).select_from(AttendanceIntent)) == 1  # updated in place
    assert client.get(f"/api/sessions/{sid}").json()["my_attendance"] == "attend"


def test_attendance_summary_is_aggregate(login, student, org, make_topic, make_user):
    sid = confirmed_session(org, make_topic)
    intentions = ["attend", "attend", "probably_attend", "probably_absent", "not_sure"]
    for i, intention in enumerate(intentions):
        client = login(make_user(f"a{i}", display_name=f"Person {i}"))
        client.put(f"/api/sessions/{sid}/attendance/me", json={"intention": intention})
    expected = {"attend": 2, "probably_attend": 1, "probably_absent": 1, "not_sure": 1}
    summary = login(student).get(f"/api/sessions/{sid}/attendance/summary")
    assert summary.json() == expected
    assert "Person" not in summary.text
    assert login(student).get(f"/api/sessions/{sid}").json()["attendance_summary"] == expected


def test_only_organisers_see_individual_attendance(login, student, org, make_topic):
    sid = confirmed_session(org, make_topic)
    login(student).put(f"/api/sessions/{sid}/attendance/me", json={"intention": "not_sure"})
    assert login(student).get(f"/api/sessions/{sid}/attendance").status_code == 403
    rows = org.get(f"/api/sessions/{sid}/attendance").json()
    assert rows == [{"user_id": student.id, "display_name": "Sam Student", "intention": "not_sure"}]


def test_attendance_requires_an_existing_session(login, student):
    response = login(student).put("/api/sessions/999/attendance/me", json={"intention": "attend"})
    assert response.status_code == 404 and response.json()["error"]["code"] == "SESSION_NOT_FOUND"


@pytest.mark.parametrize("status", ["proposed", "completed", "cancelled"])
def test_attendance_is_only_accepted_for_confirmed_sessions(login, student, org, make_topic, status):
    sid = confirmed_session(org, make_topic)
    if status == "proposed":
        org.patch(f"/api/sessions/{sid}", json={"status": "proposed"})
    else:
        org.patch(f"/api/sessions/{sid}", json={"status": status})
    response = login(student).put(f"/api/sessions/{sid}/attendance/me", json={"intention": "attend"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_SESSION_STATE"


def test_invalid_attendance_value(login, student, org, make_topic):
    sid = confirmed_session(org, make_topic)
    assert login(student).put(f"/api/sessions/{sid}/attendance/me", json={"intention": "definitely"}).status_code == 422


def test_deactivated_users_are_not_counted_in_attendance(db, login, student, org, make_topic):
    sid = confirmed_session(org, make_topic)
    login(student).put(f"/api/sessions/{sid}/attendance/me", json={"intention": "attend"})
    student.is_active = False
    db.commit()
    assert org.get(f"/api/sessions/{sid}/attendance/summary").json()["attend"] == 0
    assert org.get(f"/api/sessions/{sid}/attendance").json() == []
