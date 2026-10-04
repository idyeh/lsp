"""Test helpers for the server-rendered UI."""

import re

from fastapi.testclient import TestClient

from tests.conftest import PASSWORD

CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def visible_text(html):
    """The page text with tags removed and whitespace collapsed, so tests do not depend on markup."""
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class Browser:
    """A cookie-keeping client that fills in the CSRF token the way a real page would."""

    logged_in = False

    def __init__(self, app, headers=None):
        self.client = TestClient(app, follow_redirects=False, headers=headers)

    def token(self, page="/login"):
        response = self.client.get(page)
        assert response.status_code == 200, (page, response.status_code)
        return CSRF.search(response.text).group(1)

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, data=None, page=None, files=None, **kw):
        data = dict(data or {})
        data.setdefault("csrf_token", self.token((page or "/topics") if self.logged_in else "/login"))
        response = self.client.post(url, data=data, files=files, **kw)
        if url == "/logout":
            self.logged_in = False
        return response

    def follow(self, response):
        assert response.status_code == 303, response.status_code
        return self.client.get(response.headers["location"])

    def login(self, user, password=PASSWORD):
        response = self.post("/login", {"username": user.username, "password": password})
        self.logged_in = response.status_code == 303 and response.headers["location"] == "/"
        return response
