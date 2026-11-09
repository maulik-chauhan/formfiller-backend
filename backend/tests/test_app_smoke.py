"""App wiring smoke tests: health + stubbed endpoints return expected codes."""

from __future__ import annotations

from starlette.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_otp_request_unknown_field_rejected_422() -> None:
    # extra="forbid" rejects unknown fields before any handler/DB access.
    r = client.post(
        "/api/v1/auth/otp/request",
        json={"mobile_e164": "+919812345678", "surprise": "x"},
    )
    assert r.status_code == 422


def test_otp_request_bad_mobile_rejected_422() -> None:
    r = client.post("/api/v1/auth/otp/request", json={"mobile_e164": "+15551234567"})
    assert r.status_code == 422


def test_auth_routes_registered() -> None:
    paths = set(create_app().openapi()["paths"])
    for path in (
        "/api/v1/auth/otp/request",
        "/api/v1/auth/otp/verify",
        "/api/v1/auth/token/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/sessions",
        "/api/v1/auth/sessions/{session_id}",
        "/api/v1/auth/account",
    ):
        assert path in paths


def test_esign_disabled_404() -> None:
    r = client.post("/api/v1/esign/sessions", json={})
    assert r.status_code == 404
    assert r.json()["code"] == "ESIGN_MODULE_DISABLED"
