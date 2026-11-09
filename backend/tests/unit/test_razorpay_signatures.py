"""Unit tests for Razorpay HMAC signature verification."""

from __future__ import annotations

from app.modules.payments.gateway import (
    compute_payment_signature,
    compute_webhook_signature,
    verify_payment_signature,
    verify_webhook_signature,
)


def test_payment_signature_round_trip() -> None:
    sig = compute_payment_signature("order_abc", "pay_xyz")
    assert verify_payment_signature("order_abc", "pay_xyz", sig) is True


def test_payment_signature_rejects_forgery() -> None:
    sig = compute_payment_signature("order_abc", "pay_xyz")
    assert verify_payment_signature("order_abc", "pay_OTHER", sig) is False
    assert verify_payment_signature("order_OTHER", "pay_xyz", sig) is False
    assert verify_payment_signature("order_abc", "pay_xyz", "deadbeef") is False


def test_webhook_signature_round_trip() -> None:
    raw = b'{"event":"payment.captured"}'
    sig = compute_webhook_signature(raw)
    assert verify_webhook_signature(raw, sig) is True


def test_webhook_signature_rejects_tamper() -> None:
    raw = b'{"event":"payment.captured"}'
    sig = compute_webhook_signature(raw)
    assert verify_webhook_signature(b'{"event":"order.paid"}', sig) is False
    assert verify_webhook_signature(raw, "00") is False
