"""Command-line tasks.

    python -m app.cli init-db
    python -m app.cli create-user USERNAME --display-name "Full Name" [--role organiser] [--year-group UG2]
    python -m app.cli seed-demo
"""

import argparse
import getpass
import random
import secrets
import sys

from sqlalchemy import func, select

from app.config import database_url_from_env
from app.database import init_db, make_engine, make_sessionmaker
from app.enums import Role, TopicStatus, TopicType
from app.errors import AppError
from app.models import User
from app.schemas import TopicCreate
from app.services import preferences, topics, users
from app.services import settings as settings_service
from app.services.preferences import PreferenceChange

DEMO_TOPICS = [
    ("Git and Collaborative Workflows", "software-engineering", TopicType.core, "git, branching, code review"),
    ("Reading and Writing Research Papers", "research-methods", TopicType.core, "papers, writing, literature"),
    ("Designing Reproducible Experiments", "research-methods", TopicType.core, "experiments, reproducibility"),
    ("Presenting Your Work Clearly", "research-methods", TopicType.core, "slides, talks, feedback"),
    ("Can AI Become Part of a Game World?", "game-development", TopicType.optional, "NPC, LLM, memory"),
    ("Generative Interactive Film", "generative-media", TopicType.optional, "narrative, video, interaction"),
    ("Vision Models in Practice", "computer-vision", TopicType.optional, "detection, segmentation"),
    ("Robot Navigation Basics", "robotics", TopicType.optional, "SLAM, planning"),
    ("Lessons from Competitions", "competition-experience", TopicType.optional, "teamwork, deadlines"),
    ("Prompting and Evaluating LLMs", "artificial-intelligence", TopicType.optional, "prompts, evaluation"),
    ("Prototyping Interactive Story Tools", "interactive-film", TopicType.optional, "authoring, branching"),
]
DEMO_PRESENTING = ["none"] * 6 + ["maybe", "maybe", "co_present", "lead"]


def _open_db():
    engine = make_engine(database_url_from_env())
    init_db(engine)  # idempotent: creates missing tables only
    return engine, make_sessionmaker(engine)


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


def cmd_seed_demo(_args) -> None:
    """Fill an empty database with sample users, topics and votes so the app can be tried out."""
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
        organiser = users.get_user(db, 1)
        for title, category, topic_type, keywords in DEMO_TOPICS:
            data = TopicCreate(
                title=title,
                description=f"Demo topic: {title}.",
                category=category,
                topic_type=topic_type,
                keywords=keywords.split(","),
                status=TopicStatus.active,
            )
            topics.create_topic(db, data, organiser)
        settings_service.update(db, {"voting_open": True, "semester_name": "Demo semester"})

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
    commands.add_parser("seed-demo", help="add sample users, topics and votes to an empty database").set_defaults(
        run=cmd_seed_demo
    )

    args = parser.parse_args(argv)
    try:
        args.run(args)
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
