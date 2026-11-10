"""CORS: explicit origins only, no credentials, outermost so errors stay readable."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from app.config import get_settings

ALLOWED = "http://localhost:8080"


@pytest.fixture
def cors_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{ALLOWED}, https://preview.example.test")
    get_settings.cache_clear()
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client
    monkeypatch.undo()
    get_settings.cache_clear()


def _preflight(
    client: TestClient, origin: str, method: str = "POST", headers: str = "content-type"
):
    return client.options(
        "/api/v1/auth/otp/request",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


def test_preflight_allowed_origin(cors_client: TestClient) -> None:
    r = _preflight(cors_client, ALLOWED, headers="authorization,content-type,idempotency-key")
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert "access-control-allow-credentials" not in r.headers
    assert {m.strip() for m in r.headers["access-control-allow-methods"].split(",")} == {
        "GET",
        "POST",
        "DELETE",
        "OPTIONS",
    }


def test_preflight_denies_unknown_origin_method_and_header(cors_client: TestClient) -> None:
    assert "access-control-allow-origin" not in _preflight(cors_client, "https://evil.test").headers
    assert _preflight(cors_client, ALLOWED, method="PUT").status_code == 400
    assert _preflight(cors_client, ALLOWED, headers="x-evil").status_code == 400


def test_simple_request_echoes_only_allowed_origin(cors_client: TestClient) -> None:
    ok = cors_client.get("/api/v1/health/live", headers={"Origin": ALLOWED})
    assert ok.headers["access-control-allow-origin"] == ALLOWED
    assert ok.headers.get("access-control-allow-credentials") is None
    bad = cors_client.get("/api/v1/health/live", headers={"Origin": "https://evil.test"})
    assert "access-control-allow-origin" not in bad.headers


def test_privacy_boundary_rejections_remain_cors_readable(cors_client: TestClient) -> None:
    r = cors_client.post(
        "/api/v1/auth/otp/request",
        content=b"x" * 5000,
        headers={"Origin": ALLOWED, "Content-Type": "application/json"},
    )
    assert r.status_code == 413
    assert r.headers["access-control-allow-origin"] == ALLOWED
    multipart = cors_client.post(
        "/api/v1/auth/otp/request", files={"f": ("a.pdf", b"%PDF-1.7")}, headers={"Origin": ALLOWED}
    )
    assert multipart.status_code == 415


def test_no_cors_headers_when_unconfigured() -> None:
    from app.main import create_app

    with TestClient(create_app()) as client:
        r = client.get("/api/v1/health/live", headers={"Origin": ALLOWED})
        assert r.status_code == 200
        assert "access-control-allow-origin" not in r.headers
