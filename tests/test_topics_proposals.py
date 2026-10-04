from sqlalchemy import select

from app.enums import TopicStatus, TopicType
from app.models import Topic

NEW_TOPIC = {
    "title": "Generative Interactive Film",
    "description": "Explore dynamic narrative.",
    "category": "generative-media",
    "topic_type": "optional",
    "keywords": ["narrative", "video", "interaction"],
    "status": "active",
}


# --- topic pool -------------------------------------------------------------


def test_students_see_active_topics_with_type_and_keywords(login, student, make_topic):
    make_topic("Core one", TopicType.core, keywords=["a", "b"])
    make_topic("Optional one")
    topics = login(student).get("/api/topics").json()
    assert {(t["title"], t["topic_type"]) for t in topics} == {("Core one", "core"), ("Optional one", "optional")}
    assert next(t for t in topics if t["title"] == "Core one")["keywords"] == ["a", "b"]


def test_topic_filters(login, student, make_topic):
    make_topic("C1", TopicType.core, category="robotics")
    make_topic("O1", category="robotics")
    make_topic("O2", category="open-topic")
    make_topic("Closed", status=TopicStatus.closed)
    client = login(student)
    assert {t["title"] for t in client.get("/api/topics?type=core").json()} == {"C1"}
    assert {t["title"] for t in client.get("/api/topics?category=robotics").json()} == {"C1", "O1"}
    assert {t["title"] for t in client.get("/api/topics?status=closed").json()} == {"Closed"}
    assert client.get("/api/topics?type=bogus").status_code == 422


def test_students_cannot_see_drafts_or_archived_topics(login, student, organiser, make_topic):
    draft = make_topic("Draft", status=TopicStatus.draft)
    archived = make_topic("Archived", status=TopicStatus.archived)
    client = login(student)
    assert client.get("/api/topics?status=draft").json() == []
    assert client.get("/api/topics?status=archived").json() == []
    assert client.get(f"/api/topics/{draft.id}").status_code == 404
    assert client.get(f"/api/topics/{archived.id}").status_code == 404
    org = login(organiser)
    assert [t["title"] for t in org.get("/api/topics?status=draft").json()] == ["Draft"]
    assert org.get(f"/api/topics/{archived.id}").status_code == 200


def test_topic_detail_includes_public_summary(login, student, make_topic):
    topic = make_topic("Detail")
    body = login(student).get(f"/api/topics/{topic.id}").json()
    assert body["interest_score"] == 0
    assert body["interest_breakdown"] == {"3": 0, "2": 0, "1": 0}
    assert body["presenter_counts"] == {"lead": 0, "co_present": 0, "maybe": 0}
    assert body["selection_status"] == "not_selected"
    assert login(student).get("/api/topics/9999").json()["error"]["code"] == "TOPIC_NOT_FOUND"


def test_organiser_creates_and_edits_topics(login, db, organiser):
    client = login(organiser)
    created = client.post("/api/topics", json=NEW_TOPIC)
    assert created.status_code == 201
    topic_id = created.json()["id"]
    assert created.json()["keywords"] == ["narrative", "video", "interaction"]

    patched = client.patch(f"/api/topics/{topic_id}", json={"topic_type": "core", "keywords": ["x"]})
    assert patched.status_code == 200
    assert patched.json()["topic_type"] == "core"
    assert patched.json()["keywords"] == ["x"]
    assert patched.json()["title"] == NEW_TOPIC["title"]  # untouched fields stay
    stored = db.get(Topic, topic_id)
    assert stored.created_by == organiser.id


def test_new_topics_default_to_draft(login, organiser):
    body = {k: v for k, v in NEW_TOPIC.items() if k != "status"}
    assert login(organiser).post("/api/topics", json=body).json()["status"] == "draft"


def test_archiving_hides_a_topic_from_students(login, student, organiser, make_topic):
    topic = make_topic("Obsolete")
    assert login(organiser).patch(f"/api/topics/{topic.id}", json={"status": "archived"}).json()["status"] == "archived"
    assert login(student).get(f"/api/topics/{topic.id}").status_code == 404


