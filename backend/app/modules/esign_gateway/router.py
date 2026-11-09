"""eSign routes (``/api/v1/esign/*``) — gated OFF until Milestone 7."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status

from app.config import get_settings
from app.core.errors import AppError

router = APIRouter(prefix="/esign", tags=["esign"])


class EsignDisabled(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "ESIGN_MODULE_DISABLED"
    title = "eSign module is disabled"


class NotImplementedYet(AppError):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"
    title = "Endpoint not implemented yet"


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_esign_session() -> dict[str, Any]:
    """Start a hash-only eSign session (Milestone 7)."""
    if not get_settings().esign_module_enabled:
        raise EsignDisabled("eSign is disabled until Milestone 7 compliance review")
    raise NotImplementedYet("eSign session creation pending Milestone 7")
