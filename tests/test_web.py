"""The server-rendered UI, exercised the way a browser would: pages, forms, CSRF tokens, redirects."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.enums import SessionStatus, TopicType
from app.models import Preference, SeminarSession, Topic, User
from tests.conftest import PASSWORD

CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def visible_text(html):
    """The page text with tags removed and whitespace collapsed, so tests do not depend on markup."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class Browser:
    """A cookie-keeping client that fills in the CSRF token the way a real page would."""

    def __init__(self, app):
        self.client = TestClient(app, follow_redirects=False)

    def token(self, page="/login"):
        response = self.client.get(page)
        assert response.status_code == 200, (page, response.status_code)
        return CSRF.search(response.text).group(1)

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, page=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.token(page or "/topics" if self.logged_in else "/login"))
        return self.client.post(url, data=data, **kw)

    def follow(self, response):
        assert response.status_code == 303, response.status_code
        return self.client.get(response.headers["location"])

    logged_in = False

    def login(self, user, password=PASSWORD):
        response = self.post("/login", {"username": user.username, "password": password})
        self.logged_in = response.status_code == 303 and response.headers["location"] == "/"
        return response


@pytest.fixture
def browser(app):
    created = []

    def _browser(user=None):
        b = Browser(app)
        created.append(b)
        if user:
            assert b.login(user).headers["location"] == "/"
        return b

    yield _browser
    for b in created:
        b.client.close()


# --- login and access control -----------------------------------------------


def test_login_page_and_successful_login(browser, student):
    b = browser()
    assert "Log in" in b.get("/login").text
    response = b.login(student)
    assert (response.status_code, response.headers["location"]) == (303, "/")
    page = b.follow(b.get("/"))
    assert "Topics &amp; voting" in page.text and "Sam Student" in page.text


def test_wrong_password_shows_a_message_and_stays_logged_out(browser, student):
    b = browser()
    response = b.login(student, "wrong-password")
    assert response.headers["location"] == "/login"
    assert "Invalid username or password" in b.follow(response).text
    assert b.get("/topics").headers["location"] == "/login"


def test_login_without_a_csrf_token_is_refused(app, student):
    with TestClient(app, follow_redirects=False) as client:
        client.get("/login")
        response = client.post("/login", data={"username": "student1", "password": PASSWORD})
    assert response.status_code == 403


def test_login_with_a_wrong_csrf_token_is_refused(app, student):
    with TestClient(app, follow_redirects=False) as client:
        client.get("/login")
        response = client.post("/login", data={"username": "student1", "password": PASSWORD, "csrf_token": "forged"})
    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/topics", "/results", "/proposals", "/plan", "/organiser/topics", "/organiser/planning"])
def test_pages_redirect_anonymous_visitors_to_login(browser, path):
    response = browser().get(path)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


@pytest.mark.parametrize(
    "path",
    ["/organiser/topics", "/organiser/topics/new", "/organiser/proposals", "/organiser/settings", "/organiser/users", "/organiser/planning"],
)
def test_students_cannot_open_organiser_pages(browser, student, path):
    assert browser(student).get(path).status_code == 403


def test_students_cannot_post_organiser_actions(browser, student, make_topic):
    b = browser(student)
    topic = make_topic("Existing")
    assert b.post("/organiser/settings/voting", {"open": "1"}).status_code == 403
    assert b.post("/organiser/topics/new", {"title": "x", "description": "y", "category": "open-topic"}).status_code == 403
    assert b.post(f"/organiser/topics/{topic.id}/archive").status_code == 403
    assert b.post("/organiser/users", {"username": "evil", "display_name": "E", "password": "password-123"}).status_code == 403


def test_logout_requires_the_token_and_ends_the_session(browser, student):
    b = browser(student)
    assert b.client.post("/logout", data={}).status_code == 403
    assert b.post("/logout").headers["location"] == "/login"
    assert b.get("/topics").headers["location"] == "/login"


def test_unknown_page_gets_an_html_error_page(browser, student):
    response = browser(student).get("/no-such-page")
    assert response.status_code == 404
    assert "Page not found" in response.text and response.headers["content-type"].startswith("text/html")


