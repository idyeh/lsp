"""Engine and session factory. SQLite is the only supported database."""

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    in_memory = url.database in (None, "", ":memory:")
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if in_memory:
        kwargs["poolclass"] = StaticPool  # one shared connection, otherwise each sees an empty DB
    else:
        Path(url.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, **kwargs)

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys by default
        if not in_memory:
            cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine)


def init_db(engine: Engine) -> None:
    """Create all tables. Safe to run repeatedly; existing tables are left untouched."""
    from app import models  # noqa: F401  (registers the tables on Base.metadata)

    Base.metadata.create_all(engine)
