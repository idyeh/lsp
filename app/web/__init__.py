"""Server-rendered web UI (Jinja2 templates, plain HTML forms)."""

from fastapi import APIRouter

from app.web import auth, organiser, student

web_router = APIRouter(include_in_schema=False)
for module in (auth, student, organiser):
    web_router.include_router(module.router)