def test_topic_validation(login, organiser, make_topic):
    client = login(organiser)
    assert client.post("/api/topics", json={**NEW_TOPIC, "category": "not-a-category"}).json()["error"]["code"] == "INVALID_CATEGORY"
    assert client.post("/api/topics", json={**NEW_TOPIC, "title": "   "}).status_code == 422
    assert client.post("/api/topics", json={**NEW_TOPIC, "topic_type": "mandatory"}).status_code == 422
    topic = make_topic()
    assert client.patch(f"/api/topics/{topic.id}", json={"title": None}).status_code == 422


def test_keywords_are_normalised(login, organiser):
    body = {**NEW_TOPIC, "keywords": [" nlp ", "NLP", "a,b", ""]}
    assert login(organiser).post("/api/topics", json=body).json()["keywords"] == ["nlp", "a", "b"]


def test_category_names_are_normalised(login, organiser):
    body = {**NEW_TOPIC, "category": " Game Development "}
    assert login(organiser).post("/api/topics", json=body).json()["category"] == "game-development"


# --- proposals --------------------------------------------------------------

PROPOSAL = {
    "title": "AI-generated Music for Games",
    "description": "Explore adaptive music generation.",
    "category": "generative-media",
    "keywords": ["music", "game"],
    "rationale": "Useful for game projects.",
}


def test_student_submits_a_pending_proposal_that_is_not_in_the_pool(login, db, student):
    client = login(student)
    response = client.post("/api/topic-proposals", json=PROPOSAL)
    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    assert response.json()["submitted_by"] == student.id
    assert client.get("/api/topics").json() == []  # not in the voting pool
    assert db.scalars(select(Topic)).first() is None


def test_proposal_needs_only_title_and_description(login, student):
    response = login(student).post("/api/topic-proposals", json={"title": "Bare", "description": "Minimal"})
    assert response.status_code == 201
    assert response.json()["category"] is None


def test_students_see_only_their_own_proposals(login, student, make_user):
    other = make_user("student2")
    login(other).post("/api/topic-proposals", json=PROPOSAL)
    mine = login(student)
    mine.post("/api/topic-proposals", json={**PROPOSAL, "title": "Mine"})
    assert [p["title"] for p in mine.get("/api/topic-proposals").json()] == ["Mine"]


def test_organiser_sees_all_proposals_and_can_filter(login, student, organiser, make_user):
    login(make_user("student2")).post("/api/topic-proposals", json=PROPOSAL)
    login(student).post("/api/topic-proposals", json={**PROPOSAL, "title": "Second"})
    org = login(organiser)
    assert len(org.get("/api/topic-proposals").json()) == 2
    assert len(org.get("/api/topic-proposals?status=pending").json()) == 2
    assert org.get("/api/topic-proposals?status=approved").json() == []


def test_student_cannot_read_or_edit_another_students_proposal(login, student, make_user):
    other_id = login(make_user("student2")).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    response = login(student).patch(f"/api/topic-proposals/{other_id}", json={"title": "Hijack"})
    assert response.status_code == 404


def test_submitter_can_edit_while_pending(login, student):
    client = login(student)
    pid = client.post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    response = client.patch(f"/api/topic-proposals/{pid}", json={"title": "Better title", "keywords": []})
    assert response.status_code == 200
    assert response.json()["title"] == "Better title"
    assert response.json()["keywords"] == []


def test_approval_creates_an_active_topic_and_keeps_the_proposal(login, db, student, organiser):
    pid = login(student).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    response = login(organiser).post(f"/api/topic-proposals/{pid}/approve", json={"topic_type": "optional", "category": "game-development"})
    assert response.status_code == 200
    topic_id = response.json()["topic_id"]
    assert response.json() == {"proposal_id": pid, "status": "approved", "topic_id": topic_id}

    topic = db.get(Topic, topic_id)
    assert (topic.status, topic.topic_type, topic.category) == (TopicStatus.active, TopicType.optional, "game-development")
    assert (topic.submitted_by, topic.created_by) == (student.id, organiser.id)
    assert topic.title == PROPOSAL["title"] and topic.keyword_list == ["music", "game"]

    kept = login(student).get("/api/topic-proposals").json()[0]  # still there, linked, with reviewer
    assert (kept["status"], kept["topic_id"], kept["reviewed_by"]) == ("approved", topic_id, organiser.id)
    assert [t["title"] for t in login(student).get("/api/topics").json()] == [PROPOSAL["title"]]


