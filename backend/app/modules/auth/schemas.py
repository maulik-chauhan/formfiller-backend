"""Auth request/response schemas (citizen routes — 4 KB JSON cap).

All request models inherit ``StrictModel`` (``extra='forbid'``). No schema ever
echoes a submitted mobile number, OTP code or token back to the client.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field

from app.core.constants import ClientPlatform, OtpPurpose, UserRole
from app.core.schemas import ResponseModel, StrictModel

_HEX64 = r"^[a-f0-9]{64}$"
_OTP = r"^\d{6}$"
_MOBILE_E164 = r"^\+91[6-9]\d{9}$"


# --------------------------------------------------------------------------- #
# OTP request / verify
# --------------------------------------------------------------------------- #
class OtpRequestPayload(StrictModel):
    mobile_e164: str = Field(pattern=_MOBILE_E164, max_length=16)
    purpose: OtpPurpose = OtpPurpose.LOGIN_OR_REGISTER


class OtpChallengeResponse(ResponseModel):
    challenge_id: uuid.UUID
    expires_in_sec: int
    resend_cooldown_sec: int


class OtpVerifyPayload(StrictModel):
    challenge_id: uuid.UUID
    otp_code: str = Field(pattern=_OTP)
    device_fingerprint_hash: str = Field(pattern=_HEX64)
    platform: ClientPlatform = ClientPlatform.WEB


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #
class WalletSummary(ResponseModel):
    free_quota_total: int
    free_quota_used: int
    free_quota_remaining: int


class AuthTokensResponse(ResponseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in_sec: int
    user_id: uuid.UUID
    #: The account's own role, so the client can show the admin console entry.
    #: Display only — every admin route re-checks the role server-side.
    role: UserRole
    wallet_summary: WalletSummary


class TokenRefreshPayload(StrictModel):
    refresh_token: str = Field(min_length=20, max_length=512)
    device_fingerprint_hash: str = Field(pattern=_HEX64)


# --------------------------------------------------------------------------- #
# Sessions / logout
# --------------------------------------------------------------------------- #
class LogoutPayload(StrictModel):
    revoke_all_devices: bool = False


class SessionInfo(ResponseModel):
    session_id: uuid.UUID
    platform: ClientPlatform
    device_fingerprint_hash: str
    created_at: dt.datetime
    last_used_at: dt.datetime
    expires_at: dt.datetime
    is_current: bool


class ActiveSessionListResponse(ResponseModel):
    sessions: list[SessionInfo]


# --------------------------------------------------------------------------- #
# Account deletion
# --------------------------------------------------------------------------- #
class AccountDeletePayload(StrictModel):
    otp_challenge_id: uuid.UUID
    otp_code: str = Field(pattern=_OTP)
    confirmation: Literal["DELETE_MY_ACCOUNT"]


class AccountDeletedReceipt(ResponseModel):
    user_id: uuid.UUID
    deleted_at: dt.datetime
    revoked_session_count: int
    free_quota_tombstoned: int
    tombstone_expires_at: dt.datetime


# --------------------------------------------------------------------------- #
# Internal role note (kept for OpenAPI completeness; not a request body)
# --------------------------------------------------------------------------- #
class RoleInfo(ResponseModel):
    role: UserRole
