"""Topic content in two languages, bilingual category labels and the optional Chinese semester name."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text

from app.config import Settings
from app.database import init_db, make_engine, make_sessionmaker
from app.enums import TopicType
from app.main import create_app
from app.models import Topic, TopicTranslation
from app.services import settings as settings_service
from tests.helpers import visible_text

ZH = {"title": "游戏世界里的 AI", "description": "探索 AI 角色与记忆。", "keywords": ["NPC", "记忆"]}
BODY = {"title": "AI in Games", "description": "Explore AI characters.", "category": "game-development",
        "topic_type": "optional", "keywords": ["NPC"], "status": "active"}


# --- model and API ------------------------------------------------------------------------


def test_localized_text_falls_back_to_english(db, make_topic):
    from app.services import topics as topic_service

    topic = make_topic("Games", keywords=["a", "b"])
    assert topic.localized("zh").title == "Games" and topic.localized("zh").keyword_list == ["a", "b"]
    topic_service.apply_translations(topic, {"zh": ZH})
    db.commit()
    assert topic.localized("zh").title == "游戏世界里的 AI"
    assert topic.localized("zh").keyword_list == ["NPC", "记忆"]
    assert topic.localized("en").title == "Games"  # English is unchanged


def test_create_a_topic_with_a_chinese_version(login, organiser, student):
    created = login(organiser).post("/api/topics", json={**BODY, "translations": {"zh": ZH}})
    assert created.status_code == 201
    assert created.json()["translations"] == {"zh": ZH}
    topic_id = created.json()["id"]
    assert login(student).get(f"/api/topics/{topic_id}").json()["translations"]["zh"]["title"] == "游戏世界里的 AI"
    listed = login(student).get("/api/topics").json()
    assert listed[0]["translations"]["zh"]["description"] == "探索 AI 角色与记忆。"


def test_topics_without_a_translation_report_none(login, student, make_topic):
    make_topic("Plain")
    assert login(student).get("/api/topics").json()[0]["translations"] == {}


def test_update_replaces_removes_and_leaves_translations_alone(login, organiser):
    org = login(organiser)
    topic_id = org.post("/api/topics", json={**BODY, "translations": {"zh": ZH}}).json()["id"]
    url = f"/api/topics/{topic_id}"

    unrelated = org.patch(url, json={"title": "Renamed"})
    assert unrelated.json()["translations"] == {"zh": ZH}  # omitted: untouched

    new_zh = {"title": "新标题", "description": "新描述", "keywords": []}
    assert org.patch(url, json={"translations": {"zh": new_zh}}).json()["translations"] == {"zh": new_zh}
    assert org.patch(url, json={"translations": {"zh": None}}).json()["translations"] == {}  # null removes it
    assert org.patch(url, json={"translations": {"zh": None}}).status_code == 200  # removing nothing is fine


def test_translation_validation(login, organiser):
    org = login(organiser)
    assert org.post("/api/topics", json={**BODY, "translations": {"fr": ZH}}).status_code == 422
    assert org.post("/api/topics", json={**BODY, "translations": {"zh": {"title": "只有标题"}}}).status_code == 422
    assert org.post("/api/topics", json={**BODY, "translations": {"zh": {**ZH, "title": " "}}}).status_code == 422


def test_there_is_at_most_one_translation_per_language(db, make_topic):
    from sqlalchemy.exc import IntegrityError

    topic = make_topic()
    db.add_all([TopicTranslation(topic_id=topic.id, language="zh", title="a", description="b"),
                TopicTranslation(topic_id=topic.id, language="zh", title="c", description="d")])
    with pytest.raises(IntegrityError):
        db.commit()


def test_an_existing_database_gains_the_new_table_without_losing_data(tmp_path):
    """The translations table is new, so starting the app on a pre-existing database needs no migration."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = make_engine(url)
    init_db(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, display_name, password_hash, role, is_active, created_at) "
                          "VALUES ('o', 'O', 'x', 'organiser', 1, '2026-01-01 00:00:00')"))
        conn.execute(text("INSERT INTO topics (title, description, category, topic_type, status, created_by, created_at, updated_at) "
                          "VALUES ('Old topic', 'd', 'robotics', 'core', 'active', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"))
        conn.execute(text("DROP TABLE topic_translations"))  # what a database from before this feature looks like
    assert "topic_translations" not in inspect(engine).get_table_names()
    engine.dispose()

    app = create_app(Settings(url, "k" * 40, False, 3600))
    with TestClient(app):  # start-up runs the lifespan
        pass
    assert "topic_translations" in inspect(app.state.engine).get_table_names()
    with make_sessionmaker(app.state.engine)() as session:
        assert session.scalar(select(func.count()).select_from(Topic)) == 1
        assert session.scalar(select(Topic.title)) == "Old topic"
    app.state.engine.dispose()


# --- web pages -------------------------------------------------------------------------------


def with_chinese(db, topic):
    from app.services import topics as topic_service

    topic_service.apply_translations(topic, {"zh": ZH})
    db.commit()


def test_users_see_the_topic_in_their_language(browser, db, student, make_topic):
    topic = make_topic("AI in Games", keywords=["NPC"])
    with_chinese(db, topic)
    english = browser(student).get("/topics").text
    assert "AI in Games" in english and "游戏世界里的 AI" not in english
    chinese = browser(student, headers={"Accept-Language": "zh-CN"}).get("/topics").text
    assert "游戏世界里的 AI" in chinese and "探索 AI 角色与记忆。" in chinese and "记忆" in chinese
    assert "AI in Games" not in chinese


def test_untranslated_topics_show_english_to_chinese_users(browser, student, make_topic):
    make_topic("Only English")
    assert "Only English" in browser(student, headers={"Accept-Language": "zh-CN"}).get("/topics").text