def test_approval_defaults_to_an_optional_topic_in_the_proposal_category(login, student, organiser):
    pid = login(student).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    topic_id = login(organiser).post(f"/api/topic-proposals/{pid}/approve").json()["topic_id"]
    topic = login(organiser).get(f"/api/topics/{topic_id}").json()
    assert (topic["topic_type"], topic["category"]) == ("optional", "generative-media")


def test_approval_needs_a_category(login, student, organiser):
    pid = login(student).post("/api/topic-proposals", json={"title": "T", "description": "D"}).json()["id"]
    response = login(organiser).post(f"/api/topic-proposals/{pid}/approve", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_CATEGORY"
    ok = login(organiser).post(f"/api/topic-proposals/{pid}/approve", json={"category": "robotics"})
    assert ok.status_code == 200


def test_rejection_records_the_note(login, student, organiser):
    pid = login(student).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    response = login(organiser).post(f"/api/topic-proposals/{pid}/reject", json={"review_note": "Too close to an existing topic."})
    assert response.json() == {"proposal_id": pid, "status": "rejected", "topic_id": None}
    seen = login(student).get("/api/topic-proposals").json()[0]
    assert (seen["status"], seen["review_note"]) == ("rejected", "Too close to an existing topic.")


def test_proposals_cannot_be_edited_or_decided_again_after_a_decision(login, student, organiser):
    client, org = login(student), login(organiser)
    approved = client.post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    rejected = client.post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    org.post(f"/api/topic-proposals/{approved}/approve", json={"category": "robotics"})
    org.post(f"/api/topic-proposals/{rejected}/reject", json={})
    for pid in (approved, rejected):
        for who in (client, org):  # neither the submitter nor an organiser can edit a decided proposal
            response = who.patch(f"/api/topic-proposals/{pid}", json={"title": "Changed"})
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "INVALID_PROPOSAL_STATE"
        assert org.post(f"/api/topic-proposals/{pid}/approve", json={"category": "robotics"}).status_code == 409
        assert org.post(f"/api/topic-proposals/{pid}/reject", json={}).status_code == 409


def test_approving_twice_creates_only_one_topic(login, db, student, organiser):
    pid = login(student).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    org = login(organiser)
    org.post(f"/api/topic-proposals/{pid}/approve", json={"category": "robotics"})
    org.post(f"/api/topic-proposals/{pid}/approve", json={"category": "robotics"})
    assert len(db.scalars(select(Topic)).all()) == 1


def test_archiving_a_proposal_keeps_who_reviewed_it(login, student, organiser):
    pid = login(student).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    org = login(organiser)
    org.post(f"/api/topic-proposals/{pid}/reject", json={"review_note": "No"})
    assert org.post(f"/api/topic-proposals/{pid}/archive").json()["status"] == "archived"
    kept = org.get("/api/topic-proposals").json()[0]
    assert (kept["status"], kept["review_note"], kept["reviewed_by"]) == ("archived", "No", organiser.id)
    assert org.post(f"/api/topic-proposals/{pid}/archive").status_code == 409
    assert org.patch(f"/api/topic-proposals/{pid}", json={"title": "x"}).status_code == 409


def test_organiser_can_edit_a_pending_proposal(login, student, organiser):
    pid = login(student).post("/api/topic-proposals", json=PROPOSAL).json()["id"]
    response = login(organiser).patch(f"/api/topic-proposals/{pid}", json={"title": "Tidied up"})
    assert response.status_code == 200 and response.json()["title"] == "Tidied up"
