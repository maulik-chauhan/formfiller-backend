"""M5 entitlement lifecycle integration tests on PostgreSQL (acceptance A-F)."""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import elt
from app.core.constants import LedgerEntryType
from app.core.ledger import GENESIS_HASH, compute_entry_hash
from app.modules.entitlements.models import (
    EntitlementLease,
    EntitlementLedgerEntry,
    EntitlementWallet,
)
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


def draft(i: int) -> str:
    return f"{i:064x}"


async def create_lease(
    client: AsyncClient,
    token: str,
    tv_id: Any,
    draft_hash: str,
    key: str,
    tier: str = "FREE_STANDARD",
    dfp: str = DFP,
) -> Any:
    return await client.post(
        "/api/v1/entitlements/leases",
        json={
            "template_version_id": str(tv_id),
            "requested_tier": tier,
            "draft_instance_id_hash": draft_hash,
            "device_fingerprint_hash": dfp,
        },
        headers=auth(token, key),
    )


# --------------------------------------------------------------------------- #
# Public keys & wallet
# --------------------------------------------------------------------------- #
async def test_public_keys_endpoint(client: AsyncClient) -> None:
    r = await client.get("/api/v1/entitlements/public-keys")
    assert r.status_code == 200
    body = r.json()
    assert body["elt_typ"] == "ELT+jwt"
    assert any(k["status"] == "active" for k in body["keys"])
    assert "private" not in r.text.lower()


async def test_wallet_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/entitlements/wallet")).status_code == 401


async def test_wallet_balance(client: AsyncClient, make_principal) -> None:
    _, token = await make_principal()
    r = await client.get("/api/v1/entitlements/wallet", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["free_quota_total"] == 2
    assert r.json()["free_quota_available"] == 2


# --------------------------------------------------------------------------- #
# A. Concurrent reservation
# --------------------------------------------------------------------------- #
async def test_concurrent_reservation_two_succeed_eight_payment_required(
    client: AsyncClient, make_principal, published_template_version
) -> None:
    _, token = await make_principal()  # free_quota_total == 2
    reqs = [
        create_lease(client, token, published_template_version, draft(i), uuid4())
        for i in range(10)
    ]
    results = await asyncio.gather(*reqs)
    codes = sorted(r.status_code for r in results)
    assert codes.count(201) == 2
    assert codes.count(402) == 8


async def test_reservation_never_goes_negative(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    await asyncio.gather(
        *[
            create_lease(client, token, published_template_version, draft(i), uuid4())
            for i in range(10)
        ]
    )
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_reserved == 2
    assert wallet.free_quota_total - wallet.free_quota_used - wallet.free_quota_reserved == 0


# --------------------------------------------------------------------------- #
# B. Signature upgrade
# --------------------------------------------------------------------------- #
async def test_signature_upgrade(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    # Grant one SIG_UPGRADE_30 credit (as if purchased in M6).
    wallet = await db_session.get(EntitlementWallet, uid)
    wallet.paid_sig_upgrade_available = 1
    await db_session.commit()

    created = await create_lease(client, token, published_template_version, draft(1), uuid4())
    assert created.status_code == 201
    lease_id = created.json()["lease_id"]

    up = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/upgrade",
        json={
            "upgrade_sku": "SIG_UPGRADE_30",
            "draft_instance_id_hash": draft(1),
            "device_fingerprint_hash": DFP,
        },
        headers=auth(token, uuid4()),
    )
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["granted_tier"] == "FREE_UPGRADED_SIG_50"
    assert body["is_upgraded_with_sig_30"] is True
    assert body["capabilities"]["allow_handwritten_signature_image"] is True
    # No second base credit consumed: still 1 free reserved, 1 remaining.
    assert body["wallet_snapshot"]["lifetime_free_remaining"] == 1
    assert body["wallet_snapshot"]["paid_upgrade_30_available"] == 0

    claims = elt.verify_elt(body["offline_lease_jwt"])
    assert claims["tier"] == "FREE_UPGRADED_SIG_50"
    assert claims["cap"]["sig_img"] is True


# --------------------------------------------------------------------------- #
# C. Correction window
# --------------------------------------------------------------------------- #
async def test_commit_and_correction_window(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_lease(client, token, published_template_version, draft(1), uuid4())
    lease_id = created.json()["lease_id"]

    def commit_body() -> dict[str, Any]:
        return {
            "draft_instance_id_hash": draft(1),
            "client_completion_receipt_hash": "c" * 64,
            "completed_at_client_iso": "2026-11-10T10:00:00+00:00",
        }

    c1 = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json=commit_body(),
        headers=auth(token, uuid4()),
    )
    assert c1.status_code == 200
    assert c1.json()["commit_count"] == 1

    c2 = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json=commit_body(),
        headers=auth(token, uuid4()),
    )
    assert c2.status_code == 200
    assert c2.json()["commit_count"] == 2

    # Exactly one free credit consumed across both commits.
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_used == 1
    assert wallet.free_quota_reserved == 0

    # Expire the correction window and try again → 410 Gone.
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    lease.correction_window_expires_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(minutes=1)
    await db_session.commit()
    c3 = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json=commit_body(),
        headers=auth(token, uuid4()),
    )
    assert c3.status_code == 410
    assert c3.json()["code"] == "CORRECTION_WINDOW_EXPIRED"


