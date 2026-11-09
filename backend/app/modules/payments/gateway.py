"""Razorpay gateway abstraction and signature verification.

Signature verification is pure, deterministic crypto (HMAC-SHA256 with the
server-side secret, constant-time compare) and is used identically in test and
production. Only the two *network* operations — order creation and refund
initiation — differ between the real HTTP gateway and the in-memory fake used by
automated tests. Secrets come from configuration and are never logged or
returned in responses.
"""

from __future__ import annotations

import abc
import hashlib
import hmac
import uuid
from typing import Any

import httpx

from app.config import get_settings
from app.core.errors import GatewayError


# --------------------------------------------------------------------------- #
# Signature verification (shared by sync verify and webhook)
# --------------------------------------------------------------------------- #
def compute_payment_signature(order_id: str, payment_id: str) -> str:
    """Razorpay checkout signature: HMAC-SHA256(secret, '<order>|<payment>')."""
    secret = get_settings().razorpay_key_secret.encode("utf-8")
    msg = f"{order_id}|{payment_id}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    return hmac.compare_digest(compute_payment_signature(order_id, payment_id), signature)


def compute_webhook_signature(raw_body: bytes) -> str:
    """Razorpay webhook signature: HMAC-SHA256(webhook_secret, raw_body)."""
    secret = get_settings().razorpay_webhook_secret.encode("utf-8")
    return hmac.new(secret, raw_body, hashlib.sha256).hexdigest()


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    return hmac.compare_digest(compute_webhook_signature(raw_body), signature)


# --------------------------------------------------------------------------- #
# Gateway network operations
# --------------------------------------------------------------------------- #
class RazorpayGateway(abc.ABC):
    """Port for the two Razorpay network calls M6 performs server-side."""

    @abc.abstractmethod
    async def create_order(
        self, *, amount_paise: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict[str, Any]:
        """Create a Razorpay order; returns at least ``{"id": ...}``."""

    @abc.abstractmethod
    async def initiate_refund(
        self, *, payment_id: str, amount_paise: int, notes: dict[str, str]
    ) -> dict[str, Any]:
        """Initiate a refund; returns at least ``{"id": ..., "status": ...}``."""


class HttpRazorpayGateway(RazorpayGateway):
    """Real Razorpay REST gateway (used only in live/http mode)."""

    def __init__(self) -> None:
        s = get_settings()
        if not (s.razorpay_key_id and s.razorpay_key_secret):
            raise GatewayError("Razorpay credentials are not configured")
        self._base = s.razorpay_api_base.rstrip("/")
        self._auth = (s.razorpay_key_id, s.razorpay_key_secret)
        self._timeout = s.razorpay_http_timeout_seconds

    async def create_order(
        self, *, amount_paise: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict[str, Any]:
        payload = {"amount": amount_paise, "currency": currency, "receipt": receipt, "notes": notes}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base}/orders", json=payload, auth=self._auth)
                resp.raise_for_status()
                return dict(resp.json())
        except httpx.HTTPError as exc:  # timeouts, connection errors, 4xx/5xx
            raise GatewayError("Razorpay order creation failed") from exc

    async def initiate_refund(
        self, *, payment_id: str, amount_paise: int, notes: dict[str, str]
    ) -> dict[str, Any]:
        payload = {"amount": amount_paise, "notes": notes}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/payments/{payment_id}/refund", json=payload, auth=self._auth
                )
                resp.raise_for_status()
                return dict(resp.json())
        except httpx.HTTPError as exc:
            raise GatewayError("Razorpay refund initiation failed") from exc


class FakeRazorpayGateway(RazorpayGateway):
    """Deterministic in-memory gateway for development and automated tests."""

    async def create_order(
        self, *, amount_paise: int, currency: str, receipt: str, notes: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "id": f"order_{uuid.uuid4().hex[:18]}",
            "amount": amount_paise,
            "currency": currency,
            "receipt": receipt,
            "status": "created",
        }

    async def initiate_refund(
        self, *, payment_id: str, amount_paise: int, notes: dict[str, str]
    ) -> dict[str, Any]:
        return {
            "id": f"rfnd_{uuid.uuid4().hex[:18]}",
            "payment_id": payment_id,
            "amount": amount_paise,
            "status": "processed",
        }


def get_gateway() -> RazorpayGateway:
    """Resolve the configured Razorpay gateway (fake in tests/dev, http in prod)."""
    if get_settings().razorpay_use_fake_gateway:
        return FakeRazorpayGateway()
    return HttpRazorpayGateway()
