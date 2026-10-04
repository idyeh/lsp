#!/usr/bin/env python3
"""Check the app over real HTTP, the way a user's browser would.

Two modes:

  python scripts/smoke_test.py
      Self-contained: creates a throw-away database, runs the documented commands
      (init-db, create-user), starts the server exactly as production does
      (uvicorn app.main:create_app --factory), then checks pages, the API and a real login.

  python scripts/smoke_test.py --url http://127.0.0.1:8000
      Checks an already running server (used after a deployment). It cannot log in, so it only
      checks what anyone can see: the login page, assets, and that private pages demand a login.

Standard library only. Exits 0 when every check passes, 1 otherwise.
"""

import argparse
import http.cookiejar
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STARTUP_TIMEOUT = 30  # seconds


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # keep 3xx responses visible instead of following them


def _lower(headers) -> dict:
    """Header names are case-insensitive; servers differ in how they write them."""
    return {name.lower(): value for name, value in headers.items()}


class Client:
    def __init__(self, base_url: str):
        self.base = base_url.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect
        )

    def request(self, path: str, data: dict | None = None, headers: dict | None = None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(self.base + path, data=body, headers=headers or {})
        if body is not None:
            request.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(request, timeout=10) as response:
                return response.status, _lower(response.headers), response.read()
        except urllib.error.HTTPError as error:  # 3xx/4xx/5xx still carry a response we want to inspect
            return error.code, _lower(error.headers), error.read()


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if condition else 'FAIL'} {name}" + (f"  ({detail})" if detail and not condition else ""))
    return condition


def public_checks(client: Client) -> bool:
    ok = True
    status, headers, body = client.request("/login")
    ok &= check("login page renders", status == 200 and b"Lab Seminar Planner" in body, f"status {status}")
    status, headers, _ = client.request("/static/style.css")
    ok &= check("stylesheet is served", status == 200 and "css" in headers.get("content-type", ""), f"status {status}")
    status, _, _ = client.request("/static/app.js")
    ok &= check("script is served", status == 200, f"status {status}")
    status, _, body = client.request("/api/auth/me")
    error = json.loads(body).get("error", {}) if body else {}
    ok &= check("API demands a login (401, JSON error)", status == 401 and error.get("code") == "AUTH_REQUIRED", f"{status} {body[:80]!r}")
    status, headers, _ = client.request("/topics")
    ok &= check("private pages redirect to the login page", status == 303 and headers.get("location") == "/login", f"{status} {headers.get('location')}")
    status, _, body = client.request("/api/openapi.json")
    ok &= check("API description is available", status == 200 and "/api/topics" in json.loads(body).get("paths", {}), f"status {status}")
    status, _, body = client.request("/login", headers={"Accept-Language": "zh-CN"})
    ok &= check("Chinese interface is available", status == 200 and "登录".encode() in body, f"status {status}")
    return ok


def login_checks(client: Client, username: str, password: str) -> bool:
    ok = True
    status, _, body = client.request("/api/auth/login", {"username": username, "password": "wrong-password-xyz"})
    ok &= check("wrong password is refused", status == 401, f"status {status}")
    status, _, body = client.request("/api/auth/login", {"username": username, "password": password})
    ok &= check("login succeeds", status == 200 and json.loads(body)["user"]["role"] == "organiser", f"status {status}")
    status, _, body = client.request("/api/settings")
    ok &= check("session cookie works for the API", status == 200 and "semester_name" in json.loads(body), f"status {status}")
    status, _, _ = client.request("/topics")
    ok &= check("private page opens when logged in", status == 200, f"status {status}")
    status, _, _ = client.request("/organiser/users")
    ok &= check("organiser page opens for an organiser", status == 200, f"status {status}")
    return ok


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_cli(env: dict, *args: str, stdin: str = "") -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.cli", *args], cwd=PROJECT_ROOT, env=env, input=stdin, text=True, capture_output=True
    )
    if result.returncode != 0:
        raise SystemExit(f"app.cli {' '.join(args)} failed:\n{result.stdout}{result.stderr}")


def self_contained() -> bool:
    with tempfile.TemporaryDirectory(prefix="lsp-smoke-") as tmp:
        password = secrets.token_urlsafe(12)
        env = {
            **os.environ,
            "LSP_DATABASE_URL": f"sqlite:///{tmp}/smoke.db",
            "LSP_SECRET_KEY": secrets.token_urlsafe(32),
            "LSP_COOKIE_SECURE": "false",
        }
        print("preparing a throw-away database with the documented commands")
        run_cli(env, "init-db")
        run_cli(env, "create-user", "smoke", "--display-name", "Smoke Test", "--role", "organiser", stdin=f"{password}\n{password}\n")

        port = free_port()
        log_path = Path(tmp) / "server.log"
        with open(log_path, "w") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port)],
                cwd=PROJECT_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            )
        base = f"http://127.0.0.1:{port}"
        try:
            if not wait_until_up(base, server):
                print(log_path.read_text())
                return False
            print(f"server is up at {base}")
            client = Client(base)
            ok = public_checks(client)
            ok &= login_checks(client, "smoke", password)
            if not ok:
                print("--- server log ---\n" + log_path.read_text())
            return ok
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()


def wait_until_up(base: str, server: subprocess.Popen | None, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    client = Client(base)
    while time.monotonic() < deadline:
        if server is not None and server.poll() is not None:
            print(f"server exited early with code {server.returncode}")
            return False
        try:
            if client.request("/login")[0] == 200:
                return True
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            pass
        time.sleep(0.3)
    print(f"server did not answer within {timeout:.0f}s")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", help="check an already running server instead of starting one")
    parser.add_argument("--wait", type=float, default=0, help="with --url: seconds to wait for the server to come up")
    args = parser.parse_args()

    if args.url:
        if args.wait and not wait_until_up(args.url, None, args.wait):
            return 1
        ok = public_checks(Client(args.url))
    else:
        ok = self_contained()
    print("smoke test " + ("passed" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
