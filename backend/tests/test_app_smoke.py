"""App wiring smoke tests: health + stubbed endpoints return expected codes."""

from __future__ import annotations

from starlette.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_otp_start_is_stubbed_501() -> None:
    r = client.post("/api/v1/auth/otp/start", json={"channel": "MOBILE_SMS"})
    assert r.status_code == 501
    assert r.json()["code"] == "NOT_IMPLEMENTED"


def test_unknown_field_rejected_422() -> None:
    r = client.post(
        "/api/v1/auth/otp/start",
        json={"channel": "MOBILE_SMS", "surprise": "x"},
    )
    assert r.status_code == 422


def test_esign_disabled_404() -> None:
    r = client.post("/api/v1/esign/sessions", json={})
    assert r.status_code == 404
    assert r.json()["code"] == "ESIGN_MODULE_DISABLED"
