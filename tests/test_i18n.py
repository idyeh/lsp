"""Interface translation: the catalogue, language selection, and pages rendered in Chinese."""

import pathlib
import re
from datetime import date

import pytest
from pydantic import ValidationError

from app import i18n
from app.enums import SessionStatus
from app.i18n import N_, describe_validation_error, format_date, translate
from app.i18n.zh import ZH
from app.models import SeminarSession
from app.schemas import Language, ProposalCreate
from tests.helpers import visible_text

APP = pathlib.Path(__file__).parent.parent / "app"
DQ = r'"((?:[^"\\\n]|\\.)*)"'
SQ = r"'((?:[^'\\\n]|\\.)*)'"
IN_TEMPLATES = [re.compile(r"(?<![\w.])_\(\s*" + q) for q in (DQ, SQ)]
IN_PYTHON = [re.compile(r"\bN_\(\s*" + DQ)]
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def marked_strings() -> set[str]:
    """Every string marked for translation in the templates (_()) and Python code (N_())."""
    found: set[str] = set()
    for path in APP.rglob("*.html"):
        for rx in IN_TEMPLATES:
            found.update(m.group(1) for m in rx.finditer(path.read_text()))
    for path in APP.rglob("*.py"):
        if path.parent.name != "i18n" or path.name != "zh.py":
            for rx in IN_PYTHON:
                found.update(m.group(1) for m in rx.finditer(path.read_text()))
    return found


# --- the catalogue ----------------------------------------------------------------


def test_every_marked_string_has_a_chinese_translation():
    missing = sorted(marked_strings() - set(ZH))
    assert missing == [], f"add these to app/i18n/zh.py: {missing}"


def test_the_catalogue_has_no_unused_entries():
    unused = sorted(set(ZH) - marked_strings())
    assert unused == [], f"remove these from app/i18n/zh.py: {unused}"


def test_translations_keep_the_same_placeholders():
    for english, chinese in ZH.items():
        assert set(PLACEHOLDER.findall(english)) == set(PLACEHOLDER.findall(chinese)), english


def test_the_schema_language_type_matches_the_supported_languages():
    assert set(Language.__args__) == set(i18n.LANGUAGES)


# --- translate() ------------------------------------------------------------------------


def test_translate_uses_the_catalogue_and_falls_back_to_english():
    assert translate("zh", "Log in") == "登录"
    assert translate("en", "Log in") == "Log in"
    assert translate("zh", "A sentence nobody translated") == "A sentence nobody translated"
    assert translate("fr", "Log in") == "Log in"


def test_translate_fills_placeholders():
    assert translate("zh", "Week {n}", n=3) == "第 3 周"
    assert translate("en", "Week {n}", n=3) == "Week 3"


def test_only_marked_parameters_are_translated():
    """A user's text that happens to equal a catalogue entry must not be altered."""
    assert translate("zh", "Schedule {title}", title="Core") == "安排“Core”"
    assert translate("zh", "{current} to {new}", current=N_("draft"), new="draft") == "草稿 to draft"


def test_dates_are_formatted_for_the_language():
    assert format_date("zh", date(2026, 10, 28)) == "2026年10月28日 周三"
    assert format_date("en", date(2026, 10, 28)) == "Wed 28 Oct 2026"


def test_validation_errors_are_described_in_both_languages():
    with pytest.raises(ValidationError) as empty:
        ProposalCreate(title="  ", description="x")
    assert describe_validation_error("en", empty.value) == "Title: must not be empty."
    assert describe_validation_error("zh", empty.value) == "标题：不能为空。"
    with pytest.raises(ValidationError) as long:
        ProposalCreate(title="x" * 201, description="x")
    assert describe_validation_error("zh", long.value) == "标题：最多 200 个字符。"
    with pytest.raises(ValidationError) as many:
        ProposalCreate(title="t", description="d", keywords=[str(i) for i in range(25)])
    assert describe_validation_error("zh", many.value) == "关键词：最多允许 20 个关键词。"


# --- choosing the language ----------------------------------------------------------------


def test_default_language_is_english(browser):
    page = browser().get("/login")
    assert '<html lang="en">' in page.text and "Log in" in page.text


@pytest.mark.parametrize(
    "header, expected",
    [("zh-CN,zh;q=0.9,en;q=0.8", "zh"), ("en-US,en;q=0.9", "en"), ("fr-FR,fr;q=0.9", "en"), ("fr, zh-TW", "zh")],
)
def test_language_follows_the_browser_preference(browser, header, expected):
    page = browser(headers={"Accept-Language": header}).get("/login")
    assert f'<html lang="{"zh-CN" if expected == "zh" else "en"}">' in page.text


def test_chosen_language_overrides_the_browser_and_is_remembered(browser):
    b = browser(headers={"Accept-Language": "zh-CN"})
    assert "登录" in b.get("/login").text
    response = b.post("/language", {"lang": "en", "next": "/login"}, page="/login")
    assert (response.status_code, response.headers["location"]) == (303, "/login")
    assert "lsp_lang=en" in response.headers["set-cookie"] and "httponly" in response.headers["set-cookie"].lower()
    page = b.get("/login")
    assert '<html lang="en">' in page.text and "Log in" in page.text
    assert "登录" not in visible_text(page.text).replace("中文", "")