def test_stylesheet_is_served(browser):
    assert browser().get("/static/style.css").status_code == 200


def test_user_content_is_escaped(browser, student, make_topic):
    make_topic("<script>alert(1)</script>")
    html = browser(student).get("/topics").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


# --- topics and voting ------------------------------------------------------


def test_topic_pool_distinguishes_core_and_optional(browser, student, make_topic):
    make_topic("Core topic", TopicType.core)
    make_topic("Optional topic")
    html = browser(student).get("/topics").text
    assert "Core topics" in html and "Optional topics" in html
    assert 'class="badge core"' in html and 'class="badge optional"' in html


def test_voting_form_saves_both_preferences(browser, db, voting_open, student, make_topic):
    core = make_topic("Core", TopicType.core)
    opt = make_topic("Optional")
    b = browser(student)
    response = b.post("/topics/preferences", {f"presenting_{core.id}": "lead", f"interest_{opt.id}": "3", f"presenting_{opt.id}": "maybe"})
    assert "Your preferences were saved" in b.follow(response).text
    rows = {p.topic_id: (p.interest_level, p.presentation_preference.value) for p in db.scalars(select(Preference))}
    assert rows == {core.id: (0, "lead"), opt.id: (3, "maybe")}

    page = b.get("/topics").text  # the form shows the saved choices
    assert f'<option value="3" selected>' in page and '<option value="lead" selected>' in page


def test_resubmitting_the_form_updates_instead_of_duplicating(browser, db, voting_open, student, make_topic):
    opt = make_topic("Optional")
    b = browser(student)
    for level in ("1", "2", "3"):
        b.post("/topics/preferences", {f"interest_{opt.id}": level, f"presenting_{opt.id}": "none"})
    assert db.scalar(select(func.count()).select_from(Preference)) == 1
    assert db.scalar(select(Preference.interest_level)) == 3


def test_untouched_topics_do_not_create_preference_rows(browser, db, voting_open, student, make_topic):
    topics = [make_topic(f"T{i}") for i in range(3)]
    form = {}
    for t in topics:
        form |= {f"interest_{t.id}": "0", f"presenting_{t.id}": "none"}
    browser(student).post("/topics/preferences", form)
    assert db.scalar(select(func.count()).select_from(Preference)) == 0


def test_voting_form_is_rejected_when_voting_is_closed(browser, db, student, make_topic):
    opt = make_topic("Optional")
    b = browser(student)
    response = b.post("/topics/preferences", {f"interest_{opt.id}": "3", f"presenting_{opt.id}": "lead"})
    assert "voting is closed" in b.follow(response).text
    assert db.scalar(select(func.count()).select_from(Preference)) == 0
    assert "disabled" in b.get("/topics").text


def test_tampered_interest_value_is_reported_not_stored(browser, db, voting_open, student, make_topic):
    opt = make_topic("Optional")
    b = browser(student)
    for bad in ("9", "abc"):
        response = b.post("/topics/preferences", {f"interest_{opt.id}": bad, f"presenting_{opt.id}": "none"})
        assert "Interest level must be" in b.follow(response).text
    assert db.scalar(select(func.count()).select_from(Preference)) == 0


def test_a_bad_row_rejects_the_whole_form(browser, db, voting_open, student, make_topic):
    good, other = make_topic("Good"), make_topic("Other")
    b = browser(student)
    b.post("/topics/preferences", {f"interest_{good.id}": "2", f"presenting_{good.id}": "none", f"interest_{other.id}": "7", f"presenting_{other.id}": "none"})
    assert db.scalar(select(func.count()).select_from(Preference)) == 0


def test_results_page_shows_totals_but_not_names(browser, voting_open, student, make_user, make_topic):
    other = make_user("student2", display_name="Private Person")
    topic = make_topic("Games")
    browser(other).post("/topics/preferences", {f"interest_{topic.id}": "3", f"presenting_{topic.id}": "lead"})
    page = browser(student).get("/results").text
    assert "Games" in page and "Private Person" not in page
    assert "<strong>3</strong>" in page  # interest score


