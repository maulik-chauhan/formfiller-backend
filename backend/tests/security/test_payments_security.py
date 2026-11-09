"""M6 payment security tests: authz, ownership, idempotency, secret exposure."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.payment_helpers import auth, capture_order, create_order, idem

pytestmark = pytest.mark.asyncio


async def test_order_and_verify_require_auth(client: AsyncClient, pricing) -> None:
    r1 = await client.post("/api/v1/payments/orders", json={"sku_code": "STD_FORM_20"})
    assert r1.status_code == 401
    r2 = await client.post(
        "/api/v1/payments/orders/00000000-0000-4000-8000-000000000000/verify",
        json={"razorpay_order_id": "o", "razorpay_payment_id": "p", "razorpay_signature": "s" * 16},
    )
    assert r2.status_code == 401


async def test_order_requires_idempotency_key(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    r = await client.post(
        "/api/v1/payments/orders", json={"sku_code": "STD_FORM_20"}, headers=auth(token)
    )
    assert r.status_code == 422


async def test_user_cannot_verify_another_users_order(
    client: AsyncClient, make_principal, pricing
) -> None:
    _, token_a = await make_principal()
    _, token_b = await make_principal()
    created = await create_order(client, token_a, "STD_FORM_20")
    body = created.json()
    r = await client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json={
            "razorpay_order_id": body["gateway_order_id"],
            "razorpay_payment_id": "pay_b",
            "razorpay_signature": "x" * 32,
        },
        headers=auth(token_b, idem()),
    )
    assert r.status_code == 404  # ownership enforced, no existence leak


async def test_secrets_never_exposed(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    pricing_resp = await client.get("/api/v1/payments/pricing")
    created = await create_order(client, token, "STD_FORM_20")
    captured = await capture_order(client, token, "SIG_UPGRADE_30")
    blob = (pricing_resp.text + created.text + str(captured)).lower()
    assert "rzp_test_key_secret" not in blob
    assert "webhook_secret" not in blob
    assert "razorpay_key_secret" not in blob


async def test_esign_sku_cannot_be_ordered(client: AsyncClient, make_principal, pricing) -> None:
    _, token = await make_principal()
    # ESIGN SKU is inactive AND gated → never purchasable in M6.
    r = await create_order(client, token, "ESIGN_OTP_CDAC")
    assert r.status_code in (403, 422)
