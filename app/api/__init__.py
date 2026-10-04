"""JSON API, mounted under /api (SRS section 8)."""

from fastapi import APIRouter

from app.api import auth, preferences, proposals, results, sessions, settings, topics

api_router = APIRouter(prefix="/api")
for module in (auth, topics, proposals, preferences, results, sessions, settings):
    api_router.include_router(module.router)
