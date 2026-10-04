from app.services import settings as settings_service


def test_defaults_without_any_stored_settings(login, student):
    body = login(student).get("/api/settings").json()
    assert body["voting_open"] is False
    assert (body["target_session_count"], body["target_core_count"], body["target_optional_count"]) == (8, 4, 4)
    assert "software-engineering" in body["categories"] and len(body["categories"]) == 10


def test_organiser_opens_and_closes_voting(login, organiser, student):
    org = login(organiser)
    assert org.patch("/api/settings", json={"voting_open": True}).json()["voting_open"] is True
    assert login(student).get("/api/settings").json()["voting_open"] is True
    assert org.patch("/api/settings", json={"voting_open": False}).json()["voting_open"] is False


def test_partial_update_keeps_other_settings(login, organiser):
    org = login(organiser)
    org.patch("/api/settings", json={"semester_name": "2026-27 Semester 1", "target_optional_count": 3})
    body = org.patch("/api/settings", json={"voting_open": True}).json()
    assert body["semester_name"] == "2026-27 Semester 1" and body["target_optional_count"] == 3


def test_category_list_is_configurable_and_used_for_validation(login, organiser):
    org = login(organiser)
    topic = {"title": "T", "description": "D", "category": "quantum-computing"}
    assert org.post("/api/topics", json=topic).json()["error"]["code"] == "INVALID_CATEGORY"
    body = org.patch("/api/settings", json={"categories": ["Quantum Computing", "robotics", "robotics"]}).json()
    assert body["categories"] == ["quantum-computing", "robotics"]
    assert org.post("/api/topics", json=topic).status_code == 201
    assert org.post("/api/topics", json={**topic, "category": "open-topic"}).status_code == 422  # no longer listed


def test_a_topic_keeps_a_category_that_was_later_removed(login, organiser, make_topic):
    topic = make_topic(category="robotics")
    org = login(organiser)
    org.patch("/api/settings", json={"categories": ["open-topic"]})
    assert org.patch(f"/api/topics/{topic.id}", json={"title": "Renamed"}).status_code == 200
    assert org.patch(f"/api/topics/{topic.id}", json={"category": "vision"}).status_code == 422


def test_invalid_settings_are_rejected(login, organiser):
    org = login(organiser)
    assert org.patch("/api/settings", json={"categories": []}).json()["error"]["code"] == "INVALID_CATEGORY"
    assert org.patch("/api/settings", json={"categories": ["bad slug!"]}).status_code == 422
    assert org.patch("/api/settings", json={"voting_open": None}).status_code == 422
    assert org.patch("/api/settings", json={"target_session_count": -1}).status_code == 422
    assert org.patch("/api/settings", json={"semester_name": "  "}).status_code == 422


def test_unreadable_stored_value_falls_back_to_default(db):
    from app.models import SystemSetting

    db.add(SystemSetting(key="target_session_count", value="not-a-number"))
    db.commit()
    assert settings_service.get(db, "target_session_count") == 8
