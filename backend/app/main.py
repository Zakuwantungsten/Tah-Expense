from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from .api import api, merged_settings
from .config import Settings, get_settings
from .database import Database
from .errors import ApiError, install_error_handlers, payload
from .security_policy import docs_enabled, enforce_request_allowlists


def create_app(settings: Settings | None = None, database: Database | None = None) -> FastAPI:
    configured = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = database is None
        app.state.database = database or Database(configured)
        try:
            yield
        finally:
            if owned:
                await app.state.database.close()

    # Docs routes are always registered; middleware gates them via
    # security_docs_enabled / TAHMEED_DOCS_ENABLED.
    app = FastAPI(
        title="Tahmeed Expense API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = configured
    if configured.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=configured.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.middleware("http")
    async def security_policy(request: Request, call_next):
        if not getattr(request.state, "request_id", None):
            request.state.request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        path = request.url.path
        try:
            db = request.app.state.database.db
            settings_map = await merged_settings(db)
        except Exception:
            from .api import SETTINGS_DEFAULTS

            settings_map = dict(SETTINGS_DEFAULTS)

        if path in {"/docs", "/openapi.json", "/redoc"} and not docs_enabled(settings_map):
            return JSONResponse(
                status_code=404,
                content=payload(
                    "docs_disabled",
                    "API documentation is disabled",
                    request,
                ),
            )

        try:
            await enforce_request_allowlists(request, settings_map)
        except ApiError as exc:
            return JSONResponse(
                status_code=exc.status,
                content=payload(exc.code, exc.message, request, exc.details),
            )
        return await call_next(request)

    @app.get("/health/live", tags=["health"])
    async def live() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready(request: Request) -> dict:
        try:
            await request.app.state.database.ping()
        except PyMongoError:
            raise ApiError(503, "database_unavailable", "Database is not ready") from None
        return {"status": "ready"}

    app.include_router(api)
    install_error_handlers(app)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, proxy_headers=True)
