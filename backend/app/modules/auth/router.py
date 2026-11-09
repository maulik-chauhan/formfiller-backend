"""Auth API routes (``/api/v1/auth/*``).

Handlers are wired to the service layer where implemented and otherwise raise
``501 Not Implemented`` so the surface is explicit and testable. Full OTP/JWT
flow lands in the auth vertical slice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session
from app.modules.auth import schemas

router = APIRouter(prefix="/auth", tags=["auth"])


class NotImplementedYet(AppError):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"
    title = "Endpoint not implemented yet"


@router.post("/otp/start", response_model=schemas.OtpStartResponse)
async def otp_start(
    payload: schemas.OtpStartRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> schemas.OtpStartResponse:
    """Issue a keyed-HMAC OTP challenge (rate-limited by target + IP subnet)."""
    raise NotImplementedYet("OTP start pending auth service implementation")


@router.post("/otp/verify", response_model=schemas.TokenPairResponse)
async def otp_verify(
    payload: schemas.OtpVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> schemas.TokenPairResponse:
    """Verify an OTP; a ``MOBILE_SMS`` success flips ``is_mobile_verified=TRUE``."""
    raise NotImplementedYet("OTP verify pending auth service implementation")


@router.post("/token/refresh", response_model=schemas.TokenPairResponse)
async def refresh_token(
    payload: schemas.RefreshRequest,
    session: AsyncSession = Depends(get_session),
) -> schemas.TokenPairResponse:
    """Rotate a refresh token within its family; detect reuse."""
    raise NotImplementedYet("Token refresh pending auth service implementation")


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: schemas.LogoutRequest,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Revoke the presented refresh token's session."""
    raise NotImplementedYet("Logout pending auth service implementation")
