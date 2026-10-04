"""Shared fixtures. Every test gets a fresh SQLite database in a temporary directory."""

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from app import security
from app.config import Settings
from app.database import init_db
from app.enums import PresentationPreference, Role, TopicStatus, TopicType
from app.main import create_app
from app.models import Preference
from app.schemas import TopicCreate
from app.services import settings as settings_service
from app.services import topics as topic_service
from app.services import users as user_service

PASSWORD = "test-password-123"  # test-only value


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Cheap argon2 parameters keep the suite fast; production uses the library defaults."""
    monkeypatch.setattr(security, "_hasher", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    monkeypatch.setattr(security, "_DUMMY_HASH", security._hasher.hash("not-a-real-password"))


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        secret_key="test-secret-key-0123456789abcdef0123456789",
        cookie_secure=False,
        session_max_age=3600,
    )
    application = create_app(settings)
    init_db(application.state.engine)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def db(app):
    session = app.state.sessionmaker()
    yield session
    session.close()


@pytest.fixture
def make_user(db):
    def _make(username="student1", role=Role.student, display_name=None, year_group=None):
        return user_service.create_user(
            db, username, display_name or username.title(), PASSWORD, role=role, year_group=year_group
        )

    return _make


@pytest.fixture
def organiser(make_user):
    return make_user("organiser", Role.organiser, "Olivia Organiser")


@pytest.fixture
def student(make_user):
    return make_user("student1", Role.student, "Sam Student")


@pytest.fixture
def make_topic(db, organiser):
    def _make(
        title="Topic", topic_type=TopicType.optional, status=TopicStatus.active, category="open-topic", keywords=()
    ):
        data = TopicCreate(
            title=title,
            description=f"About {title}",
            category=category,
            topic_type=topic_type,
            keywords=list(keywords),
            status=status,
        )
        return topic_service.create_topic(db, data, organiser)

    return _make


@pytest.fixture
def make_preference(db):
    """Insert a preference row directly, bypassing voting rules (for building scenarios)."""

    def _make(user, topic, interest=0, presenting=PresentationPreference.none):
        row = Preference(
            user_id=user.id, topic_id=topic.id, interest_level=interest, presentation_preference=presenting
        )
        db.add(row)
        db.commit()
        return row

    return _make


@pytest.fixture
def voting_open(db):
    settings_service.update(db, {"voting_open": True})


@pytest.fixture
def login(app):
    """Return a TestClient logged in as the given user (each call gets its own cookie jar)."""
    clients = []

    def _login(user, password=PASSWORD):
        client = TestClient(app)
        response = client.post("/api/auth/login", json={"username": user.username, "password": password})
        assert response.status_code == 200, response.text
        clients.append(client)
        return client

    yield _login
    for client in clients:
        client.close()


@pytest.fixture
def anon(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def browser(app):
    """Factory for web clients: browser(user) is logged in through the real login form."""
    from tests.helpers import Browser

    created = []

    def _browser(user=None, headers=None):
        b = Browser(app, headers)
        created.append(b)
        if user:
            assert b.login(user).headers["location"] == "/"
        return b

    yield _browser
    for b in created:
        b.client.close()