def test_organiser_results_show_volunteer_names(browser, voting_open, organiser, make_user, make_topic):
    volunteer = make_user("student2", display_name="Vera Volunteer")
    topic = make_topic("Games")
    browser(volunteer).post("/topics/preferences", {f"interest_{topic.id}": "1", f"presenting_{topic.id}": "co_present"})
    assert "Vera Volunteer" in browser(organiser).get("/results").text


# --- proposals --------------------------------------------------------------


def test_proposal_to_topic_end_to_end(browser, db, student, organiser):
    s, o = browser(student), browser(organiser)
    response = s.post("/proposals", {"title": "Music for games", "description": "Adaptive scores", "category": "generative-media", "keywords": "music, games", "rationale": "Fun"})
    assert "submitted for review" in s.follow(response).text
    assert db.scalar(select(func.count()).select_from(Topic)) == 0  # not in the pool yet
    assert "Music for games" not in s.get("/topics").text

    assert "Music for games" in o.get("/organiser/proposals").text
    proposal_id = db.scalar(select(Topic.id)) or 1
    response = o.post(f"/organiser/proposals/{proposal_id}/approve", {"topic_type": "core", "category": "robotics"})
    assert "now in the pool" in o.follow(response).text

    topic = db.scalar(select(Topic))
    assert (topic.title, topic.topic_type, topic.category) == ("Music for games", TopicType.core, "robotics")
    assert "Music for games" in s.get("/topics").text


def test_student_can_edit_a_pending_proposal_but_not_see_others(browser, student, make_user):
    other = browser(make_user("student2"))
    other.post("/proposals", {"title": "Hidden idea", "description": "d"})
    b = browser(student)
    b.post("/proposals", {"title": "Mine", "description": "first version"})
    page = b.get("/proposals").text
    assert "Mine" in page and "Hidden idea" not in page
    response = b.post("/proposals/2/edit", {"title": "Mine v2", "description": "second", "category": "", "keywords": "", "rationale": ""})
    assert "Proposal updated" in b.follow(response).text
    hijack = b.post("/proposals/1/edit", {"title": "Hijack", "description": "x"})
    assert "Proposal not found" in b.follow(hijack).text  # same answer as for a missing proposal
    assert "Hijack" not in other.get("/proposals").text and "Hidden idea" in other.get("/proposals").text


def test_proposal_rejection_note_reaches_the_student(browser, student, organiser):
    s, o = browser(student), browser(organiser)
    s.post("/proposals", {"title": "Too similar", "description": "d"})
    o.post("/organiser/proposals/1/reject", {"review_note": "Overlaps an existing topic"})
    assert "Overlaps an existing topic" in s.get("/proposals").text


# --- organiser management ---------------------------------------------------


def test_organiser_pages_render(browser, organiser, make_topic):
    topic = make_topic("Some topic")
    b = browser(organiser)
    for path in ("/organiser/topics", "/organiser/topics/new", f"/organiser/topics/{topic.id}", "/organiser/proposals", "/organiser/settings", "/organiser/users", "/organiser/planning"):
        assert b.get(path).status_code == 200, path


def test_organiser_creates_edits_and_archives_a_topic(browser, db, organiser, student):
    o, s = browser(organiser), browser(student)
    form = {"title": "New idea", "description": "About it", "category": "robotics", "topic_type": "optional", "status": "active", "keywords": "a, b"}
    assert "Topic created" in o.follow(o.post("/organiser/topics/new", form)).text
    topic = db.scalar(select(Topic))
    assert "New idea" in s.get("/topics").text

    assert "Topic saved" in o.follow(o.post(f"/organiser/topics/{topic.id}", {**form, "title": "Renamed", "topic_type": "core"})).text
    db.refresh(topic)
    assert (topic.title, topic.topic_type) == ("Renamed", TopicType.core)

    o.post(f"/organiser/topics/{topic.id}/archive")
    assert "Renamed" not in s.get("/topics").text


def test_invalid_topic_form_shows_an_error_and_creates_nothing(browser, db, organiser):
    o = browser(organiser)
    response = o.post("/organiser/topics/new", {"title": "", "description": "d", "category": "robotics", "topic_type": "optional", "status": "active"})
    assert "Title" in o.follow(response).text
    response = o.post("/organiser/topics/new", {"title": "x", "description": "d", "category": "nonsense", "topic_type": "optional", "status": "active"})
    assert "not one of the configured categories" in o.follow(response).text
    assert db.scalar(select(func.count()).select_from(Topic)) == 0


