"""M5 security tests (acceptance G): authz, ownership, draft/timestamp, keys."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.entitlements.models import EntitlementLease, EntitlementWallet
from app.workers.reaper import reap_once

pytestmark = pytest.mark.asyncio

DFP = "a" * 64


def auth(token: str, key: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def uuid4() -> str:
    return str(uuid.uuid4())


async def _lease(client: AsyncClient, token: str, tv_id: Any, draft: str) -> str:
    r = await client.post(
        "/api/v1/entitlements/leases",
        json={
            "template_version_id": str(tv_id),
            "requested_tier": "FREE_STANDARD",
            "draft_instance_id_hash": draft,
            "device_fingerprint_hash": DFP,
        },
        headers=auth(token, uuid4()),
    )
    assert r.status_code == 201, r.text
    return r.json()["lease_id"]


async def test_unauthenticated_rejected(client: AsyncClient, published_template_version) -> None:
    r = await client.post(
        "/api/v1/entitlements/leases",
        json={
            "template_version_id": str(published_template_version),
            "requested_tier": "FREE_STANDARD",
            "draft_instance_id_hash": "b" * 64,
            "device_fingerprint_hash": DFP,
        },
        headers={"Idempotency-Key": uuid4()},
    )
    assert r.status_code == 401


async def test_idempotency_key_required(
    client: AsyncClient, make_principal, published_template_version
) -> None:
    _, token = await make_principal()
    r = await client.post(
        "/api/v1/entitlements/leases",
        json={
            "template_version_id": str(published_template_version),
            "requested_tier": "FREE_STANDARD",
            "draft_instance_id_hash": "b" * 64,
            "device_fingerprint_hash": DFP,
        },
        headers=auth(token),  # no Idempotency-Key
    )
    assert r.status_code == 422


async def test_invalid_draft_hash_rejected(
    client: AsyncClient, make_principal, published_template_version
) -> None:
    _, token = await make_principal()
    r = await client.post(
        "/api/v1/entitlements/leases",
        json={
            "template_version_id": str(published_template_version),
            "requested_tier": "FREE_STANDARD",
            "draft_instance_id_hash": "not-a-hash",
            "device_fingerprint_hash": DFP,
        },
        headers=auth(token, uuid4()),
    )
    assert r.status_code == 422


async def test_user_cannot_touch_another_users_lease(
    client: AsyncClient, make_principal, published_template_version
) -> None:
    _, token_a = await make_principal()
    _, token_b = await make_principal()
    lease_a = await _lease(client, token_a, published_template_version, "b" * 64)

    # B tries to commit / release / upgrade A's lease → 404 (no existence leak).
    for path, body in (
        (
            "commit",
            {"draft_instance_id_hash": "b" * 64, "client_completion_receipt_hash": "c" * 64},
        ),
        ("release", {"draft_instance_id_hash": "b" * 64, "unconsumed_attestation_hmac": "d" * 64}),
        (
            "upgrade",
            {
                "upgrade_sku": "SIG_UPGRADE_30",
                "draft_instance_id_hash": "b" * 64,
                "device_fingerprint_hash": DFP,
            },
        ),
    ):
        r = await client.post(
            f"/api/v1/entitlements/leases/{lease_a}/{path}",
            json=body,
            headers=auth(token_b, uuid4()),
        )
        assert r.status_code == 404


async def test_client_timestamp_cannot_extend_expired_window(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    _, token = await make_principal()
    lease_id = await _lease(client, token, published_template_version, "b" * 64)
    await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={"draft_instance_id_hash": "b" * 64, "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, uuid4()),
    )
    # Force the correction window into the past.
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    lease.correction_window_expires_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1)
    await db_session.commit()

    # A far-future client timestamp must NOT reopen the window (server clock wins).
    r = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={
            "draft_instance_id_hash": "b" * 64,
            "client_completion_receipt_hash": "c" * 64,
            "completed_at_client_iso": "2099-01-01T00:00:00+00:00",
        },
        headers=auth(token, uuid4()),
    )
    assert r.status_code == 410


async def test_backdated_timestamp_cannot_extend_recovery_window(
    client: AsyncClient, make_principal, published_template_version, db_session, db_engine
) -> None:
    uid, token = await make_principal()
    w = await db_session.get(EntitlementWallet, uid)
    w.free_quota_total = 1
    await db_session.commit()
    lease_id = await _lease(client, token, published_template_version, "b" * 64)
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    lease.reservation_expires_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1)
    await db_session.commit()
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    await reap_once(maker)

    # Recovery with a wildly future client ts: window is capped at reservation+24h,
    # never driven by the client clock. Recovery still succeeds (credit available).
    r = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={
            "draft_instance_id_hash": "b" * 64,
            "client_completion_receipt_hash": "c" * 64,
            "completed_at_client_iso": "2099-01-01T00:00:00+00:00",
        },
        headers=auth(token, uuid4()),
    )
    assert r.status_code == 200
    db_session.expire_all()
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    # Window is bounded by reservation_expires_at + 24h, not the 2099 client stamp.
    assert lease.correction_window_expires_at < dt.datetime.now(tz=dt.UTC) + dt.timedelta(days=2)


async def test_public_keys_never_expose_private(client: AsyncClient) -> None:
    r = await client.get("/api/v1/entitlements/public-keys")
    assert r.status_code == 200
    text = r.text.lower()
    assert "private" not in text
    assert "seed" not in text
    assert all("public_key_b64" in k for k in r.json()["keys"])
