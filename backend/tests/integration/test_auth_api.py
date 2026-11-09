"""End-to-end auth API tests against PostgreSQL (OTP, sessions, deletion)."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.hashing import mobile_blind_index
from app.modules.auth.models import OtpChallenge, User
from app.modules.auth.sms_provider import get_mock_provider
from app.modules.entitlements.models import EntitlementLedgerEntry, EntitlementWallet

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
    assert resp.status_code == 202, resp.text
    challenge_id = resp.json()["challenge_id"]
    code = get_mock_provider().get_last_code(mobile)
    assert code is not None
    return challenge_id, code


async def _login(client: AsyncClient, mobile: str = MOBILE, dfp: str = DFP) -> dict[str, Any]:
    challenge_id, code = await _request_otp(client, mobile)
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": code,
            "device_fingerprint_hash": dfp,
            "platform": "WEB",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# Registration & login
# --------------------------------------------------------------------------- #
async def test_new_user_registration_initializes_wallet(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    data = await _login(client)
    assert data["wallet_summary"]["free_quota_total"] == 2  # configured in test env
    assert data["wallet_summary"]["free_quota_used"] == 0
    assert data["wallet_summary"]["free_quota_remaining"] == 2
    assert data["token_type"] == "Bearer"
    assert data["expires_in_sec"] == 900

    user = await db_session.get(User, uuid.UUID(data["user_id"]))
    assert user is not None
    assert user.is_mobile_verified is True
    assert user.mobile_verified_at is not None


async def test_existing_user_login_does_not_create_duplicate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    first = await _login(client)
    second = await _login(client)
    assert first["user_id"] == second["user_id"]
    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_blind_index_uniqueness_across_mobiles(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, MOBILE)
    await _login(client, MOBILE2, dfp=DFP2)
    indexes = (await db_session.execute(select(User.mobile_blind_index))).scalars().all()
    assert len(indexes) == 2
    assert len(set(indexes)) == 2


async def test_wallet_total_follows_configured_setting(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    from app.config import get_settings

    data = await _login(client)
    wallet = await db_session.get(EntitlementWallet, uuid.UUID(data["user_id"]))
    assert wallet is not None
    assert wallet.free_quota_total == get_settings().resolved_free_quota_total


# --------------------------------------------------------------------------- #
# OTP lifecycle
# --------------------------------------------------------------------------- #
async def test_invalid_otp_rejected(client: AsyncClient) -> None:
    challenge_id, _ = await _request_otp(client)
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": "000000",
            "device_fingerprint_hash": DFP,
            "platform": "WEB",
        },
    )
    assert resp.status_code == 401


async def test_expired_otp_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    challenge_id, code = await _request_otp(client)
    challenge = await db_session.get(OtpChallenge, uuid.UUID(challenge_id))
    assert challenge is not None
    challenge.expires_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(seconds=1)
    await db_session.commit()
    resp = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": code,
            "device_fingerprint_hash": DFP,
            "platform": "WEB",
        },
    )
    assert resp.status_code == 401


async def test_three_failed_attempts_locks_challenge(client: AsyncClient) -> None:
    challenge_id, code = await _request_otp(client)
    for _ in range(3):
        bad = await client.post(
            "/api/v1/auth/otp/verify",
            json={
                "challenge_id": challenge_id,
                "otp_code": "000000",
                "device_fingerprint_hash": DFP,
                "platform": "WEB",
            },
        )
        assert bad.status_code == 401
    # Correct code now rejected because the challenge is LOCKED.
    locked = await client.post(
        "/api/v1/auth/otp/verify",
        json={
            "challenge_id": challenge_id,
            "otp_code": code,
            "device_fingerprint_hash": DFP,
            "platform": "WEB",
        },
    )
    assert locked.status_code == 401


async def test_resend_cooldown_blocks_immediate_second_request(client: AsyncClient) -> None:
    await _request_otp(client)
    resp = await client.post("/api/v1/auth/otp/request", json={"mobile_e164": MOBILE})
    assert resp.status_code == 429


async def test_hourly_request_rate_limit(client: AsyncClient, db_session: AsyncSession) -> None:
    target = mobile_blind_index(MOBILE)
    now = dt.datetime.now(tz=dt.UTC)
    for _ in range(5):
        db_session.add(
            OtpChallenge(
                id=uuid.uuid4(),
                target_blind_index=target,
                channel="MOBILE_SMS",
                purpose="LOGIN_OR_REGISTER",
                otp_code_hmac="0" * 64,
                status="VERIFIED",  # consumed, so cooldown does not apply
                client_ip_subnet_hash="f" * 64,
                expires_at=now + dt.timedelta(minutes=5),
                created_at=now - dt.timedelta(minutes=5),
            )
        )
    await db_session.commit()
    resp = await client.post("/api/v1/auth/otp/request", json={"mobile_e164": MOBILE})
    assert resp.status_code == 429


async def test_concurrent_verification_yields_single_success(client: AsyncClient) -> None:
    challenge_id, code = await _request_otp(client)
    payload = {
        "challenge_id": challenge_id,
        "otp_code": code,
        "device_fingerprint_hash": DFP,
        "platform": "WEB",
    }
    r1, r2 = await asyncio.gather(
        client.post("/api/v1/auth/otp/verify", json=payload),
        client.post("/api/v1/auth/otp/verify", json=payload),
        return_exceptions=False,
    )
    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses[0] == 200
    assert statuses[1] != 200  # second is a replay/conflict, never a 2nd success


async def test_otp_replay_prevented(client: AsyncClient) -> None:
    challenge_id, code = await _request_otp(client)
    payload = {
        "challenge_id": challenge_id,
        "otp_code": code,
        "device_fingerprint_hash": DFP,
        "platform": "WEB",
    }
    first = await client.post("/api/v1/auth/otp/verify", json=payload)
    assert first.status_code == 200
    replay = await client.post("/api/v1/auth/otp/verify", json=payload)
    assert replay.status_code == 409


# --------------------------------------------------------------------------- #
# Refresh rotation & reuse detection
# --------------------------------------------------------------------------- #
async def test_refresh_rotation_issues_new_tokens(client: AsyncClient) -> None:
    data = await _login(client)
    resp = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": data["refresh_token"], "device_fingerprint_hash": DFP},
    )
    assert resp.status_code == 200
    rotated = resp.json()
    assert rotated["refresh_token"] != data["refresh_token"]
    assert rotated["access_token"] != data["access_token"]


async def test_old_refresh_reuse_revokes_entire_family(client: AsyncClient) -> None:
    data = await _login(client)
    first_refresh = data["refresh_token"]
    rotated = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": first_refresh, "device_fingerprint_hash": DFP},
    )
    assert rotated.status_code == 200
    new_refresh = rotated.json()["refresh_token"]

    # Replaying the already-rotated token is detected and revokes the family.
    reuse = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": first_refresh, "device_fingerprint_hash": DFP},
    )
    assert reuse.status_code == 401

    # The post-reuse token (new_refresh) is now also revoked.
    after = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": new_refresh, "device_fingerprint_hash": DFP},
    )
    assert after.status_code == 401


async def test_refresh_device_fingerprint_mismatch_rejected(client: AsyncClient) -> None:
    data = await _login(client)
    resp = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": data["refresh_token"], "device_fingerprint_hash": DFP2},
    )
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Sessions & logout
# --------------------------------------------------------------------------- #
async def test_logout_current_device_revokes_session(client: AsyncClient) -> None:
    data = await _login(client)
    token = data["access_token"]
    out = await client.post("/api/v1/auth/logout", json={}, headers=_auth(token))
    assert out.status_code == 204
    # Access token now points at a revoked session.
    resp = await client.get("/api/v1/auth/sessions", headers=_auth(token))
    assert resp.status_code == 401


async def test_logout_all_devices(client: AsyncClient) -> None:
    first = await _login(client, dfp=DFP)
    second = await _login(client, dfp=DFP2)
    out = await client.post(
        "/api/v1/auth/logout",
        json={"revoke_all_devices": True},
        headers=_auth(second["access_token"]),
    )
    assert out.status_code == 204
    for data in (first, second):
        resp = await client.get("/api/v1/auth/sessions", headers=_auth(data["access_token"]))
        assert resp.status_code == 401


async def test_list_and_revoke_individual_session(client: AsyncClient) -> None:
    first = await _login(client, dfp=DFP)
    await _login(client, dfp=DFP2)
    listed = await client.get("/api/v1/auth/sessions", headers=_auth(first["access_token"]))
    assert listed.status_code == 200
    sessions = listed.json()["sessions"]
    assert len(sessions) == 2
    current = next(s for s in sessions if s["is_current"])
    other = next(s for s in sessions if not s["is_current"])
    assert "refresh_token_sha256" not in current  # never exposed

    revoke = await client.delete(
        f"/api/v1/auth/sessions/{other['session_id']}", headers=_auth(first["access_token"])
    )
    assert revoke.status_code == 204
    after = await client.get("/api/v1/auth/sessions", headers=_auth(first["access_token"]))
    assert len(after.json()["sessions"]) == 1


async def test_suspended_account_is_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    data = await _login(client)
    user = await db_session.get(User, uuid.UUID(data["user_id"]))
    assert user is not None
    user.account_status = "SUSPENDED"
    await db_session.commit()
    resp = await client.get("/api/v1/auth/sessions", headers=_auth(data["access_token"]))
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Account deletion
# --------------------------------------------------------------------------- #
async def test_account_deletion_requires_confirmation_string(client: AsyncClient) -> None:
    data = await _login(client)
    cid, code = await _request_otp(client, purpose="ACCOUNT_DELETE")
    resp = await client.request(
        "DELETE",
        "/api/v1/auth/account",
        json={"otp_challenge_id": cid, "otp_code": code, "confirmation": "WRONG"},
        headers=_auth(data["access_token"]),
    )
    assert resp.status_code == 422  # Literal confirmation mismatch


async def test_account_deletion_flow_and_tombstone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    data = await _login(client)
    user_id = uuid.UUID(data["user_id"])
    # Simulate one consumed free form.
    wallet = await db_session.get(EntitlementWallet, user_id)
    assert wallet is not None
    wallet.free_quota_used = 1
    await db_session.commit()

    cid, code = await _request_otp(client, purpose="ACCOUNT_DELETE")
    resp = await client.request(
        "DELETE",
        "/api/v1/auth/account",
        json={"otp_challenge_id": cid, "otp_code": code, "confirmation": "DELETE_MY_ACCOUNT"},
        headers=_auth(data["access_token"]),
    )
    assert resp.status_code == 200, resp.text
    receipt = resp.json()
    assert receipt["free_quota_tombstoned"] == 1
    assert receipt["revoked_session_count"] >= 1

    db_session.expire_all()
    user = await db_session.get(User, user_id)
    assert user is not None
    assert user.account_status == "DELETED"
    assert user.mobile_blind_index is None  # anonymised


async def test_reregistration_restores_consumed_quota(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    data = await _login(client)
    user_id = uuid.UUID(data["user_id"])
    wallet = await db_session.get(EntitlementWallet, user_id)
    assert wallet is not None
    wallet.free_quota_used = 1
    await db_session.commit()

    cid, code = await _request_otp(client, purpose="ACCOUNT_DELETE")
    deleted = await client.request(
        "DELETE",
        "/api/v1/auth/account",
        json={"otp_challenge_id": cid, "otp_code": code, "confirmation": "DELETE_MY_ACCOUNT"},
        headers=_auth(data["access_token"]),
    )
    assert deleted.status_code == 200

    # Re-register the SAME mobile → consumed quota is restored, not reset.
    again = await _login(client)
    assert again["wallet_summary"]["free_quota_used"] == 1
    assert again["wallet_summary"]["free_quota_remaining"] == 1


async def test_financial_ledger_preserved_on_deletion(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    data = await _login(client)
    user_id = uuid.UUID(data["user_id"])
    db_session.add(
        EntitlementLedgerEntry(
            id=uuid.uuid4(),
            user_id=user_id,
            sequence_no=1,
            entry_type="FREE_GRANT",
            credit_bucket="FREE_QUOTA",
            delta_available=2,
            delta_reserved=0,
            available_balance_after=2,
            reserved_balance_after=0,
            idempotency_key="seed-1",
            prev_entry_hash="0" * 64,
            entry_hash="f" * 64,
        )
    )
    await db_session.commit()

    cid, code = await _request_otp(client, purpose="ACCOUNT_DELETE")
    deleted = await client.request(
        "DELETE",
        "/api/v1/auth/account",
        json={"otp_challenge_id": cid, "otp_code": code, "confirmation": "DELETE_MY_ACCOUNT"},
        headers=_auth(data["access_token"]),
    )
    assert deleted.status_code == 200

    ledger_count = await db_session.scalar(
        select(func.count())
        .select_from(EntitlementLedgerEntry)
        .where(EntitlementLedgerEntry.user_id == user_id)
    )
    assert ledger_count == 1  # financial ledger untouched by erasure
    assert await db_session.get(EntitlementWallet, user_id) is not None
