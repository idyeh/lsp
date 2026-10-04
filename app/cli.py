"""Command-line tasks.

    python -m app.cli init-db
    python -m app.cli create-user USERNAME --display-name "Full Name" [--role organiser] [--year-group UG2]
    python -m app.cli import-topics [FILE] [--translations FILE] [--created-by USERNAME]
    python -m app.cli seed-demo [--topics-file FILE]
"""

import argparse
import getpass
import random
import secrets
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.config import PROJECT_ROOT, database_url_from_env
from app.database import init_db, make_engine, make_sessionmaker
from app.enums import Role, TopicType
from app.errors import AppError
from app.models import User
from app.services import preferences, topics, users
from app.services import settings as settings_service
from app.services.preferences import PreferenceChange
from app.services.topic_import import ImportReport, import_topics

# Sample topics shipped outside version control (docs/ is part of the repository, this file is not).
DEFAULT_TOPICS_FILE = PROJECT_ROOT / "docs" / "sample_topics.json"
DEMO_PRESENTING = ["none"] * 6 + ["maybe", "maybe", "co_present", "lead"]


def _open_db():
    engine = make_engine(database_url_from_env())
    init_db(engine)  # idempotent: creates missing tables only
    return engine, make_sessionmaker(engine)


def _translations_file(topics_file: Path, explicit: str | None) -> Path:
    """sample_topics.json -> sample_topics.zh.json unless a file is named explicitly."""
    return Path(explicit) if explicit else topics_file.with_suffix(".zh.json")


def _print_report(report: ImportReport, translations_file: Path) -> None:
    print(f"Topics: {len(report.created)} created, {len(report.skipped)} already present.")
    if report.categories_added:
        print(f"Categories added: {', '.join(report.categories_added)}")
    if report.translated:
        print(f"Translations added to existing topics: {len(report.translated)}")
    if not translations_file.exists():
        print(f"No translations file at {translations_file}; topics are English only.")
    for title in report.untranslated:
        print(f"  no translation for: {title}")
    for title in report.unmatched_translations:
        print(f"  translation matches no topic in the file: {title}")


def cmd_init_db(_args) -> None:
    engine, _ = _open_db()
    print(f"Database ready at {engine.url.render_as_string(hide_password=True)}")


def cmd_create_user(args) -> None:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise AppError(422, "VALIDATION_ERROR", "The passwords do not match.")
    _, sessions = _open_db()
    with sessions() as db:
        user = users.create_user(db, args.username, args.display_name, password, Role(args.role), args.year_group)
        print(f"Created {user.role.value} '{user.username}'.")


def cmd_import_topics(args) -> None:
    topics_file = Path(args.file) if args.file else DEFAULT_TOPICS_FILE
    translations_file = _translations_file(topics_file, args.translations)
    _, sessions = _open_db()
    with sessions() as db:
        if args.created_by:
            actor = db.scalar(select(User).where(User.username == users.normalise_username(args.created_by)))
        else:
            actor = db.scalar(select(User).where(User.role == Role.organiser, User.is_active.is_(True)).order_by(User.id))
        if actor is None:
            raise AppError(404, "USER_NOT_FOUND", "No organiser found to own the topics; create one first (create-user).")
        _print_report(import_topics(db, actor, topics_file, translations_file), translations_file)


def cmd_seed_demo(args) -> None:
    """Fill an empty database with sample users, topics and votes so the app can be tried out."""
    topics_file = Path(args.topics_file) if args.topics_file else DEFAULT_TOPICS_FILE
    translations_file = _translations_file(topics_file, args.translations)
    if not topics_file.exists():
        raise AppError(
            404, "IMPORT_ERROR", f"Sample topics not found at {topics_file}. Put the file there or pass --topics-file."
        )
    _, sessions = _open_db()
    with sessions() as db:
        if db.scalar(select(func.count(User.id))):
            raise AppError(409, "CONFLICT", "The database already has users; demo data is only added to an empty one.")
        credentials = []
        accounts = [("organiser", "Demo Organiser", Role.organiser, None)] + [
            (f"student{i}", f"Demo Student {i}", Role.student, ("UG2", "UG3", "PG1")[i % 3]) for i in range(1, 9)
        ]
        for username, name, role, year in accounts:
            password = secrets.token_urlsafe(9)
            users.create_user(db, username, name, password, role, year)
            credentials.append((username, password))
        _print_report(import_topics(db, users.get_user(db, 1), topics_file, translations_file), translations_file)
        settings_service.update(db, {"voting_open": True, "semester_name": "Demo semester", "semester_name_zh": "演示学期"})

        rng = random.Random(2026)  # fixed seed: the demo votes are the same every time
        pool = topics.list_pool(db)
        for user in users.list_users(db):
            changes = [
                PreferenceChange(
                    t.id,
                    rng.randint(0, 3) if t.topic_type == TopicType.optional else 0,
                    rng.choice(DEMO_PRESENTING),
                )
                for t in pool
            ]
            preferences.save_preferences(db, user, changes)

    print("Demo data created. Log in with these accounts (passwords are random and shown only now):")
    for username, password in credentials:
        print(f"  {username:<10} {password}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db", help="create the database tables").set_defaults(run=cmd_init_db)
    create = commands.add_parser("create-user", help="create an account (prompts for the password)")
    create.add_argument("username")
    create.add_argument("--display-name", required=True)
    create.add_argument("--role", choices=[r.value for r in Role], default=Role.student.value)
    create.add_argument("--year-group")
    create.set_defaults(run=cmd_create_user)
    imp = commands.add_parser("import-topics", help="import topics (and translations) from JSON files")
    imp.add_argument("file", nargs="?", help=f"topics file (default: {DEFAULT_TOPICS_FILE.relative_to(PROJECT_ROOT)})")
    imp.add_argument("--translations", help="translations file (default: the topics file name with .zh.json)")
    imp.add_argument("--created-by", help="username that will own the topics (default: the first organiser)")
    imp.set_defaults(run=cmd_import_topics)
    demo = commands.add_parser("seed-demo", help="add sample users, topics and votes to an empty database")
    demo.add_argument("--topics-file", help=f"topics file (default: {DEFAULT_TOPICS_FILE.relative_to(PROJECT_ROOT)})")
    demo.add_argument("--translations", help="translations file (default: the topics file name with .zh.json)")
    demo.set_defaults(run=cmd_seed_demo)

    args = parser.parse_args(argv)
    try:
        args.run(args)
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
