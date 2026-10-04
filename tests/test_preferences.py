import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.enums import TopicStatus, TopicType
from app.models import Preference
from app.services import settings as settings_service


def pref_count(db, user, topic):
    return db.scalar(
        select(func.count()).select_from(Preference).where(Preference.user_id == user.id, Preference.topic_id == topic.id)
    )


def test_create_preference(login, voting_open, student, make_topic):
    topic = make_topic("Games")
    client = login(student)
    response = client.put(
        f"/api/topics/{topic.id}/preference", json={"interest_level": 3, "presentation_preference": "co_present"}
    )
    assert response.status_code == 200
    assert response.json() == {"topic_id": topic.id, "interest_level": 3, "presentation_preference": "co_present"}
    assert client.get("/api/preferences/me").json() == [response.json()]


def test_update_preference_in_place_without_duplicates(login, db, voting_open, student, make_topic):
    topic = make_topic()
    client = login(student)
    url = f"/api/topics/{topic.id}/preference"
    client.put(url, json={"interest_level": 1, "presentation_preference": "none"})
    response = client.put(url, json={"interest_level": 3, "presentation_preference": "lead"})
    assert response.json()["interest_level"] == 3
    assert response.json()["presentation_preference"] == "lead"
    assert pref_count(db, student, topic) == 1


def test_interest_and_presentation_are_independent(login, voting_open, student, make_topic):
    topic = make_topic()
    client = login(student)
    url = f"/api/topics/{topic.id}/preference"
    response = client.put(url, json={"interest_level": 0, "presentation_preference": "lead"})
    assert response.json() == {"topic_id": topic.id, "interest_level": 0, "presentation_preference": "lead"}
    response = client.put(url, json={"interest_level": 2, "presentation_preference": "none"})
    assert response.json()["presentation_preference"] == "none"


def test_database_rejects_duplicate_preference_rows(db, student, make_topic, make_preference):
    topic = make_topic()
    make_preference(student, topic, interest=1)
    with pytest.raises(IntegrityError):
        make_preference(student, topic, interest=2)


def test_database_rejects_out_of_range_interest(db, student, make_topic, make_preference):
    with pytest.raises(IntegrityError):
        make_preference(student, make_topic(), interest=4)


@pytest.mark.parametrize("level", [4, -1, 99])
def test_invalid_interest_level(login, voting_open, student, make_topic, level):
    topic = make_topic()
    response = login(student).put(
        f"/api/topics/{topic.id}/preference", json={"interest_level": level, "presentation_preference": "none"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INTEREST_LEVEL"


@pytest.mark.parametrize("value", [True, "3", 2.5])
def test_non_integer_interest_level_is_a_validation_error(login, voting_open, student, make_topic, value):
    topic = make_topic()
    response = login(student).put(
        f"/api/topics/{topic.id}/preference", json={"interest_level": value, "presentation_preference": "none"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_invalid_presentation_preference(login, voting_open, student, make_topic):
    topic = make_topic()
    response = login(student).put(
        f"/api/topics/{topic.id}/preference", json={"interest_level": 1, "presentation_preference": "keynote"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PRESENTATION_PREFERENCE"


def test_voting_closed_blocks_changes(login, db, voting_open, student, make_topic):
    topic = make_topic()
    client = login(student)
    url = f"/api/topics/{topic.id}/preference"
    client.put(url, json={"interest_level": 2, "presentation_preference": "none"})
    settings_service.update(db, {"voting_open": False})

    response = client.put(url, json={"interest_level": 3, "presentation_preference": "none"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VOTING_CLOSED"
    assert client.delete(url).status_code == 409
    assert client.get("/api/preferences/me").json()[0]["interest_level"] == 2  # unchanged


def test_voting_is_closed_by_default(login, student, make_topic):
    topic = make_topic()
    response = login(student).put(
        f"/api/topics/{topic.id}/preference", json={"interest_level": 1, "presentation_preference": "none"}
    )
    assert response.json()["error"]["code"] == "VOTING_CLOSED"


def test_users_only_ever_change_their_own_preference(login, db, voting_open, student, make_user, make_topic):
    other = make_user("student2")
    topic = make_topic()
    login(other).put(f"/api/topics/{topic.id}/preference", json={"interest_level": 3, "presentation_preference": "lead"})
    login(student).put(f"/api/topics/{topic.id}/preference", json={"interest_level": 1, "presentation_preference": "none"})
    rows = {p.user_id: p.interest_level for p in db.scalars(select(Preference))}
    assert rows == {other.id: 3, student.id: 1}
    assert login(student).get("/api/preferences/me").json()[0]["interest_level"] == 1


def test_reset_deletes_the_record(login, db, voting_open, student, make_topic):
    topic = make_topic()
    client = login(student)
    url = f"/api/topics/{topic.id}/preference"
    client.put(url, json={"interest_level": 3, "presentation_preference": "lead"})
    assert client.delete(url).status_code == 204
    assert pref_count(db, student, topic) == 0
    assert client.get("/api/preferences/me").json() == []
    assert client.delete(url).status_code == 204  # resetting again is harmless


def test_preferences_only_for_active_topics(login, voting_open, student, make_topic):
    client = login(student)
    body = {"interest_level": 1, "presentation_preference": "none"}
    draft = make_topic(status=TopicStatus.draft)
    closed = make_topic(status=TopicStatus.closed)
    assert client.put(f"/api/topics/{draft.id}/preference", json=body).status_code == 404
    response = client.put(f"/api/topics/{closed.id}/preference", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_TOPIC_STATE"
    assert client.put("/api/topics/9999/preference", json=body).json()["error"]["code"] == "TOPIC_NOT_FOUND"


def test_core_topics_collect_presentation_but_not_interest(login, voting_open, student, make_topic):
    core = make_topic("Core", topic_type=TopicType.core)
    client = login(student)
    url = f"/api/topics/{core.id}/preference"
    rejected = client.put(url, json={"interest_level": 2, "presentation_preference": "lead"})
    assert rejected.json()["error"]["code"] == "INVALID_INTEREST_LEVEL"
    accepted = client.put(url, json={"interest_level": 0, "presentation_preference": "lead"})
    assert accepted.status_code == 200