# --------------------------------------------------------------------------- #
# D. Offline recovery + reaper
# --------------------------------------------------------------------------- #
async def _expire_and_reap(db_session, db_engine, lease_id: str) -> int:
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    lease.reservation_expires_at = dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1)
    await db_session.commit()
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    return await reap_once(maker)


async def test_reaper_expires_issued_lease(
    client: AsyncClient, make_principal, published_template_version, db_session, db_engine
) -> None:
    uid, token = await make_principal()
    created = await create_lease(client, token, published_template_version, draft(1), uuid4())
    lease_id = created.json()["lease_id"]

    reaped = await _expire_and_reap(db_session, db_engine, lease_id)
    assert reaped == 1
    db_session.expire_all()
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    assert lease.lease_status == "EXPIRED_RELEASED"
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_reserved == 0
    assert wallet.free_quota_used == 0  # credit restored

    # Reaper is idempotent: a second sweep reaps nothing and does not double-restore.
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    assert await reap_once(maker) == 0


async def test_delayed_recovery_success_when_credit_available(
    client: AsyncClient, make_principal, published_template_version, db_session, db_engine
) -> None:
    uid, token = await make_principal()
    wallet = await db_session.get(EntitlementWallet, uid)
    wallet.free_quota_total = 1  # single credit so recovery is unambiguous
    await db_session.commit()

    created = await create_lease(client, token, published_template_version, draft(1), uuid4())
    lease_id = created.json()["lease_id"]
    await _expire_and_reap(db_session, db_engine, lease_id)

    recover = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={
            "draft_instance_id_hash": draft(1),
            "client_completion_receipt_hash": "c" * 64,
            "completed_at_client_iso": "2026-11-10T10:00:00+00:00",
        },
        headers=auth(token, uuid4()),
    )
    assert recover.status_code == 200
    assert recover.json()["lease_status"] == "COMMITTED"
    db_session.expire_all()
    w = await db_session.get(EntitlementWallet, uid)
    assert w.free_quota_used == 1


