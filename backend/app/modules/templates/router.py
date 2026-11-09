"""Template catalogue routes (``/api/v1/templates/*``) + admin upload gate."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session

router = APIRouter(prefix="/templates", tags=["templates"])
admin_router = APIRouter(prefix="/admin/templates", tags=["admin-templates"])


class NotImplementedYet(AppError):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"
    title = "Endpoint not implemented yet"


@router.get("")
async def list_published_templates(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """List PUBLISHED templates with their current version pointers."""
    raise NotImplementedYet("Template listing pending templates service")


@router.get("/{slug}")
async def get_template_by_slug(
    slug: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    raise NotImplementedYet("Template detail pending templates service")


@admin_router.post("/{template_id}/versions", status_code=status.HTTP_201_CREATED)
async def upload_template_version(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Admin-only multipart blank-PDF upload.

    The PrivacyBoundaryMiddleware ADMIN_TEMPLATE_UPLOAD profile has already
    enforced the bearer token, ``multipart/form-data``, and 10 MB cap before
    this handler runs. Downstream validation (``%PDF-1.`` magic, zero /JS,
    /Launch, /EmbeddedFiles, empty AcroForm /V) happens here.
    """
    raise NotImplementedYet("Admin template version upload pending templates service")
