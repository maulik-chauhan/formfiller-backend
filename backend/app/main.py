"""FastAPI application factory wiring middleware, routers and error handlers."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.routing import APIRoute
from sqlalchemy import text

from app.config import get_settings
from app.core.dependencies import get_auth_context
from app.core.errors import register_exception_handlers
from app.db.session import engine
from app.middleware.privacy_boundary import PrivacyBoundaryMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.entitlements.router import router as entitlements_router
from app.modules.esign_gateway.router import router as esign_router
from app.modules.payments.router import router as payments_router
from app.modules.templates.router import admin_router as templates_admin_router
from app.modules.templates.router import router as templates_router

#: The complete browser-facing CORS surface. Credentials are never allowed: auth is
#: a bearer token in the Authorization header, so clients use ``credentials: "omit"``.
CORS_ALLOWED_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]
CORS_ALLOWED_HEADERS = ["Authorization", "Content-Type", "Idempotency-Key", "X-Request-Id"]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    # Fail fast if a shared environment is missing mandatory, distinct secrets.
    get_settings().validate_production_secrets()
    yield


def _requires_auth(dependant: Dependant) -> bool:
    """True when the route's dependency tree funnels through ``get_auth_context``."""
    if dependant.call is get_auth_context:
        return True
    return any(_requires_auth(child) for child in dependant.dependencies)


def _install_openapi(app: FastAPI, api_routes: list[tuple[str, APIRoute]]) -> None:
    """Declare ``BearerAuth`` on exactly the operations that enforce it at runtime."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        for path, route in api_routes:
            if not _requires_auth(route.dependant):
                continue
            operations = schema["paths"].get(path, {})
            for method in route.methods or ():
                operation = operations.get(method.lower())
                if operation is not None:
                    operation["security"] = [{"BearerAuth": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def _configure_dev_logging() -> None:
    """Surface ``app.*`` INFO logs locally (e.g. the console OTP provider).

    Only called outside shared environments. Application loggers never receive
    request bodies, tokens or applicant data; in production the console OTP
    provider is inert regardless of logging configuration.
    """
    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s - %(message)s"))
        app_logger.addHandler(handler)
        app_logger.propagate = False
    app_logger.setLevel(logging.INFO)


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.environment == "development":
        _configure_dev_logging()
    app = FastAPI(
        title="Indian Government Forms Automation Platform — Backend",
        version="2.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Zero-knowledge privacy boundary: every request body is size-capped and
    # inspected before it can reach a route handler.
    app.add_middleware(PrivacyBoundaryMiddleware)

    # CORS is added last so it is the OUTERMOST layer: preflight is answered
    # without touching the privacy boundary, and boundary rejections (413/415/422)
    # still carry CORS headers so the browser can read the problem document.
    # No origins configured => no CORS headers at all.
    origins = settings.cors_allowed_origins_list
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=CORS_ALLOWED_METHODS,
            allow_headers=CORS_ALLOWED_HEADERS,
            expose_headers=["Retry-After"],
            max_age=600,
        )
    register_exception_handlers(app)

    prefix = settings.api_v1_prefix
    routers = (
        auth_router,
        templates_router,
        templates_admin_router,
        entitlements_router,
        payments_router,
        esign_router,
    )
    for router in routers:
        app.include_router(router, prefix=prefix)

    @app.get(f"{prefix}/health/live", tags=["ops"])
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name, "version": "2.1.0"}

    @app.get(f"{prefix}/health/ready", tags=["ops"])
    async def health_ready() -> dict[str, str]:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ready"}

    # Back-compat liveness alias.
    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name, "version": "2.1.0"}

    # The complete, flat list of ``(full path, route)`` — independent of how the
    # framework nests included routers. Used for OpenAPI security and by the
    # privacy-surface tests that pin the route inventory.
    api_routes: list[tuple[str, APIRoute]] = [
        (prefix + route.path, route)
        for router in routers
        for route in router.routes
        if isinstance(route, APIRoute)
    ]
    api_routes += [(route.path, route) for route in app.routes if isinstance(route, APIRoute)]
    app.state.api_routes = api_routes
    _install_openapi(app, api_routes)
    return app


app = create_app()
