import pytest
from sqlalchemy import func, inspect, select

from app import cli
from app.database import make_engine, make_sessionmaker
from app.models import Preference, Topic, User
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


def test_seed_demo_populates_an_empty_database_once(cli_db, capsys):
    _, sessions = cli_db
    assert cli.main(["seed-demo"]) == 0
    out = capsys.readouterr().out
    with sessions() as db:
        assert db.scalar(select(func.count(User.id))) == 9
        assert db.scalar(select(func.count(Topic.id))) == len(cli.DEMO_TOPICS)
        assert db.scalar(select(func.count(Preference.id))) > 0
        assert settings_service.is_voting_open(db)
    assert "organiser" in out and "student8" in out

    assert cli.main(["seed-demo"]) == 1  # refuses to add demo data to a database that has users
    assert "already has users" in capsys.readouterr().err