def test_organiser_opens_and_closes_voting(browser, organiser, student):
    o = browser(organiser)
    assert "Voting is now open" in o.follow(o.post("/organiser/settings/voting", {"open": "1"})).text
    assert "Voting is <strong>open</strong>" in browser(student).get("/topics").text
    assert "Voting is now closed" in o.follow(o.post("/organiser/settings/voting", {"open": "0"})).text


def test_organiser_edits_settings_and_categories(browser, db, organiser):
    o = browser(organiser)
    form = {"semester_name": "Autumn 2026", "target_session_count": "6", "target_core_count": "3", "target_optional_count": "3", "categories": "robotics\nvision, open-topic"}
    assert "Settings saved" in o.follow(o.post("/organiser/settings", form)).text
    from app.services import settings as settings_service

    assert settings_service.get(db, "categories") == ["robotics", "vision", "open-topic"]
    assert "Autumn 2026" in o.get("/organiser/settings").text
    assert "must be a number" in o.follow(o.post("/organiser/settings", {**form, "target_core_count": "x"})).text


def test_organiser_manages_users(browser, db, organiser):
    o = browser(organiser)
    created = o.post("/organiser/users", {"username": "NewStudent", "display_name": "New Student", "password": "a-long-password", "role": "student", "year_group": "UG2"})
    assert "Account created" in o.follow(created).text
    new = db.scalar(select(User).where(User.username == "newstudent"))
    assert new and new.year_group == "UG2"
    assert browser().login(new, "a-long-password").headers["location"] == "/"

    short = o.post("/organiser/users", {"username": "weak", "display_name": "W", "password": "short", "role": "student"})
    assert "Password must be" in o.follow(short).text
    dup = o.post("/organiser/users", {"username": "newstudent", "display_name": "Again", "password": "a-long-password", "role": "student"})
    assert "already taken" in o.follow(dup).text

    o.post(f"/organiser/users/{new.id}/active", {"active": "0"})
    assert browser().login(new, "a-long-password").headers["location"] == "/login"
    o.post(f"/organiser/users/{new.id}/password", {"password": "another-long-password"})
    o.post(f"/organiser/users/{new.id}/active", {"active": "1"})
    assert browser().login(new, "another-long-password").headers["location"] == "/"


def test_the_last_organiser_cannot_be_locked_out(browser, organiser):
    o = browser(organiser)
    assert "last active organiser" in o.follow(o.post(f"/organiser/users/{organiser.id}/active", {"active": "0"})).text
    assert "last active organiser" in o.follow(o.post(f"/organiser/users/{organiser.id}/role", {"role": "student"})).text
    assert o.get("/organiser/users").status_code == 200


# --- planning, sessions and attendance --------------------------------------


def test_planning_to_attendance_end_to_end(browser, db, voting_open, organiser, student, make_user, make_topic):
    core = make_topic("Core one", TopicType.core)
    popular, modest = make_topic("Popular"), make_topic("Modest")
    volunteer = make_user("vol", display_name="Vera Volunteer")
    browser(volunteer).post("/topics/preferences", {f"interest_{popular.id}": "3", f"presenting_{popular.id}": "lead", f"interest_{modest.id}": "1", f"presenting_{modest.id}": "none"})

    o, s = browser(organiser), browser(student)
    o.post("/organiser/settings", {"semester_name": "S1", "target_session_count": "2", "target_core_count": "1", "target_optional_count": "1", "categories": "open-topic"})
    page = o.get("/organiser/planning").text
    assert "Recommended" in page and "NO_PRESENTER" in page  # core topic has nobody to present it

    response = o.post("/organiser/planning/sessions", {"topic_id": [str(core.id), str(popular.id)], f"week_{core.id}": "2", f"week_{popular.id}": ""})
    assert "Created 2 provisional session(s)" in o.follow(response).text
    repeat = o.post("/organiser/planning/sessions", {"topic_id": [str(core.id)]})
    assert "Already scheduled" in o.follow(repeat).text
    assert db.scalar(select(func.count()).select_from(SeminarSession)) == 2

    plan = s.get("/plan").text  # provisional sessions are visible, clearly marked
    assert "Provisional" in plan and "Confirmed" not in plan.split("<tbody>")[1]
    assert "Opens when the session is confirmed" in plan

    publish = o.follow(o.post("/organiser/sessions/publish"))
    assert "Published: 1 session(s)" in publish.text and "Popular" in publish.text  # Popular has no week yet
    core_session = db.scalar(select(SeminarSession).where(SeminarSession.topic_id == core.id))
    assert core_session.status == SessionStatus.confirmed

    o.post(f"/organiser/sessions/{core_session.id}/presenters", {"user_id": str(volunteer.id), "presenter_role": "lead"})
    plan = s.get("/plan").text
    assert "Vera Volunteer" in plan and "Confirmed" in plan

    saved = s.follow(s.post(f"/sessions/{core_session.id}/attendance", {"intention": "probably_attend"}, page="/plan"))
    assert "expected attendance was saved" in saved.text
    assert "0 attend · 1 probably" in visible_text(s.get("/plan").text)
    detail = o.get(f"/organiser/sessions/{core_session.id}").text
    assert "Sam Student" in detail and "Probably attend" in detail


