import base64
import json

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from app.services import users as user_service
from tests.conftest import PASSWORD

NEW = "a-brand-new-password"


def change(client, current=PASSWORD, new=NEW):
    return client.post("/api/auth/password", json={"current_password": current, "new_password": new})


def test_user_changes_their_own_password(login, anon, student):
    client = login(student)
    assert change(client).status_code == 204
    assert client.get("/api/auth/me").status_code == 200  # the current session carries on
    assert anon.post("/api/auth/login", json={"username": "student1", "password": PASSWORD}).status_code == 401
    assert anon.post("/api/auth/login", json={"username": "student1", "password": NEW}).status_code == 200


def test_wrong_current_password_changes_nothing(login, anon, student):
    response = change(login(student), current="not-my-password")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert anon.post("/api/auth/login", json={"username": "student1", "password": PASSWORD}).status_code == 200


def test_new_password_must_be_acceptable_and_different(login, student):
    client = login(student)
    assert change(client, new="short").json()["error"]["code"] == "WEAK_PASSWORD"
    assert change(client, new=PASSWORD).json()["error"]["code"] == "PASSWORD_UNCHANGED"
    assert change(client, new="").status_code == 422


def test_password_change_requires_login(anon):
    assert change(anon).status_code == 401


def test_password_change_signs_out_the_users_other_sessions(app, login, student):
    here, elsewhere = login(student), login(student)  # two devices
    assert elsewhere.get("/api/auth/me").status_code == 200
    assert change(here).status_code == 204
    assert elsewhere.get("/api/auth/me").status_code == 401
    assert here.get("/api/auth/me").status_code == 200


def test_organiser_password_reset_signs_out_the_old_sessions(app, login, organiser, student):
    victim = login(student)
    with app.state.sessionmaker() as db:
        user_service.set_password(db, user_service.get_user(db, student.id), "set-by-the-organiser")
    assert victim.get("/api/auth/me").status_code == 401
    with TestClient(app) as fresh:
        assert fresh.post("/api/auth/login", json={"username": "student1", "password": "set-by-the-organiser"}).status_code == 200


def test_a_session_without_a_password_stamp_is_rejected(app, login, student):
    """Cookies issued before this check existed carry no stamp; they must log in again."""
    client = login(student)
    cookie = client.cookies.get("session")
    payload = json.loads(base64.b64decode(cookie.split(".")[0]))
    assert "pw" in payload  # the stamp is really there
    payload.pop("pw")
    forged = TimestampSigner("test-secret-key-0123456789abcdef0123456789").sign(base64.b64encode(json.dumps(payload).encode()))
    client.cookies.set("session", forged.decode())
    assert client.get("/api/auth/me").status_code == 401
