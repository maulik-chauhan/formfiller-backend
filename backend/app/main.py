"""FastAPI application factory wiring middleware, routers and error handlers."""

from __future__ import annotations

from fastapi import FastAPI

from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.middleware.privacy_boundary import PrivacyBoundaryMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.entitlements.router import router as entitlements_router
from app.modules.esign_gateway.router import router as esign_router
from app.modules.payments.router import router as payments_router
from app.modules.templates.router import admin_router as templates_admin_router
from app.modules.templates.router import router as templates_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Indian Government Forms Automation Platform — Backend",
        version="2.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    # Zero-knowledge privacy boundary runs first (outermost ASGI layer).
    app.add_middleware(PrivacyBoundaryMiddleware)
    register_exception_handlers(app)

    prefix = settings.api_v1_prefix
    app.include_router(auth_router, prefix=prefix)
    app.include_router(templates_router, prefix=prefix)
    app.include_router(templates_admin_router, prefix=prefix)
    app.include_router(entitlements_router, prefix=prefix)
    app.include_router(payments_router, prefix=prefix)
    app.include_router(esign_router, prefix=prefix)

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.app_name, "version": "2.1.0"}

    return app


app = create_app()