def test_provisional_sessions_do_not_take_attendance(browser, db, student, organiser, make_topic):
    from app.services import sessions as session_service

    topic = make_topic()
    (session,), _ = session_service.create_proposed_sessions(db, organiser, [(topic.id, 3)])
    b = browser(student)
    response = b.post(f"/sessions/{session.id}/attendance", {"intention": "attend"}, page="/plan")
    assert "only be recorded for confirmed sessions" in b.follow(response).text


def test_session_page_edit_replace_topic_and_presenters(browser, db, organiser, make_topic, make_user):
    from app.services import sessions as session_service

    first, second = make_topic("First"), make_topic("Second")
    presenter = make_user("pres", display_name="Pat Presenter")
    (session,), _ = session_service.create_proposed_sessions(db, organiser, [(first.id, 4)])
    o = browser(organiser)
    url = f"/organiser/sessions/{session.id}"

    assert "Pat Presenter" in o.get(url).text  # selectable as a presenter
    o.post(f"{url}/presenters", {"user_id": str(presenter.id), "presenter_role": "co_presenter"})
    again = o.post(f"{url}/presenters", {"user_id": str(presenter.id), "presenter_role": "lead"})
    assert "already a presenter" in o.follow(again).text

    saved = o.post(f"{url}/update", {"teaching_week": "6", "session_date": "2026-11-04", "start_time": "15:30", "status": "confirmed", "notes": "Room 4"})
    assert "Session saved" in o.follow(saved).text
    db.refresh(session)
    assert (session.teaching_week, session.status, session.notes) == (6, SessionStatus.confirmed, "Room 4")

    refused = o.post(f"{url}/topic", {"topic_id": str(second.id)})
    assert "only be replaced while the session is proposed" in o.follow(refused).text

    o.post(f"{url}/update", {"teaching_week": "6", "status": "proposed"})
    replaced = o.post(f"{url}/topic", {"topic_id": str(second.id)})
    assert "Topic replaced" in o.follow(replaced).text
    db.refresh(session)
    assert session.topic_id == second.id and session.presenters == []

    o.post(f"{url}/update", {"status": "confirmed"})  # the week was cleared by the omitted field
    assert db.get(SeminarSession, session.id).status == SessionStatus.proposed


def test_manual_session_creation_and_validation(browser, db, organiser, make_topic):
    topic = make_topic("Manual")
    o = browser(organiser)
    ok = o.post("/organiser/sessions/new", {"topic_id": str(topic.id), "teaching_week": "5", "session_date": "2026-10-28", "start_time": "14:00", "status": "confirmed", "notes": ""})
    assert "Session created" in o.follow(ok).text
    bad = o.post("/organiser/sessions/new", {"topic_id": str(topic.id), "teaching_week": "", "session_date": "", "status": "confirmed"})
    assert "needs a teaching week or a date" in o.follow(bad).text
    assert db.scalar(select(func.count()).select_from(SeminarSession)) == 1
