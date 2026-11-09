"""M6 refund reconciliation tests on PostgreSQL (7 scenarios + ledger)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.ledger import GENESIS_HASH, compute_entry_hash
from app.modules.entitlements.models import (
    EntitlementLease,
    EntitlementLedgerEntry,
    EntitlementWallet,
)
from app.modules.payments.models import PaymentOrder, PaymentRefund
from tests.payment_helpers import (
    auth,
    capture_order,
    idem,
    post_webhook,
    refund_failed_payload,
    refund_processed_payload,
)

pytestmark = pytest.mark.asyncio

DRAFT = "a" * 64
DFP = "b" * 64


async def _make_paid_std_lease(
    client: AsyncClient, token: str, tv_id: Any, draft: str = DRAFT
) -> str:
    r = await client.post(
        "/api/v1/entitlements/leases",
        json={
            "template_version_id": str(tv_id),
            "requested_tier": "PAID_STANDARD_20",
            "draft_instance_id_hash": draft,
            "device_fingerprint_hash": DFP,
        },
        headers=auth(token, idem()),
    )
    assert r.status_code == 201, r.text
    return r.json()["lease_id"]


async def _commit_lease(client: AsyncClient, token: str, lease_id: str, draft: str = DRAFT) -> None:
    r = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={"draft_instance_id_hash": draft, "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, idem()),
    )
    assert r.status_code == 200, r.text


# --------------------------------------------------------------------------- #
# Scenario 1 - full refund, credit AVAILABLE
# --------------------------------------------------------------------------- #
async def test_refund_available_credit(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    r = await post_webhook(
        client, refund_processed_payload("rfnd_1", cap["payment_id"], cap["total"])
    )
    assert r.status_code == 200
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.payment_status == "REFUNDED"
    assert order.refunded_amount_paise == cap["total"]
    refund = (await db_session.execute(select(PaymentRefund))).scalars().one()
    assert refund.wallet_reconciliation_action == "DEBITED_AVAILABLE_CREDIT"


# --------------------------------------------------------------------------- #
# Scenario 2 - full refund, credit RESERVED in ISSUED lease
# --------------------------------------------------------------------------- #
async def test_refund_reserved_lease(
    client: AsyncClient, make_principal, pricing, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    lease_id = await _make_paid_std_lease(client, token, published_template_version)

    r = await post_webhook(
        client, refund_processed_payload("rfnd_2", cap["payment_id"], cap["total"])
    )
    assert r.status_code == 200
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_reserved == 0
    lease = await db_session.get(EntitlementLease, uuid.UUID(lease_id))
    assert lease.lease_status == "REVOKED_REFUND"
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.payment_status == "REFUNDED"


async def test_revoked_lease_cannot_commit(
    client: AsyncClient, make_principal, pricing, published_template_version, db_session
) -> None:
    _, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    lease_id = await _make_paid_std_lease(client, token, published_template_version)
    await post_webhook(client, refund_processed_payload("rfnd_2b", cap["payment_id"], cap["total"]))
    # A revoked lease is no longer committable.
    r = await client.post(
        f"/api/v1/entitlements/leases/{lease_id}/commit",
        json={"draft_instance_id_hash": DRAFT, "client_completion_receipt_hash": "c" * 64},
        headers=auth(token, idem()),
    )
    assert r.status_code == 409


# --------------------------------------------------------------------------- #
# Scenario 3 - full refund after COMMITTED (post-consumption)
# --------------------------------------------------------------------------- #
async def test_refund_post_consumption(
    client: AsyncClient, make_principal, pricing, published_template_version, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    lease_id = await _make_paid_std_lease(client, token, published_template_version)
    await _commit_lease(client, token, lease_id)

    r = await post_webhook(
        client, refund_processed_payload("rfnd_3", cap["payment_id"], cap["total"])
    )
    assert r.status_code == 200
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0
    assert wallet.paid_std_base_reserved == 0
    assert wallet.post_consumption_refund_count == 1
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.payment_status == "REFUNDED_POST_CONSUMPTION"


# --------------------------------------------------------------------------- #
# Scenario 4 - partial refunds below and at/above the base threshold
# --------------------------------------------------------------------------- #
async def test_partial_refund_threshold(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")  # total 2360, base 2000

    # Below base threshold → no credit revocation.
    await post_webhook(client, refund_processed_payload("rfnd_4a", cap["payment_id"], 300))
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 1
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.payment_status == "PARTIALLY_REFUNDED"
    assert order.refunded_amount_paise == 300

    # Crossing the base threshold → revoke exactly one credit.
    await post_webhook(client, refund_processed_payload("rfnd_4b", cap["payment_id"], 2000))
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0  # revoked once
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.refunded_amount_paise == 2300

    # Final partial completing the total → no double revocation.
    await post_webhook(client, refund_processed_payload("rfnd_4c", cap["payment_id"], 60))
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.payment_status == "REFUNDED"
    assert order.refunded_amount_paise == 2360


# --------------------------------------------------------------------------- #
# Scenario 5 - failed refund
# --------------------------------------------------------------------------- #
async def test_failed_refund(client: AsyncClient, make_principal, pricing, db_session) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    r = await post_webhook(client, refund_failed_payload("rfnd_5", cap["payment_id"], cap["total"]))
    assert r.status_code == 200
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 1  # unchanged
    order = await db_session.get(PaymentOrder, uuid.UUID(cap["order_id"]))
    assert order.payment_status == "CAPTURED"  # not refunded
    refund = (await db_session.execute(select(PaymentRefund))).scalars().one()
    assert refund.refund_status == "FAILED"
    assert refund.failure_reason


# --------------------------------------------------------------------------- #
# Scenario 6 - duplicate refund webhook
# --------------------------------------------------------------------------- #
async def test_duplicate_refund_webhook(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    payload = refund_processed_payload("rfnd_6", cap["payment_id"], cap["total"])
    r1 = await post_webhook(client, payload, event_id="evt_r6")
    r2 = await post_webhook(client, payload, event_id="evt_r6")
    assert r1.json()["status"] == "PROCESSED"
    assert r2.json()["status"] == "ALREADY_PROCESSED"
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0  # debited once only
    refund_count = await db_session.scalar(select(func.count()).select_from(PaymentRefund))
    assert refund_count == 1


# --------------------------------------------------------------------------- #
# Scenario 7 - concurrent refund processing (same refund id)
# --------------------------------------------------------------------------- #
async def test_concurrent_same_refund_debits_once(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    payload = refund_processed_payload("rfnd_7", cap["payment_id"], cap["total"])
    r1, r2 = await asyncio.gather(
        post_webhook(client, payload, event_id="evt_7a"),
        post_webhook(client, payload, event_id="evt_7b"),
    )
    assert {r1.status_code, r2.status_code} == {200}
    db_session.expire_all()
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0  # never negative, debited once
    refund_count = await db_session.scalar(select(func.count()).select_from(PaymentRefund))
    assert refund_count == 1  # gateway_refund_id uniqueness held


# --------------------------------------------------------------------------- #
# G. Ledger integrity across payment + refund
# --------------------------------------------------------------------------- #
async def test_ledger_chain_across_payment_and_refund(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    cap = await capture_order(client, token, "STD_FORM_20")
    await post_webhook(client, refund_processed_payload("rfnd_g", cap["payment_id"], cap["total"]))

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
    assert [e.entry_type for e in entries] == ["PAID_CREDIT", "REFUND_DEBIT_AVAILABLE"]
    prev = GENESIS_HASH
    for i, e in enumerate(entries, start=1):
        assert e.sequence_no == i
        assert e.prev_entry_hash == prev
        assert e.entry_hash == compute_entry_hash(
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
        prev = e.entry_hash
    # Exactly one PAID_CREDIT per order (partial unique index).
    paid = [e for e in entries if e.entry_type == "PAID_CREDIT"]
    assert len(paid) == 1
    assert paid[0].reference_payment_order_id == uuid.UUID(cap["order_id"])
