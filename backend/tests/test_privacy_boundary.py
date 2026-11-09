"""Route-aware PrivacyBoundaryMiddleware behaviour (spec Section 6)."""

from __future__ import annotations

import base64

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app.middleware.privacy_boundary import (
    PolicyProfile,
    PrivacyBoundaryMiddleware,
    classify,
)


def _build_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(PrivacyBoundaryMiddleware)

    @app.post("/api/v1/auth/otp/start")
    async def citizen(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/api/v1/admin/templates/{tid}/versions")
    async def admin_upload(tid: str) -> JSONResponse:
        return JSONResponse({"ok": True})

    @app.post("/api/v1/payments/webhooks/razorpay")
    async def webhook(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return TestClient(app)


UUID = "123e4567-e89b-12d3-a456-426614174000"


def test_classify_profiles() -> None:
    assert classify("/api/v1/auth/otp/start", "POST").profile is PolicyProfile.CITIZEN_DEFAULT
    assert (
        classify(f"/api/v1/admin/templates/{UUID}/versions", "POST").profile
        is PolicyProfile.ADMIN_TEMPLATE_UPLOAD
    )
    assert (
        classify("/api/v1/payments/webhooks/razorpay", "POST").profile
        is PolicyProfile.SIGNED_WEBHOOK
    )


def test_citizen_accepts_small_json() -> None:
    client = _build_client()
    r = client.post("/api/v1/auth/otp/start", json={"channel": "MOBILE_SMS"})
    assert r.status_code == 200


def test_citizen_rejects_non_json_content_type() -> None:
    client = _build_client()
    r = client.post(
        "/api/v1/auth/otp/start",
        content=b"hello",
        headers={"content-type": "text/plain"},
    )
    assert r.status_code == 415


def test_citizen_rejects_oversize_body() -> None:
    client = _build_client()
    big = {"blob": "x" * 5000}
    r = client.post("/api/v1/auth/otp/start", json=big)
    assert r.status_code == 413


def test_citizen_rejects_embedded_pdf_data_uri() -> None:
    client = _build_client()
    payload = {"x": "data:application/pdf;base64,JVBERi0xLjcK"}
    r = client.post("/api/v1/auth/otp/start", json=payload)
    assert r.status_code == 422
    assert r.json()["code"] == "PRIVACY_BOUNDARY_VIOLATION"


def test_citizen_rejects_raw_pdf_magic() -> None:
    client = _build_client()
    b64 = base64.b64encode(b"%PDF-1.7 trailing").decode()
    r = client.post("/api/v1/auth/otp/start", json={"doc": b64})
    # Base64 of "%PDF-" is "JVBERi0" which is a forbidden marker.
    assert r.status_code == 422


def test_admin_upload_requires_auth_header() -> None:
    client = _build_client()
    r = client.post(
        f"/api/v1/admin/templates/{UUID}/versions",
        content=b"--boundary--",
        headers={"content-type": "multipart/form-data; boundary=boundary"},
    )
    assert r.status_code == 401


def test_admin_upload_rejects_json_content_type() -> None:
    client = _build_client()
    r = client.post(
        f"/api/v1/admin/templates/{UUID}/versions",
        json={"x": 1},
        headers={"authorization": "Bearer t"},
    )
    assert r.status_code == 415


def test_admin_upload_allows_authenticated_multipart() -> None:
    client = _build_client()
    r = client.post(
        f"/api/v1/admin/templates/{UUID}/versions",
        content=b"--b--",
        headers={
            "authorization": "Bearer t",
            "content-type": "multipart/form-data; boundary=b",
        },
    )
    assert r.status_code == 200


def test_webhook_requires_signature() -> None:
    client = _build_client()
    r = client.post("/api/v1/payments/webhooks/razorpay", json={"event": "x"})
    assert r.status_code == 401


def test_webhook_allows_signed_json() -> None:
    client = _build_client()
    r = client.post(
        "/api/v1/payments/webhooks/razorpay",
        json={"event": "refund.processed"},
        headers={"x-razorpay-signature": "sig"},
    )
    assert r.status_code == 200
