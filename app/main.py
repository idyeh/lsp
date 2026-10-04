"""Application factory and error handling."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.api import api_router
from app.config import Settings, load_settings
from app.database import init_db, make_engine, make_sessionmaker
from app.errors import AppError
from app.web import web_router

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"


def _error_body(code: str, message: str, details: list | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def _is_api(request: Request) -> bool:
    return request.url.path.startswith("/api/") or request.url.path == "/api"


def _respond(request: Request, status_code: int, code: str, message: str, details: list | None = None):
    """JSON error body for the API; redirect or HTML error page for the web UI."""
    if _is_api(request):
        return JSONResponse(_error_body(code, message, details), status_code=status_code)
    if status_code == 401:
        return RedirectResponse("/login", status_code=303)
    from app.web.common import render_error  # imported lazily: the web layer imports this package

    return render_error(request, status_code, message)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError):
        return _respond(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        details = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]} for err in exc.errors()
        ]
        return _respond(request, 422, "VALIDATION_ERROR", "The request data is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return _respond(request, exc.status_code, codes.get(exc.status_code, "HTTP_ERROR"), str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        # Details go to the log only; the client gets a generic message.
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _respond(request, 500, "INTERNAL_ERROR", "Something went wrong on the server.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(app.state.engine)  # creates missing tables only; never alters or drops existing ones
    logging.getLogger("uvicorn.error").info("Using database %s", app.state.engine.url.render_as_string(hide_password=True))
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(
        title="Lab Seminar Planner",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=settings.session_max_age,
        same_site="lax",
        https_only=settings.cookie_secure,
    )
    register_error_handlers(app)
    app.include_router(api_router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    return app
