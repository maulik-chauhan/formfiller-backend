"""Payment routes (``/api/v1/payments/*``) incl. the signed Razorpay webhook."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session

router = APIRouter(prefix="/payments", tags=["payments"])


class NotImplementedYet(AppError):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"
    title = "Endpoint not implemented yet"


@router.post("/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Create a Razorpay order from a ``pricing_catalog`` SKU snapshot."""
    raise NotImplementedYet("Order creation pending payment service")


@router.post("/webhooks/razorpay", status_code=status.HTTP_200_OK)
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Idempotent, HMAC-verified webhook sink (SIGNED_WEBHOOK profile, 64 KB)."""
    raise NotImplementedYet("Webhook processing pending payment service")
