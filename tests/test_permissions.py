"""Organiser-only operations (SRS 8.12 rules 1 and 8) and authentication on every route."""

import pytest

ORGANISER_ONLY = [
    ("POST", "/api/topics", {"title": "T", "description": "D", "category": "open-topic"}),
    ("PATCH", "/api/topics/1", {"title": "New"}),
    ("POST", "/api/topic-proposals/1/approve", {}),
    ("POST", "/api/topic-proposals/1/reject", {}),
    ("POST", "/api/topic-proposals/1/archive", None),
    ("GET", "/api/results/topics/1/presenters", None),
    ("POST", "/api/plans/candidate/generate", {}),
    ("POST", "/api/sessions", {"topic_id": 1}),
    ("PATCH", "/api/sessions/1", {"notes": "x"}),
    ("POST", "/api/sessions/1/presenters", {"user_id": 1, "presenter_role": "lead"}),
    ("DELETE", "/api/sessions/1/presenters/1", None),
    ("GET", "/api/sessions/1/attendance", None),
    ("PATCH", "/api/settings", {"voting_open": True}),
]

LOGIN_REQUIRED = ORGANISER_ONLY + [
    ("GET", "/api/auth/me", None),
    ("GET", "/api/topics", None),
    ("GET", "/api/topics/1", None),
    ("GET", "/api/preferences/me", None),
    ("PUT", "/api/topics/1/preference", {"interest_level": 1, "presentation_preference": "none"}),
    ("DELETE", "/api/topics/1/preference", None),
    ("GET", "/api/results/topics", None),
    ("POST", "/api/topic-proposals", {"title": "T", "description": "D"}),
    ("GET", "/api/topic-proposals", None),
    ("PATCH", "/api/topic-proposals/1", {"title": "x"}),
    ("GET", "/api/sessions", None),
    ("GET", "/api/sessions/1", None),
    ("PUT", "/api/sessions/1/attendance/me", {"intention": "attend"}),
    ("GET", "/api/sessions/1/attendance/summary", None),
    ("GET", "/api/settings", None),
]


def call(client, method, path, body):
    return client.request(method, path, json=body) if body is not None else client.request(method, path)


@pytest.mark.parametrize("method, path, body", LOGIN_REQUIRED)
def test_anonymous_requests_are_rejected(anon, method, path, body):
    response = call(anon, method, path, body)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("method, path, body", ORGANISER_ONLY)
def test_students_are_forbidden(login, student, method, path, body):
    response = call(login(student), method, path, body)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize("method, path, body", ORGANISER_ONLY)
def test_organisers_pass_the_permission_check(login, organiser, method, path, body):
    """Past the permission gate the request may still 404/422 on the dummy ids, but never 401/403."""
    response = call(login(organiser), method, path, body)
    assert response.status_code not in (401, 403)


def test_unknown_api_route_returns_json_error(login, student):
    response = login(student).get("/api/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
