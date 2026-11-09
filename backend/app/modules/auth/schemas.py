"""Auth request/response schemas (citizen routes — 4 KB JSON cap)."""

from __future__ import annotations

import uuid

from pydantic import Field

from app.core.constants import ClientPlatform, OtpChannel, OtpPurpose, UserRole
from app.core.schemas import ResponseModel, StrictModel

# E.164 mobile; lenient length bound so the 4 KB middleware cap is the real gate.
_MOBILE_PATTERN = r"^\+[1-9]\d{6,14}$"


class OtpStartRequest(StrictModel):
    channel: OtpChannel
    purpose: OtpPurpose = OtpPurpose.LOGIN_OR_REGISTER
    mobile_e164: str | None = Field(default=None, pattern=_MOBILE_PATTERN)
    email: str | None = Field(default=None, max_length=254)
    platform: ClientPlatform = ClientPlatform.WEB


class OtpStartResponse(ResponseModel):
    challenge_id: uuid.UUID
    expires_in_seconds: int
    resend_cooldown_seconds: int


class OtpVerifyRequest(StrictModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=4, max_length=8, pattern=r"^\d{4,8}$")
    device_fingerprint_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    platform: ClientPlatform = ClientPlatform.WEB


class TokenPairResponse(ResponseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    access_expires_in_seconds: int
    role: UserRole
    is_mobile_verified: bool


class RefreshRequest(StrictModel):
    refresh_token: str = Field(min_length=10, max_length=512)


class LogoutRequest(StrictModel):
    refresh_token: str = Field(min_length=10, max_length=512)