def test_results_and_plan_use_the_translated_title(browser, db, student, organiser, make_topic):
    from app.services import sessions as session_service

    topic = make_topic("AI in Games")
    with_chinese(db, topic)
    session_service.create_proposed_sessions(db, organiser, [(topic.id, 3)])
    b = browser(student, headers={"Accept-Language": "zh-CN"})
    assert "游戏世界里的 AI" in b.get("/results").text
    assert "游戏世界里的 AI" in b.get("/plan").text


def test_organiser_edits_both_versions_in_the_topic_form(browser, db, organiser):
    o = browser(organiser)
    form = {"title": "AI in Games", "description": "Explore.", "category": "game-development", "topic_type": "optional",
            "status": "active", "keywords": "NPC", "title_zh": "游戏里的 AI", "description_zh": "探索。", "keywords_zh": "角色, 记忆"}
    assert "Topic created" in o.follow(o.post("/organiser/topics/new", form)).text
    topic = db.scalar(select(Topic))
    assert topic.localized("zh").title == "游戏里的 AI" and topic.localized("zh").keyword_list == ["角色", "记忆"]
    assert "游戏里的 AI" in o.get(f"/organiser/topics/{topic.id}").text  # the form shows the stored Chinese text

    o.post(f"/organiser/topics/{topic.id}", {**form, "title_zh": "新标题"})
    db.refresh(topic)
    assert topic.localized("zh").title == "新标题"

    o.post(f"/organiser/topics/{topic.id}", {**form, "title_zh": "", "description_zh": "", "keywords_zh": ""})
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(TopicTranslation)) == 0  # emptying both removes the version


def test_a_half_filled_chinese_version_is_rejected(browser, db, organiser):
    o = browser(organiser)
    form = {"title": "T", "description": "D", "category": "robotics", "topic_type": "optional", "status": "active",
            "title_zh": "只有标题", "description_zh": ""}
    assert "both a Chinese title and a Chinese description" in o.follow(o.post("/organiser/topics/new", form)).text
    assert db.scalar(select(func.count()).select_from(Topic)) == 0


def test_topic_list_shows_which_topics_lack_a_chinese_version(browser, db, organiser, make_topic):
    with_chinese(db, make_topic("Translated"))
    make_topic("Not yet")
    rows = visible_text(browser(organiser).get("/organiser/topics").text)
    assert "Translated" in rows and "✓" in rows and "missing" in rows


# --- categories and semester name --------------------------------------------------------------------


def test_category_labels_follow_the_language():
    labels = settings_service.DEFAULT_CATEGORY_LABELS
    assert settings_service.category_label("robotics", "en", labels) == "Robotics"
    assert settings_service.category_label("robotics", "zh", labels) == "机器人"
    assert settings_service.category_label("made-up", "zh", labels) == "Made up"  # falls back to a name from the slug
    assert settings_service.category_label("x-y", "en", {"x-y": {"en": "Custom"}}) == "Custom"


def test_default_categories_have_chinese_names(browser, student, make_topic):
    make_topic("T", category="game-development")
    assert "游戏开发" in browser(student, headers={"Accept-Language": "zh-CN"}).get("/topics").text
    assert "Game development" in browser(student).get("/topics").text


def test_category_labels_are_configurable_through_the_api(login, organiser, student):
    org = login(organiser)
    body = org.patch("/api/settings", json={"categories": ["seminar-practice", "robotics"],
                                            "category_labels": {"seminar-practice": {"en": "Seminar craft", "zh": "研讨实践"}}}).json()
    assert body["category_labels"]["seminar-practice"] == {"en": "Seminar craft", "zh": "研讨实践"}
    assert body["category_labels"]["robotics"] == {"zh": "机器人"}  # defaults stay available
    assert login(student).get("/api/settings").json()["category_labels"]["seminar-practice"]["zh"] == "研讨实践"
    assert org.patch("/api/settings", json={"category_labels": {"Bad Slug!": {"zh": "x"}}}).status_code == 422


def test_category_labels_are_edited_as_text_lines(browser, db, organiser, make_topic):
    o = browser(organiser)
    page = o.get("/organiser/settings").text
    assert "robotics | Robotics | 机器人" in page  # the current labels are shown
    lines = "seminar-practice | Seminar practice | 研讨实践\nrobotics | Robotics | 机器人\nvision, open-topic"
    form = {"semester_name": "Autumn", "semester_name_zh": "秋季学期", "target_session_count": "8",
            "target_core_count": "4", "target_optional_count": "4", "categories": lines}
    assert "Settings saved" in o.follow(o.post("/organiser/settings", form)).text
    assert settings_service.get_categories(db) == ["seminar-practice", "robotics", "vision", "open-topic"]
    labels = settings_service.get(db, "category_labels")
    assert labels["seminar-practice"] == {"zh": "研讨实践"}  # the English name equals the derived one, so it is not stored
    assert settings_service.get(db, "semester_name_zh") == "秋季学期"


def test_semester_name_follows_the_language(browser, db, student):
    settings_service.update(db, {"semester_name": "Autumn", "semester_name_zh": "秋季学期"})
    assert "Autumn" in browser(student).get("/topics").text
    assert "秋季学期" in browser(student, headers={"Accept-Language": "zh-CN"}).get("/topics").text
    settings_service.update(db, {"semester_name_zh": ""})  # no Chinese name: show the English one
    assert "Autumn" in browser(student, headers={"Accept-Language": "zh-CN"}).get("/topics").text


def test_topic_type_labels_are_localised(browser, student, make_topic):
    make_topic("C", TopicType.core)
    assert "核心" in browser(student, headers={"Accept-Language": "zh-CN"}).get("/topics").text
