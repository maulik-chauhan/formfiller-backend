"""Privacy & security tests: no PII/secret leakage, ownership enforcement."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.models import AuditLog
from app.modules.auth.models import OtpChallenge, User
from app.modules.auth.sms_provider import get_mock_provider

pytestmark = pytest.mark.asyncio

MOBILE = "+919812345678"
MOBILE2 = "+919800000011"
DFP = "a" * 64
DFP2 = "b" * 64


async def _request_otp(
    client: AsyncClient, mobile: str = MOBILE, purpose: str | None = None
) -> tuple[str, str]:
    body: dict[str, Any] = {"mobile_e164": mobile}
    if purpose:
        body["purpose"] = purpose
    resp = await client.post("/api/v1/auth/otp/request", json=body)
    assert resp.status_code == 202
    return resp.json()["challenge_id"], get_mock_provider().get_last_code(mobile) or ""


async def _login(client: AsyncClient, mobile: str = MOBILE, dfp: str = DFP) -> dict[str, Any]:
    cid, code = await _request_otp(client, mobile)
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "challenge_id": cid,
            "otp_code": code,
            "device_fingerprint_hash": dfp,
            "platform": "WEB",
        },
    )
    assert resp.status_code == 200
    return resp.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_no_raw_mobile_number_in_database(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client)
    bare = MOBILE.lstrip("+")
    for model in (User, OtpChallenge, AuditLog):
        rows = (await db_session.execute(select(model))).scalars().all()
        for row in rows:
            dumped = str(row.__dict__)
            assert MOBILE not in dumped
            assert bare not in dumped


async def test_no_plaintext_otp_in_database(client: AsyncClient, db_session: AsyncSession) -> None:
    cid, code = await _request_otp(client)
    challenge = await db_session.get(OtpChallenge, uuid.UUID(cid))
    assert challenge is not None
    assert challenge.otp_code_hmac != code
    assert len(challenge.otp_code_hmac) == 64
    assert code not in challenge.otp_code_hmac


async def test_no_token_or_otp_leak_in_error_response(client: AsyncClient) -> None:
    cid, _ = await _request_otp(client)
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "challenge_id": cid,
            "otp_code": "000000",
            "device_fingerprint_hash": DFP,
            "platform": "WEB",
        },
    )
    assert resp.status_code == 401
    body = resp.text
    assert "000000" not in body  # submitted OTP never echoed


async def test_no_pii_in_validation_error(client: AsyncClient) -> None:
    # extra="forbid": the secret-bearing extra field value must not be echoed.
    resp = await client.post(
        "/api/v1/auth/otp/request",
        json={"mobile_e164": MOBILE, "leaked_secret": "TOPSECRET-VALUE-123"},
    )
    assert resp.status_code == 422
    assert "TOPSECRET-VALUE-123" not in resp.text


async def test_refresh_token_hash_never_exposed_in_session_list(client: AsyncClient) -> None:
    data = await _login(client)
    listed = await client.get("/api/v1/auth/sessions", headers=_auth(data["access_token"]))
    assert listed.status_code == 200
    assert "refresh_token" not in listed.text
    assert "sha256" not in listed.text.lower()


async def test_cannot_revoke_another_users_session(client: AsyncClient) -> None:
    user_a = await _login(client, MOBILE, DFP)
    user_b = await _login(client, MOBILE2, DFP2)

    a_sessions = await client.get("/api/v1/auth/sessions", headers=_auth(user_a["access_token"]))
    a_session_id = a_sessions.json()["sessions"][0]["session_id"]

    # User B attempts to revoke user A's session → not found (ownership enforced).
    resp = await client.delete(
        f"/api/v1/auth/sessions/{a_session_id}", headers=_auth(user_b["access_token"])
    )
    assert resp.status_code == 404

    # A's session remains active.
    still = await client.get("/api/v1/auth/sessions", headers=_auth(user_a["access_token"]))
    assert still.status_code == 200


async def test_unauthenticated_requests_rejected(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/sessions")).status_code == 401
    assert (await client.post("/api/v1/auth/logout", json={})).status_code == 401


async def test_audit_log_metadata_has_no_pii(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login(client)
    rows = (await db_session.execute(select(AuditLog))).scalars().all()
    assert rows  # events were recorded
    event_types = {r.event_type for r in rows}
    assert "AUTH_OTP_REQUESTED" in event_types
    assert "AUTH_LOGIN_SUCCESS" in event_types
    for row in rows:
        serialized = str(row.metadata_jsonb)
        assert MOBILE not in serialized
        assert "otp" not in serialized.lower()
        assert (
            "token" not in str(row.metadata_jsonb).lower()
            or "token_family_id" in row.metadata_jsonb
        )
