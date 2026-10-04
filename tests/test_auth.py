from sqlalchemy import select

from app.enums import Role
from app.models import User
from tests.conftest import PASSWORD


def test_registered_user_can_log_in(anon, student):
    response = anon.post("/api/auth/login", json={"username": "student1", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json() == {
        "user": {"id": student.id, "username": "student1", "display_name": "Sam Student", "role": "student"}
    }
    assert anon.get("/api/auth/me").json()["username"] == "student1"


def test_username_login_is_case_insensitive(anon, student):
    response = anon.post("/api/auth/login", json={"username": "  STUDENT1 ", "password": PASSWORD})
    assert response.status_code == 200


def test_wrong_password_is_rejected(anon, student):
    response = anon.post("/api/auth/login", json={"username": "student1", "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"
    assert anon.get("/api/auth/me").status_code == 401


def test_unknown_user_gets_the_same_error_as_a_wrong_password(anon, student):
    unknown = anon.post("/api/auth/login", json={"username": "nobody", "password": "whatever-123"})
    wrong = anon.post("/api/auth/login", json={"username": "student1", "password": "whatever-123"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_deactivated_user_cannot_log_in(anon, db, student):
    student.is_active = False
    db.commit()
    response = anon.post("/api/auth/login", json={"username": "student1", "password": PASSWORD})
    assert response.status_code == 401


def test_deactivation_ends_an_existing_session(login, db, student):
    client = login(student)
    assert client.get("/api/auth/me").status_code == 200
    student.is_active = False
    db.commit()
    assert client.get("/api/auth/me").status_code == 401


def test_logout_ends_the_session(login, student):
    client = login(student)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401


def test_passwords_are_stored_hashed(db, student):
    stored = db.scalar(select(User.password_hash).where(User.id == student.id))
    assert stored != PASSWORD
    assert PASSWORD not in stored
    assert stored.startswith("$argon2")


def test_session_cookie_is_http_only(anon, student):
    response = anon.post("/api/auth/login", json={"username": "student1", "password": PASSWORD})
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=lax" in response.headers["set-cookie"].lower()


def test_roles_are_distinguishable(login, student, organiser):
    assert login(student).get("/api/auth/me").json()["role"] == Role.student
    assert login(organiser).get("/api/auth/me").json()["role"] == Role.organiser


def test_login_validates_input(anon):
    response = anon.post("/api/auth/login", json={"username": "", "password": ""})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
