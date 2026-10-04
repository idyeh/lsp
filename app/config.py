"""Runtime configuration, read from environment variables."""

import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Anchored to the project folder so the same database is used wherever the server is started from.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_URL = f"sqlite:///{PROJECT_ROOT / 'data' / 'seminar.db'}"
DEFAULT_SESSION_MAX_AGE = 7 * 24 * 3600  # seconds


@dataclass(frozen=True)
class Settings:
    database_url: str
    secret_key: str
    cookie_secure: bool  # set the Secure flag on the session cookie (use with HTTPS)
    session_max_age: int


def database_url_from_env() -> str:
    return os.environ.get("LSP_DATABASE_URL", DEFAULT_DATABASE_URL)


def load_settings() -> Settings:
    secret_key = os.environ.get("LSP_SECRET_KEY")
    if not secret_key:
        secret_key = secrets.token_urlsafe(32)
        logger.warning(
            "LSP_SECRET_KEY is not set; using a random key. "
            "Everyone will be logged out whenever the server restarts."
        )
    return Settings(
        database_url=database_url_from_env(),
        secret_key=secret_key,
        cookie_secure=os.environ.get("LSP_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        session_max_age=int(os.environ.get("LSP_SESSION_MAX_AGE", DEFAULT_SESSION_MAX_AGE)),
    )
