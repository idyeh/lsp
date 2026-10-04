import pytest

from app.enums import PresentationPreference as P
from app.enums import SessionStatus
from app.models import SeminarSession
from app.services.results import compute_results, interest_score


@pytest.mark.parametrize(
    "n3, n2, n1, expected",
    [(0, 0, 0, 0), (1, 0, 0, 3), (0, 1, 0, 2), (0, 0, 1, 1), (6, 3, 2, 26), (2, 2, 2, 12)],
)
def test_interest_score_formula(n3, n2, n1, expected):
    assert interest_score(n3, n2, n1) == expected  # 3*N3 + 2*N2 + N1; the SRS example is 26


def test_scores_and_breakdown_from_stored_preferences(db, make_user, make_topic, make_preference):
    topic = make_topic("Games")
    levels = [3, 3, 2, 1, 1, 0]
    for i, level in enumerate(levels):
        make_preference(make_user(f"u{i}"), topic, interest=level)
    (result,) = compute_results(db, [topic])
    assert result.breakdown == {3: 2, 2: 1, 1: 2, 0: 1}
    assert result.interest_score == 3 * 2 + 2 * 1 + 1 * 2
    assert result.interest_breakdown == {"3": 2, "2": 1, "1": 2}


def test_presenter_counts_are_separate_from_interest(db, make_user, make_topic, make_preference):
    topic = make_topic()
    make_preference(make_user("a"), topic, interest=0, presenting=P.lead)
    make_preference(make_user("b"), topic, interest=3, presenting=P.co_present)
    make_preference(make_user("c"), topic, interest=1, presenting=P.maybe)
    make_preference(make_user("d"), topic, interest=2, presenting=P.none)
    (result,) = compute_results(db, [topic])
    assert result.presenter_counts == {"lead": 1, "co_present": 1, "maybe": 1}
    assert result.interest_score == 3 + 1 + 2


def test_deactivated_users_are_not_counted(db, make_user, make_topic, make_preference):
    topic = make_topic()
    gone = make_user("gone")
    make_preference(gone, topic, interest=3, presenting=P.lead)
    make_preference(make_user("here"), topic, interest=1)
    gone.is_active = False
    db.commit()
    (result,) = compute_results(db, [topic])
    assert result.interest_score == 1
    assert result.presenter_counts["lead"] == 0


def test_results_are_sorted_by_score_then_id(db, make_user, make_topic, make_preference):
    low, high, tie = make_topic("Low"), make_topic("High"), make_topic("Tie")
    user = make_user("u")
    make_preference(user, high, interest=3)
    make_preference(user, tie, interest=1)
    make_preference(user, low, interest=1)
    ordered = [r.topic.title for r in compute_results(db, [low, high, tie])]
    assert ordered == ["High", "Low", "Tie"]  # Low and Tie share a score; Low has the lower id


def test_selection_status_follows_sessions(db, organiser, make_topic):
    free, provisional, confirmed, cancelled = (make_topic(n) for n in ("Free", "Prov", "Conf", "Canc"))
    for topic, status in [
        (provisional, SessionStatus.proposed),
        (confirmed, SessionStatus.confirmed),
        (cancelled, SessionStatus.cancelled),
    ]:
        db.add(SeminarSession(topic_id=topic.id, teaching_week=1, status=status, created_by=organiser.id))
    db.commit()
    got = {r.topic.title: r.selection_status for r in compute_results(db, [free, provisional, confirmed, cancelled])}
    assert got == {"Free": "not_selected", "Prov": "proposed", "Conf": "confirmed", "Canc": "not_selected"}


def test_results_api_shape_and_privacy(login, db, voting_open, student, make_user, make_topic):
    other = make_user("student2", display_name="Secret Other")
    topic = make_topic("Games")
    login(other).put(f"/api/topics/{topic.id}/preference", json={"interest_level": 3, "presentation_preference": "lead"})
    client = login(student)
    client.put(f"/api/topics/{topic.id}/preference", json={"interest_level": 2, "presentation_preference": "maybe"})

    response = client.get("/api/results/topics")
    assert response.status_code == 200
    (row,) = response.json()
    assert row["topic_id"] == topic.id
    assert row["interest_score"] == 5
    assert row["interest_breakdown"] == {"3": 1, "2": 1, "1": 0}
    assert row["presenter_counts"] == {"lead": 1, "co_present": 0, "maybe": 1}
    assert row["selection_status"] == "not_selected"
    # Only aggregates: no names or ids of other students anywhere in the payload.
    assert "Secret Other" not in response.text
    assert str(other.id) not in {str(v) for v in row.values()}


def test_results_exclude_draft_and_archived_topics(login, student, make_topic):
    make_topic("Hidden draft", status="draft")
    make_topic("Hidden archived", status="archived")
    make_topic("Visible")
    titles = [r["title"] for r in login(student).get("/api/results/topics").json()]
    assert titles == ["Visible"]


def test_only_organisers_see_who_volunteered(login, voting_open, student, organiser, make_user, make_topic):
    volunteer = make_user("vol", display_name="Vera Volunteer")
    topic = make_topic()
    login(volunteer).put(
        f"/api/topics/{topic.id}/preference", json={"interest_level": 1, "presentation_preference": "co_present"}
    )
    url = f"/api/results/topics/{topic.id}/presenters"
    assert login(student).get(url).status_code == 403
    response = login(organiser).get(url)
    assert response.json() == [
        {"user_id": volunteer.id, "display_name": "Vera Volunteer", "presentation_preference": "co_present"}
    ]
