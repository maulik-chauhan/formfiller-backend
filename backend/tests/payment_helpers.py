"""Helpers for payment/refund tests (not collected by pytest)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from httpx import AsyncClient

from app.modules.payments.gateway import compute_payment_signature, compute_webhook_signature


def auth(token: str, key: str | None = None) -> dict[str, str]:
    h = {"Authorization": f"Bearer {token}"}
    if key:
        h["Idempotency-Key"] = key
    return h


def idem() -> str:
    return str(uuid.uuid4())


async def create_order(
    client: AsyncClient, token: str, sku: str = "STD_FORM_20", key: str | None = None
) -> Any:
    return await client.post(
        "/api/v1/payments/orders",
        json={"sku_code": sku},
        headers=auth(token, key or idem()),
    )


async def capture_order(
    client: AsyncClient, token: str, sku: str = "STD_FORM_20", payment_id: str | None = None
) -> dict[str, Any]:
    r = await create_order(client, token, sku)
    assert r.status_code == 201, r.text
    body = r.json()
    gwo = body["gateway_order_id"]
    pay = payment_id or f"pay_{uuid.uuid4().hex[:14]}"
    sig = compute_payment_signature(gwo, pay)
    v = await client.post(
        f"/api/v1/payments/orders/{body['order_id']}/verify",
        json={"razorpay_order_id": gwo, "razorpay_payment_id": pay, "razorpay_signature": sig},
        headers=auth(token, idem()),
    )
    assert v.status_code == 200, v.text
    return {
        "order_id": body["order_id"],
        "gateway_order_id": gwo,
        "payment_id": pay,
        "total": body["total_amount_paise"],
        "base": body["base_amount_paise"],
    }


def webhook_headers(raw: bytes, event_id: str | None = None) -> dict[str, str]:
    return {
        "X-Razorpay-Signature": compute_webhook_signature(raw),
        "X-Razorpay-Event-Id": event_id or f"evt_{uuid.uuid4().hex[:14]}",
        "content-type": "application/json",
    }


def capture_payload(gateway_order_id: str, payment_id: str) -> dict[str, Any]:
    return {
        "event": "payment.captured",
        "payload": {"payment": {"entity": {"id": payment_id, "order_id": gateway_order_id}}},
    }


def refund_processed_payload(refund_id: str, payment_id: str, amount: int) -> dict[str, Any]:
    return {
        "event": "refund.processed",
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "payment_id": payment_id,
                    "amount": amount,
                    "status": "processed",
                }
            }
        },
    }


def refund_failed_payload(refund_id: str, payment_id: str, amount: int) -> dict[str, Any]:
    return {
        "event": "refund.failed",
        "payload": {
            "refund": {
                "entity": {
                    "id": refund_id,
                    "payment_id": payment_id,
                    "amount": amount,
                    "status": "failed",
                    "error_description": "bank declined the refund",
                }
            }
        },
    }


async def post_webhook(
    client: AsyncClient, payload: dict[str, Any], event_id: str | None = None
) -> Any:
    raw = json.dumps(payload).encode("utf-8")
    return await client.post(
        "/api/v1/payments/webhooks/razorpay", content=raw, headers=webhook_headers(raw, event_id)
    )