async def test_delayed_recovery_conflict_when_credit_spent(
    client: AsyncClient, make_principal, published_template_version, db_session, db_engine
) -> None:
    uid, token = await make_principal()
    wallet = await db_session.get(EntitlementWallet, uid)
    wallet.free_quota_total = 1
    await db_session.commit()

    a = await create_lease(client, token, published_template_version, draft(1), uuid4())
    lease_a = a.json()["lease_id"]
    await _expire_and_reap(db_session, db_engine, lease_a)

    # Spend the single restored credit on another draft and commit it.
    b = await create_lease(client, token, published_template_version, draft(2), uuid4())
    assert b.status_code == 201
    await client.post(
        f"/api/v1/entitlements/leases/{b.json()['lease_id']}/commit",
        json={"draft_instance_id_hash": draft(2), "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, uuid4()),
    )

    # Now the late recovery of A must fail — no negative balance.
    recover = await client.post(
        f"/api/v1/entitlements/leases/{lease_a}/commit",
        json={"draft_instance_id_hash": draft(1), "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, uuid4()),
    )
    assert recover.status_code == 409
    assert recover.json()["code"] == "LEASE_EXPIRED_CREDIT_CONSUMED"
    db_session.expire_all()
    w = await db_session.get(EntitlementWallet, uid)
    assert w.free_quota_used == 1  # not 2; never negative


# --------------------------------------------------------------------------- #
# E. Release
# --------------------------------------------------------------------------- #
async def test_release_is_idempotent_and_blocks_committed(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_lease(client, token, published_template_version, draft(1), uuid4())
    lease_id = created.json()["lease_id"]

    body = {"draft_instance_id_hash": draft(1), "unconsumed_attestation_hmac": "d" * 64}
    r1 = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/release", json=body, headers=auth(token, uuid4())
    )
    assert r1.status_code == 200
    assert r1.json()["lease_status"] == "RELEASED"

    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_reserved == 0
    assert wallet.free_quota_total - wallet.free_quota_used - wallet.free_quota_reserved == 2

    # Repeat release → idempotent 200, reservation restored exactly once.
    r2 = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/release", json=body, headers=auth(token, uuid4())
    )
    assert r2.status_code == 200
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_total - wallet.free_quota_used - wallet.free_quota_reserved == 2

    # A committed lease cannot be released.
    created2 = await create_lease(client, token, published_template_version, draft(2), uuid4())
    lid2 = created2.json()["lease_id"]
    await client.post(
        f"/api/v1/entitlements/leases/{lid2}/commit",
        json={"draft_instance_id_hash": draft(2), "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, uuid4()),
    )
    r3 = await client.post(
        f"/api/v1/entitlements/leases/{lid2}/release",
        json={"draft_instance_id_hash": draft(2), "unconsumed_attestation_hmac": "d" * 64},
        headers=auth(token, uuid4()),
    )
    assert r3.status_code == 409


# --------------------------------------------------------------------------- #
# F. Idempotency & ledger integrity
# --------------------------------------------------------------------------- #
async def test_create_lease_idempotent_replay(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    key = uuid4()
    r1 = await create_lease(client, token, published_template_version, draft(1), key)
    r2 = await create_lease(client, token, published_template_version, draft(1), key)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["lease_id"] == r2.json()["lease_id"]
    # Exactly one reserve ledger entry and one reserved credit.
    count = await db_session.scalar(
        select(func.count())
        .select_from(EntitlementLedgerEntry)
        .where(EntitlementLedgerEntry.user_id == uid)
    )
    assert count == 1
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_reserved == 1


async def test_payment_required_leaves_no_ledger_or_wallet_change(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    wallet = await db_session.get(EntitlementWallet, uid)
    wallet.free_quota_total = 0  # no free credits
    await db_session.commit()

    r = await create_lease(client, token, published_template_version, draft(1), uuid4())
    assert r.status_code == 402
    # Rollback leaves no ledger entry and no reservation.
    count = await db_session.scalar(
        select(func.count())
        .select_from(EntitlementLedgerEntry)
        .where(EntitlementLedgerEntry.user_id == uid)
    )
    assert count == 0
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.free_quota_reserved == 0


async def test_ledger_hash_chain_integrity(
    client: AsyncClient, make_principal, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_lease(client, token, published_template_version, draft(1), uuid4())
    lease_id = created.json()["lease_id"]
    await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={"draft_instance_id_hash": draft(1), "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, uuid4()),
    )
    entries = (
        (
            await db_session.execute(
                select(EntitlementLedgerEntry)
                .where(EntitlementLedgerEntry.user_id == uid)
                .order_by(EntitlementLedgerEntry.sequence_no)
            )
        )
        .scalars()
        .all()
    )
    assert len(entries) == 2  # RESERVE + COMMIT
    prev = GENESIS_HASH
    for i, e in enumerate(entries, start=1):
        assert e.sequence_no == i
        assert e.prev_entry_hash == prev
        recomputed = compute_entry_hash(
            user_id=str(uid),
            sequence_no=e.sequence_no,
            entry_type=e.entry_type,
            credit_bucket=e.credit_bucket,
            delta_available=e.delta_available,
            delta_reserved=e.delta_reserved,
            available_balance_after=e.available_balance_after,
            reserved_balance_after=e.reserved_balance_after,
            idempotency_key=e.idempotency_key,
            prev_entry_hash=e.prev_entry_hash,
        )
        assert e.entry_hash == recomputed
        prev = e.entry_hash
    assert entries[0].entry_type == LedgerEntryType.LEASE_RESERVE
    assert entries[1].entry_type == LedgerEntryType.LEASE_COMMIT
