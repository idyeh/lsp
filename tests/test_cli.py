import json
import pathlib

import pytest
from sqlalchemy import func, inspect, select

from app import cli
from app.database import make_engine, make_sessionmaker
from app.models import Preference, Topic, TopicTranslation, User
from app.services import settings as settings_service


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'nested' / 'seminar.db'}"
    monkeypatch.setenv("LSP_DATABASE_URL", url)
    engine = make_engine(url)
    yield engine, make_sessionmaker(engine)
    engine.dispose()


def test_init_db_creates_the_schema_from_a_clean_environment(cli_db, capsys):
    engine, _ = cli_db
    assert cli.main(["init-db"]) == 0
    assert {"users", "topics", "topic_proposals", "preferences", "sessions", "session_presenters",
            "attendance_intents", "system_settings"} <= set(inspect(engine).get_table_names())
    assert cli.main(["init-db"]) == 0  # running it again is harmless
    assert "Database ready" in capsys.readouterr().out


def test_create_user_prompts_for_a_password(cli_db, monkeypatch, capsys):
    _, sessions = cli_db
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "a-long-password")
    assert cli.main(["create-user", "Boss", "--display-name", "The Boss", "--role", "organiser"]) == 0
    with sessions() as db:
        user = db.scalar(select(User))
        assert (user.username, user.role.value) == ("boss", "organiser")
        assert user.password_hash.startswith("$argon2")
    assert "Created organiser 'boss'" in capsys.readouterr().out


def test_create_user_rejects_mismatched_and_weak_passwords(cli_db, monkeypatch, capsys):
    answers = iter(["a-long-password", "something-else"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    assert cli.main(["create-user", "x", "--display-name", "X"]) == 1
    assert "do not match" in capsys.readouterr().err
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "short")
    assert cli.main(["create-user", "x", "--display-name", "X"]) == 1
    assert "Password must be" in capsys.readouterr().err


TOPICS = {
    "core_topics": [
        {"title": "Running a Seminar", "description": "How to run one.", "category": "seminar-practice",
         "topic_type": "core", "keywords": ["seminar", "teamwork"], "status": "active"},
    ],
    "elective_topics": [
        {"title": "AI in Games", "description": "AI characters.", "category": "creative-ai",
         "topic_type": "optional", "keywords": ["NPC"], "status": "active"},
        {"title": "Robot Basics", "description": "Robots.", "category": "robotics",
         "topic_type": "optional", "keywords": [], "status": "active"},
    ],
}
ZH_FILE = {
    "language": "zh",
    "topics": {
        "Running a Seminar": {"title": "办好研讨会", "description": "怎么办。", "keywords": ["研讨会"]},
        "AI in Games": {"title": "游戏里的 AI", "description": "AI 角色。", "keywords": ["NPC"]},
        "Not In The File": {"title": "无", "description": "无"},
    },
    "category_labels": {"seminar-practice": "研讨实践", "creative-ai": "创意 AI"},
}


@pytest.fixture
def files(tmp_path):
    topics, zh = tmp_path / "topics.json", tmp_path / "topics.zh.json"
    topics.write_text(json.dumps(TOPICS), encoding="utf-8")
    zh.write_text(json.dumps(ZH_FILE, ensure_ascii=False), encoding="utf-8")
    return topics, zh


def seed_organiser(sessions, username="boss"):
    from app.database import init_db
    from app.enums import Role
    from app.services import users

    init_db(sessions.kw["bind"])  # the cli_db fixture starts from an empty file
    with sessions() as db:
        users.create_user(db, username, "Boss", "a-long-password", Role.organiser)


def test_import_topics_creates_topics_translations_and_categories(cli_db, files, capsys):
    _, sessions = cli_db
    topics_file, zh_file = files
    seed_organiser(sessions)
    assert cli.main(["import-topics", str(topics_file)]) == 0  # the .zh.json next to it is found automatically
    out = capsys.readouterr().out
    assert "3 created" in out and "Categories added: seminar-practice, creative-ai" in out
    assert "no translation for: Robot Basics" in out
    assert "translation matches no topic in the file: Not In The File" in out

    with sessions() as db:
        by_title = {t.title: t for t in db.scalars(select(Topic))}
        assert set(by_title) == {"Running a Seminar", "AI in Games", "Robot Basics"}
        seminar = by_title["Running a Seminar"]
        assert (seminar.topic_type.value, seminar.status.value, seminar.category) == ("core", "active", "seminar-practice")
        assert seminar.localized("zh").title == "办好研讨会" and seminar.localized("zh").keyword_list == ["研讨会"]
        assert by_title["Robot Basics"].localized("zh").title == "Robot Basics"  # untranslated: English fallback
        assert seminar.created_by == db.scalar(select(User.id))
        assert "seminar-practice" in settings_service.get_categories(db)
        labels = settings_service.get(db, "category_labels")
        assert labels["creative-ai"]["zh"] == "创意 AI" and settings_service.category_label("creative-ai") == "Creative AI"


def test_importing_twice_changes_nothing(cli_db, files, capsys):
    _, sessions = cli_db
    seed_organiser(sessions)
    cli.main(["import-topics", str(files[0])])
    capsys.readouterr()
    assert cli.main(["import-topics", str(files[0])]) == 0
    assert "0 created, 3 already present" in capsys.readouterr().out
    with sessions() as db:
        assert db.scalar(select(func.count(Topic.id))) == 3
        assert db.scalar(select(func.count(TopicTranslation.id))) == 2


def test_a_translation_file_added_later_fills_in_missing_translations_only(cli_db, files, capsys):
    _, sessions = cli_db
    topics_file, zh_file = files
    seed_organiser(sessions)
    cli.main(["import-topics", str(topics_file), "--translations", str(zh_file.with_name("absent.json"))])
    assert "topics are English only" in capsys.readouterr().out
    with sessions() as db:
        assert db.scalar(select(func.count(TopicTranslation.id))) == 0
        topic = db.scalar(select(Topic).where(Topic.title == "AI in Games"))
        topic.translations.append(TopicTranslation(language="zh", title="已有的翻译", description="手工编辑过"))
        db.commit()

    cli.main(["import-topics", str(topics_file)])
    assert "Translations added to existing topics: 1" in capsys.readouterr().out  # only 'Running a Seminar'
    with sessions() as db:
        assert db.scalar(select(Topic).where(Topic.title == "AI in Games")).localized("zh").title == "已有的翻译"
        assert db.scalar(select(Topic).where(Topic.title == "Running a Seminar")).localized("zh").title == "办好研讨会"


def test_existing_titles_are_matched_ignoring_case_and_duplicates_in_a_file_are_imported_once(cli_db, tmp_path, capsys):
    _, sessions = cli_db
    seed_organiser(sessions)
    data = {"topics": [dict(TOPICS["elective_topics"][1]), dict(TOPICS["elective_topics"][1], title="ROBOT BASICS")]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(data))
    assert cli.main(["import-topics", str(path)]) == 0
    with sessions() as db:
        assert db.scalar(select(func.count(Topic.id))) == 1


def test_import_reports_problems_and_creates_nothing(cli_db, tmp_path, capsys):
    _, sessions = cli_db
    seed_organiser(sessions)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"topics": [TOPICS["core_topics"][0], {"title": "", "description": "x", "category": "robotics"}]}))
    assert cli.main(["import-topics", str(bad)]) == 1
    assert "topics #2" in capsys.readouterr().err
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json")
    assert cli.main(["import-topics", str(broken)]) == 1 and "not valid JSON" in capsys.readouterr().err
    assert cli.main(["import-topics", str(tmp_path / "missing.json")]) == 1 and "File not found" in capsys.readouterr().err
    with sessions() as db:
        assert db.scalar(select(func.count(Topic.id))) == 0


