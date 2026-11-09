"""Authentication API routes (``/api/v1/auth/*``)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
    AuthContext,
    RequestContext,
    get_request_context,
    require_authenticated_user,
)
from app.db.session import get_session
from app.modules.auth import schemas
from app.modules.auth.service import AuthService, IssuedAuth
from app.modules.auth.sms_provider import OtpDeliveryProvider, get_otp_provider

router = APIRouter(prefix="/auth", tags=["auth"])


def _service(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
    provider: OtpDeliveryProvider = Depends(get_otp_provider),
) -> AuthService:
    return AuthService(session, ctx=ctx, otp_provider=provider)


def _tokens_response(service: AuthService, issued: IssuedAuth) -> schemas.AuthTokensResponse:
    wallet = issued.wallet
    remaining = max(
        wallet.free_quota_total - wallet.free_quota_used - wallet.free_quota_reserved, 0
    )
    return schemas.AuthTokensResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in_sec=service.settings.access_token_ttl_minutes * 60,
        user_id=issued.user.id,
        wallet_summary=schemas.WalletSummary(
            free_quota_total=wallet.free_quota_total,
            free_quota_used=wallet.free_quota_used,
            free_quota_remaining=remaining,
        ),
    )


@router.post(
    "/otp/request",
    response_model=schemas.OtpChallengeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_otp(
    payload: schemas.OtpRequestPayload,
    service: AuthService = Depends(_service),
) -> schemas.OtpChallengeResponse:
    challenge = await service.request_otp(payload)
    return schemas.OtpChallengeResponse(
        challenge_id=challenge.id,
        expires_in_sec=service.settings.otp_ttl_seconds,
        resend_cooldown_sec=service.settings.otp_resend_cooldown_seconds,
    )


@router.post("/otp/verify", response_model=schemas.AuthTokensResponse)
async def verify_otp(
    payload: schemas.OtpVerifyPayload,
    service: AuthService = Depends(_service),
) -> schemas.AuthTokensResponse:
    issued = await service.verify_otp(payload)
    return _tokens_response(service, issued)


@router.post("/token/refresh", response_model=schemas.AuthTokensResponse)
async def refresh_token(
    payload: schemas.TokenRefreshPayload,
    service: AuthService = Depends(_service),
) -> schemas.AuthTokensResponse:
    issued = await service.refresh(payload)
    return _tokens_response(service, issued)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: schemas.LogoutPayload,
    auth: AuthContext = Depends(require_authenticated_user),
    service: AuthService = Depends(_service),
) -> Response:
    await service.logout(auth, revoke_all_devices=payload.revoke_all_devices)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions", response_model=schemas.ActiveSessionListResponse)
async def list_sessions(
    auth: AuthContext = Depends(require_authenticated_user),
    service: AuthService = Depends(_service),
) -> schemas.ActiveSessionListResponse:
    sessions = await service.list_sessions(auth)
    return schemas.ActiveSessionListResponse(sessions=sessions)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(require_authenticated_user),
    service: AuthService = Depends(_service),
) -> Response:
    await service.revoke_session(auth, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/account", response_model=schemas.AccountDeletedReceipt)
async def delete_account(
    payload: schemas.AccountDeletePayload,
    auth: AuthContext = Depends(require_authenticated_user),
    service: AuthService = Depends(_service),
) -> schemas.AccountDeletedReceipt:
    result = await service.delete_account(auth, payload)
    return schemas.AccountDeletedReceipt(
        user_id=result.user_id,
        deleted_at=result.deleted_at,
        revoked_session_count=result.revoked_session_count,
        free_quota_tombstoned=result.free_quota_tombstoned,
        tombstone_expires_at=result.tombstone_expires_at,
    )
