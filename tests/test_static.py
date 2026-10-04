"""Static assets and the password show/hide toggle's wiring.

The toggle itself runs in the browser; these tests check that it is delivered, translated, and
covers every password field. Its behaviour was checked by hand in a browser.
"""

import re

PASSWORD_PAGES = ["/login", "/account"]


def test_the_script_and_stylesheet_are_served(browser):
    b = browser()
    for path, kind in [("/static/app.js", "javascript"), ("/static/style.css", "css")]:
        response = b.get(path)
        assert response.status_code == 200 and kind in response.headers["content-type"]


def test_the_script_is_loaded_with_translated_labels(browser):
    page = browser().get("/login").text
    assert re.search(r'<script src="/static/app\.js\?v=\d+" defer', page)
    assert 'data-show-label="Show"' in page and 'data-hide-label="Hide"' in page and 'data-aria-label="Show password"' in page
    chinese = browser(headers={"Accept-Language": "zh-CN"}).get("/login").text
    assert 'data-show-label="显示"' in chinese and 'data-hide-label="隐藏"' in chinese and 'data-aria-label="显示密码"' in chinese


def test_every_password_field_is_a_real_password_input_the_script_can_enhance(browser, student, organiser):
    """Fields must start hidden (type=password) so the toggle is purely an opt-in reveal."""
    pages = {"/login": browser(), "/account": browser(student), "/organiser/users": browser(organiser)}
    for path, b in pages.items():
        html = b.get(path).text
        fields = re.findall(r"<input [^>]*>", html)
        passwords = [f for f in fields if 'name="password"' in f or "_password" in f]
        assert passwords, path
        assert all('type="password"' in f for f in passwords), path
        assert not any(re.search(r'type="text"[^>]*(?:name="[a-z_]*password)', f) for f in fields), path


def test_autocomplete_hints_are_set_so_password_managers_keep_working(browser, student):
    login = browser().get("/login").text
    assert 'autocomplete="current-password"' in login and 'autocomplete="username"' in login
    account = browser(student).get("/account").text
    assert account.count('autocomplete="new-password"') == 2 and 'autocomplete="current-password"' in account


def test_the_script_never_submits_forms_or_changes_other_inputs():
    """Cheap guards on the source: the toggle is a type=button and only touches password inputs."""
    from pathlib import Path

    source = (Path(__file__).parent.parent / "app" / "web" / "static" / "app.js").read_text()
    assert 'button.type = "button"' in source
    assert "input[type=\"password\"]" in source