def test_import_needs_an_organiser_to_own_the_topics(cli_db, files, capsys):
    assert cli.main(["import-topics", str(files[0])]) == 1
    assert "create one first" in capsys.readouterr().err


def test_import_rejects_a_translation_file_without_a_valid_language(cli_db, files, tmp_path, capsys):
    _, sessions = cli_db
    seed_organiser(sessions)
    bad = tmp_path / "bad.zh.json"
    bad.write_text(json.dumps({"language": "en", "topics": {}}))
    assert cli.main(["import-topics", str(files[0]), "--translations", str(bad)]) == 1
    assert "'language' must be one of: zh" in capsys.readouterr().err


def test_seed_demo_populates_an_empty_database_once(cli_db, files, capsys):
    _, sessions = cli_db
    assert cli.main(["seed-demo", "--topics-file", str(files[0])]) == 0
    out = capsys.readouterr().out
    with sessions() as db:
        assert db.scalar(select(func.count(User.id))) == 9
        assert db.scalar(select(func.count(Topic.id))) == 3
        assert db.scalar(select(func.count(Preference.id))) > 0
        assert settings_service.is_voting_open(db)
        assert settings_service.get(db, "semester_name_zh") == "演示学期"
    assert "organiser" in out and "student8" in out

    assert cli.main(["seed-demo", "--topics-file", str(files[0])]) == 1  # refuses a database that has users
    assert "already has users" in capsys.readouterr().err


def test_seed_demo_needs_the_sample_file(cli_db, tmp_path, capsys):
    engine, _ = cli_db
    assert cli.main(["seed-demo", "--topics-file", str(tmp_path / "nope.json")]) == 1
    assert "Sample topics not found" in capsys.readouterr().err
    assert not pathlib.Path(engine.url.database).exists()  # it failed before touching the database


def test_the_real_sample_files_import_cleanly_when_present(cli_db, capsys):
    """Guards the project's own sample data. Skipped where the (git-ignored) files are absent."""
    if not cli.DEFAULT_TOPICS_FILE.exists():
        pytest.skip("docs/sample_topics.json is not present")
    _, sessions = cli_db
    seed_organiser(sessions)
    assert cli.main(["import-topics"]) == 0
    out = capsys.readouterr().out
    assert "no translation for" not in out and "matches no topic" not in out, out
    with sessions() as db:
        topics = list(db.scalars(select(Topic)))
        assert topics and all(t.status.value == "active" for t in topics)
        if cli.DEFAULT_TOPICS_FILE.with_suffix(".zh.json").exists():
            assert all("zh" in t.translation_map for t in topics)