def test_language_survives_logout_and_login(browser, student):
    b = browser(student)
    b.post("/language", {"lang": "zh", "next": "/topics"})
    b.post("/logout")
    assert '<html lang="zh-CN">' in b.get("/login").text
    b.login(student)
    assert '<html lang="zh-CN">' in b.get("/topics").text


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example/x", "/\\evil.example", "evil", "", "/a\r\nSet-Cookie: x=y"])
def test_language_switch_never_redirects_off_site(browser, target):
    response = browser().post("/language", {"lang": "zh", "next": target}, page="/login")
    assert response.headers["location"] == "/"


def test_unknown_languages_are_ignored(browser):
    b = browser()
    response = b.post("/language", {"lang": "xx", "next": "/login"}, page="/login")
    assert "set-cookie" not in response.headers
    assert b.get("/login").status_code == 200


def test_a_bogus_language_cookie_is_ignored(browser):
    b = browser()
    b.client.cookies.set("lsp_lang", "klingon")
    assert '<html lang="en">' in b.get("/login").text


def test_the_switcher_is_on_every_page_and_returns_to_the_same_page(browser, student):
    b = browser(student)
    page = b.get("/results?x=1")
    assert 'name="next" value="/results?x=1"' in page.text
    assert ">中文<" in page.text and ">English<" in page.text


# --- pages in Chinese ----------------------------------------------------------------------------


@pytest.fixture
def chinese(browser):
    def _chinese(user=None):
        b = browser(user, headers={"Accept-Language": "zh-CN"})
        return b

    return _chinese


def test_main_pages_render_in_chinese(chinese, voting_open, student, make_topic):
    make_topic("Games")
    b = chinese(student)
    topics = b.get("/topics").text
    assert '<html lang="zh-CN">' in topics
    for text in ("主题与投票", "核心主题", "选修主题", "保存我的偏好", "参加兴趣", "投票已开放"):
        assert text in topics, text
    assert "选修" in b.get("/results").text and "兴趣得分" in b.get("/results").text
    assert "提议主题" in b.get("/proposals").text
    assert "学期计划" in b.get("/plan").text


def test_organiser_pages_render_in_chinese(chinese, organiser):
    b = chinese(organiser)
    for path, expected in [
        ("/organiser/topics", "管理主题"),
        ("/organiser/topics/new", "中文标题"),
        ("/organiser/proposals", "审核提案"),
        ("/organiser/settings", "设置与投票"),
        ("/organiser/users", "创建账号"),
        ("/organiser/planning", "规划与场次"),
        ("/account", "修改你的密码"),
    ]:
        assert expected in b.get(path).text, path


def test_rule_violations_are_explained_in_chinese(chinese, student, make_topic):
    topic = make_topic("Games")
    b = chinese(student)  # voting is closed by default
    response = b.post("/topics/preferences", {f"interest_{topic.id}": "3", f"presenting_{topic.id}": "none"})
    assert "投票已关闭，偏好不能再修改。" in b.follow(response).text


def test_form_errors_are_explained_in_chinese(chinese, student):
    b = chinese(student)
    response = b.post("/proposals", {"title": "", "description": "d"})
    assert "标题：不能为空。" in b.follow(response).text


def test_login_failure_is_explained_in_chinese(chinese, student):
    b = chinese()
    response = b.login(student, "wrong-password")
    assert "用户名或密码不正确。" in b.follow(response).text


def test_error_pages_are_translated(chinese, student):
    page = chinese(student).get("/no-such-page")
    assert page.status_code == 404 and "页面不存在" in page.text


def test_messages_with_parameters_are_translated_including_status_words(chinese, db, organiser, make_topic):
    from app.services import sessions as session_service

    topic = make_topic()
    (session,), _ = session_service.create_proposed_sessions(db, organiser, [(topic.id, 2)])
    b = chinese(organiser)
    response = b.post(f"/organiser/sessions/{session.id}/update", {"teaching_week": "2", "status": "completed"})
    assert "暂定的场次不能变为已完成。" in b.follow(response).text


def test_session_dates_are_shown_in_chinese(chinese, db, student, organiser, make_topic):
    topic = make_topic()
    db.add(SeminarSession(topic_id=topic.id, teaching_week=4, session_date=date(2026, 10, 28),
                          status=SessionStatus.confirmed, created_by=organiser.id))
    db.commit()
    text = visible_text(chinese(student).get("/plan").text)
    assert "第 4 周" in text and "2026年10月28日 周三" in text and "已确认" in text


def test_the_api_stays_in_english_whatever_the_language(browser, student):
    b = browser(headers={"Accept-Language": "zh-CN"})
    b.client.cookies.set("lsp_lang", "zh")
    response = b.client.post("/api/auth/login", json={"username": "student1", "password": "nope-nope-nope"})
    assert response.json()["error"] == {"code": "AUTH_INVALID_CREDENTIALS", "message": "Invalid username or password."}
