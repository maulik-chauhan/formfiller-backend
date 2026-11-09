"""M6 payment integration tests on PostgreSQL (pricing, orders, verify, webhook)."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.modules.entitlements.models import EntitlementLedgerEntry, EntitlementWallet
from app.modules.payments.gateway import compute_payment_signature
from app.modules.payments.models import PaymentOrder
from tests.payment_helpers import (
    auth,
    capture_order,
    capture_payload,
    create_order,
    idem,
    post_webhook,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# A. Dynamic pricing
# --------------------------------------------------------------------------- #
async def test_pricing_lists_active_and_computes_tax(client: AsyncClient, pricing) -> None:
    r = await client.get("/api/v1/payments/pricing")
    assert r.status_code == 200
    skus = {s["sku_code"]: s for s in r.json()["skus"]}
    # eSign SKU is inactive/gated → absent.
    assert "ESIGN_OTP_CDAC" not in skus
    assert set(skus) == {"STD_FORM_20", "SIG_IMAGE_50", "SIG_UPGRADE_30"}
    # GST exclusive: tax = total - base - surcharge (integer paise).
    assert skus["STD_FORM_20"]["tax_amount_paise"] == 360
    assert skus["STD_FORM_20"]["total_payable_paise"] == 2360
    # Surcharge passed through.
    assert skus["SIG_IMAGE_50"]["esp_surcharge_paise"] == 500
    assert "secret" not in r.text.lower()


# --------------------------------------------------------------------------- #
# B. Order creation
# --------------------------------------------------------------------------- #
async def test_create_order_valid(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    r = await create_order(client, token, "STD_FORM_20")
    assert r.status_code == 201
    body = r.json()
    assert body["total_amount_paise"] == 2360
    assert body["base_amount_paise"] == 2000
    assert body["tax_amount_paise"] == 360
    assert body["gateway_order_id"].startswith("order_")
    assert body["razorpay_key_id"] == "rzp_test_key_id"
    assert "secret" not in r.text.lower()


async def test_invalid_and_inactive_sku_rejected(
    client: AsyncClient, make_principal, pricing
) -> None:
    _, token = await make_principal()
    # Unknown SKU → schema 422.
    bad = await client.post(
        "/api/v1/payments/orders", json={"sku_code": "NOPE"}, headers=auth(token, idem())
    )
    assert bad.status_code == 422
    # Inactive eSign SKU → 422 (not purchasable).
    inactive = await create_order(client, token, "ESIGN_OTP_CDAC")
    assert inactive.status_code == 422


async def test_invalid_quantity_rejected(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    for q in (0, 2):
        r = await client.post(
            "/api/v1/payments/orders",
            json={"sku_code": "STD_FORM_20", "quantity": q},
            headers=auth(token, idem()),
        )
        assert r.status_code == 422


async def test_client_supplied_amount_ignored(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    # extra="forbid" rejects any attempt to inject a total.
    r = await client.post(
        "/api/v1/payments/orders",
        json={"sku_code": "STD_FORM_20", "total_amount_paise": 1},
        headers=auth(token, idem()),
    )
    assert r.status_code == 422


async def test_order_idempotent_replay(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    key = idem()
    r1 = await create_order(client, token, "STD_FORM_20", key=key)
    r2 = await create_order(client, token, "STD_FORM_20", key=key)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["order_id"] == r2.json()["order_id"]
    assert r1.json()["gateway_order_id"] == r2.json()["gateway_order_id"]
    count = await db_session.scalar(
        select(func.count()).select_from(PaymentOrder).where(PaymentOrder.user_id == uid)
    )
    assert count == 1


# --------------------------------------------------------------------------- #
# C. Signature verification
# --------------------------------------------------------------------------- #
async def test_verify_credits_wallet(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    captured = await capture_order(client, token, "STD_FORM_20")
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 1
    order = await db_session.get(PaymentOrder, uuid.UUID(captured["order_id"]))
    assert order.payment_status == "CAPTURED"


async def test_forged_signature_never_credits(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_order(client, token, "STD_FORM_20")
    body = created.json()
    r = await client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json={
            "razorpay_order_id": body["gateway_order_id"],
            "razorpay_payment_id": "pay_forged",
            "razorpay_signature": "deadbeefdeadbeef",
        },
        headers=auth(token, idem()),
    )
    assert r.status_code == 401
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 0


async def test_wrong_order_id_rejected(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    created = await create_order(client, token, "STD_FORM_20")
    body = created.json()
    sig = compute_payment_signature("order_mismatch", "pay_1")
    r = await client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json={
            "razorpay_order_id": "order_mismatch",
            "razorpay_payment_id": "pay_1",
            "razorpay_signature": sig,
        },
        headers=auth(token, idem()),
    )
    assert r.status_code == 422


async def test_verify_is_idempotent(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_order(client, token, "STD_FORM_20")
    body = created.json()
    gwo, pay = body["gateway_order_id"], "pay_dup"
    sig = compute_payment_signature(gwo, pay)
    payload = {"razorpay_order_id": gwo, "razorpay_payment_id": pay, "razorpay_signature": sig}
    v1 = await client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json=payload,
        headers=auth(token, idem()),
    )
    v2 = await client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json=payload,
        headers=auth(token, idem()),
    )
    assert v1.status_code == 200 and v2.status_code == 200
    assert v1.json()["credited"] is True and v2.json()["credited"] is False
    paid_credits = await db_session.scalar(
        select(func.count())
        .select_from(EntitlementLedgerEntry)
        .where(
            EntitlementLedgerEntry.user_id == uid,
            EntitlementLedgerEntry.entry_type == "PAID_CREDIT",
        )
    )
    assert paid_credits == 1


# --------------------------------------------------------------------------- #
# D. Critical concurrent fulfilment: sync verify + webhook
# --------------------------------------------------------------------------- #
async def test_concurrent_verify_and_webhook_credit_once(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_order(client, token, "STD_FORM_20")
    body = created.json()
    gwo, pay = body["gateway_order_id"], "pay_race"
    sig = compute_payment_signature(gwo, pay)

    verify = client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json={"razorpay_order_id": gwo, "razorpay_payment_id": pay, "razorpay_signature": sig},
        headers=auth(token, idem()),
    )
    webhook = post_webhook(client, capture_payload(gwo, pay))
    r_verify, r_webhook = await asyncio.gather(verify, webhook)
    assert r_verify.status_code == 200
    assert r_webhook.status_code == 200

    db_session.expire_all()
    order = await db_session.get(PaymentOrder, uuid.UUID(body["order_id"]))
    assert order.payment_status == "CAPTURED"
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 1  # credited exactly once
    paid_credits = await db_session.scalar(
        select(func.count())
        .select_from(EntitlementLedgerEntry)
        .where(
            EntitlementLedgerEntry.user_id == uid,
            EntitlementLedgerEntry.entry_type == "PAID_CREDIT",
        )
    )
    assert paid_credits == 1


# --------------------------------------------------------------------------- #
# E. Webhook
# --------------------------------------------------------------------------- #
async def test_webhook_valid_capture(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_order(client, token, "STD_FORM_20")
    body = created.json()
    r = await post_webhook(client, capture_payload(body["gateway_order_id"], "pay_wh"))
    assert r.status_code == 200
    assert r.json()["status"] == "PROCESSED"
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 1


async def test_webhook_invalid_signature_rejected(client: AsyncClient, pricing) -> None:
    import json as _json

    raw = _json.dumps(capture_payload("order_x", "pay_x")).encode()
    r = await client.post(
        "/api/v1/payments/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": "forged",
            "X-Razorpay-Event-Id": "evt_1",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 401


async def test_webhook_oversize_rejected(client: AsyncClient) -> None:
    import json as _json

    big = {"event": "payment.captured", "blob": "x" * 70_000}
    raw = _json.dumps(big).encode()
    # Signature present so we reach the size gate; middleware caps at 64 KB.
    from app.modules.payments.gateway import compute_webhook_signature

    r = await client.post(
        "/api/v1/payments/webhooks/razorpay",
        content=raw,
        headers={
            "X-Razorpay-Signature": compute_webhook_signature(raw),
            "X-Razorpay-Event-Id": "evt_big",
            "content-type": "application/json",
        },
    )
    assert r.status_code == 413


async def test_webhook_duplicate_event_id(
    client: AsyncClient, make_principal, pricing, db_session
) -> None:
    uid, token = await make_principal()
    created = await create_order(client, token, "STD_FORM_20")
    body = created.json()
    payload = capture_payload(body["gateway_order_id"], "pay_once")
    r1 = await post_webhook(client, payload, event_id="evt_same")
    r2 = await post_webhook(client, payload, event_id="evt_same")
    assert r1.json()["status"] == "PROCESSED"
    assert r2.json()["status"] == "ALREADY_PROCESSED"
    wallet = await db_session.get(EntitlementWallet, uid)
    assert wallet.paid_std_base_available == 1  # not credited twice


async def test_webhook_unknown_event_ignored(client: AsyncClient, pricing) -> None:
    r = await post_webhook(client, {"event": "subscription.charged", "payload": {}})
    assert r.status_code == 200
    assert r.json()["status"] == "IGNORED"
