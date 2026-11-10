"""Storage port (static + expiring URLs) and shared-environment config validation."""

from __future__ import annotations

import base64
import time

import pytest

from app.config import Settings, get_settings
from app.modules.templates.storage import (
    LocalObjectStorage,
    MockSignedUrlStorage,
    get_storage,
    object_key_for,
)

SHA = "a" * 64


def _seed(start: int) -> str:
    return base64.b64encode(bytes(range(start, start + 32))).decode()


def _shared(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "staging",
        "blind_index_hmac_key": "real-blind",
        "otp_hmac_key": "real-otp",
        "ip_subnet_hash_key": "real-ip",
        "access_jwt_ed25519_seed_b64": _seed(0),
        "lease_signing_seed_b64": _seed(40),
        "template_signing_ed25519_seed_b64": _seed(80),
        "cors_allowed_origins": "https://app.example.test",
        "template_storage_provider": "local",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_object_key_is_content_addressed() -> None:
    assert object_key_for(SHA) == f"blank_pdfs/{SHA}.pdf"


def test_local_storage_urls_are_static_and_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEMPLATE_STORAGE_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        storage = LocalObjectStorage()
        assert storage.supports_expiring_urls is False
        key = storage.store_blank_pdf(object_key=object_key_for(SHA), data=b"%PDF-1.7 x")
        assert storage.read_blank_pdf(key) == b"%PDF-1.7 x"
        assert storage.read_blank_pdf(object_key_for("b" * 64)) is None
        url = storage.build_download_url(key, expires_in_seconds=10)
        assert url == f"{get_settings().template_cdn_base_url}/blank_pdfs/{SHA}.pdf"
        assert "?" not in url
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_mock_signed_urls_expire_and_detect_tampering() -> None:
    storage = MockSignedUrlStorage()
    assert storage.supports_expiring_urls is True
    key = object_key_for(SHA)
    url = storage.build_download_url(key, expires_in_seconds=60)
    assert url.startswith(f"{get_settings().template_cdn_base_url}/blank_pdfs/{SHA}.pdf?expires=")
    assert storage.verify_download_url(url)
    assert not storage.verify_download_url(url, now=time.time() + 61)
    assert not storage.verify_download_url(url.replace(SHA, "b" * 64))
    assert not storage.verify_download_url(url[:-1] + ("0" if url[-1] != "0" else "1"))
    assert not storage.verify_download_url(url.split("?")[0])
    assert not storage.verify_download_url(url.replace("https://", "https://evil."))


def test_provider_selection(monkeypatch) -> None:
    try:
        assert type(get_storage()) is LocalObjectStorage
        monkeypatch.setenv("TEMPLATE_STORAGE_PROVIDER", "mock_signed")
        get_settings.cache_clear()
        assert type(get_storage()) is MockSignedUrlStorage
        monkeypatch.setenv("TEMPLATE_STORAGE_PROVIDER", "s3")
        get_settings.cache_clear()
        with pytest.raises(RuntimeError, match="DEC-3"):
            get_storage()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_cors_origin_list_parsing() -> None:
    s = Settings(_env_file=None, cors_allowed_origins=" https://a.test/ , http://localhost:8080 ,")
    assert s.cors_allowed_origins_list == ["https://a.test", "http://localhost:8080"]
    assert Settings(_env_file=None, cors_allowed_origins="").cors_allowed_origins_list == []


def test_local_environments_are_exempt_from_secret_validation() -> None:
    Settings(_env_file=None, environment="development").validate_production_secrets()
    Settings(_env_file=None, environment="test").validate_production_secrets()


def test_valid_shared_environment_passes() -> None:
    _shared().validate_production_secrets()
    _shared(environment="production").validate_production_secrets()


@pytest.mark.parametrize(
    "missing",
    [
        "access_jwt_ed25519_seed_b64",
        "lease_signing_seed_b64",
        "template_signing_ed25519_seed_b64",
        "otp_hmac_key",
    ],
)
def test_staging_and_production_require_every_secret(missing: str) -> None:
    for environment in ("staging", "production"):
        with pytest.raises(RuntimeError, match="Missing/insecure"):
            _shared(environment=environment, **{missing: ""}).validate_production_secrets()


def test_default_dev_hmac_keys_are_rejected() -> None:
    with pytest.raises(RuntimeError, match="Missing/insecure"):
        _shared(blind_index_hmac_key="dev-blind-index-key-change-me").validate_production_secrets()


def test_seeds_must_be_32_bytes_and_valid_base64() -> None:
    short = base64.b64encode(b"short").decode()
    with pytest.raises(RuntimeError, match="32 bytes"):
        _shared(lease_signing_seed_b64=short).validate_production_secrets()
    with pytest.raises(RuntimeError, match="base64"):
        _shared(template_signing_ed25519_seed_b64="!!not-base64!!").validate_production_secrets()


def test_signing_seeds_must_be_mutually_distinct() -> None:
    for a, b in (
        ("lease_signing_seed_b64", "access_jwt_ed25519_seed_b64"),
        ("template_signing_ed25519_seed_b64", "access_jwt_ed25519_seed_b64"),
        ("template_signing_ed25519_seed_b64", "lease_signing_seed_b64"),
    ):
        settings = _shared()
        clash = _shared(**{a: getattr(settings, b)})
        with pytest.raises(RuntimeError, match="distinct"):
            clash.validate_production_secrets()
    with pytest.raises(RuntimeError, match="Previous publisher seed"):
        _shared(
            template_signing_prev_seed_b64=_seed(0), template_signing_prev_key_id="old"
        ).validate_production_secrets()


def test_production_rejects_wildcard_http_origins_and_mock_storage() -> None:
    for origins in ("*", "https://*.example.test", "http://app.example.test"):
        with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
            _shared(
                environment="production", cors_allowed_origins=origins
            ).validate_production_secrets()
    with pytest.raises(RuntimeError, match="TEMPLATE_STORAGE_PROVIDER"):
        _shared(
            environment="production", template_storage_provider="mock_signed"
        ).validate_production_secrets()
    # Staging may use plain-http preview origins; production may not.
    _shared(cors_allowed_origins="http://preview.internal:8080").validate_production_secrets()
