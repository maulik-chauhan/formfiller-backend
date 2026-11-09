"""Unit tests for Ed25519 access tokens and blind-index crypto (no DB)."""

from __future__ import annotations

import uuid

import pytest

from app.config import get_settings
from app.core.constants import UserRole
from app.core.errors import UnauthorizedError
from app.core.hashing import (
    email_blind_index,
    mobile_blind_index,
    sha256_hex,
)
from app.core.security import create_access_token, decode_access_token


def _token() -> str:
    return create_access_token(user_id=uuid.uuid4(), session_id=uuid.uuid4(), role=UserRole.CITIZEN)


def test_valid_token_round_trips_with_expected_claims() -> None:
    uid = uuid.uuid4()
    sid = uuid.uuid4()
    token = create_access_token(user_id=uid, session_id=sid, role=UserRole.CITIZEN)
    claims = decode_access_token(token)
    assert claims["sub"] == str(uid)
    assert claims["sid"] == str(sid)
    assert claims["role"] == "CITIZEN"
    assert claims["iss"] == get_settings().jwt_issuer
    # No PII claim is ever present.
    assert set(claims) == {"iss", "sub", "sid", "role", "iat", "exp"}


def test_invalid_signature_rejected() -> None:
    token = _token()
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(UnauthorizedError):
        decode_access_token(tampered)


def test_expired_token_rejected() -> None:
    settings = get_settings()
    original = settings.access_token_ttl_minutes
    settings.access_token_ttl_minutes = -1
    try:
        token = _token()
    finally:
        settings.access_token_ttl_minutes = original
    with pytest.raises(UnauthorizedError):
        decode_access_token(token)


def test_invalid_issuer_rejected() -> None:
    settings = get_settings()
    original = settings.jwt_issuer
    settings.jwt_issuer = "https://evil.example"
    try:
        token = _token()
    finally:
        settings.jwt_issuer = original
    # Signed by our key, but carries a foreign issuer → rejected.
    with pytest.raises(UnauthorizedError):
        decode_access_token(token)


def test_garbage_token_rejected() -> None:
    with pytest.raises(UnauthorizedError):
        decode_access_token("not-a-jwt")


def test_blind_indexes_deterministic_distinct_and_keyed() -> None:
    a = mobile_blind_index("+919812345678")
    assert a == mobile_blind_index("+919812345678")
    assert len(a) == 64
    assert a != mobile_blind_index("+919800000000")
    # Mobile and email namespaces never collide for similar inputs.
    assert a != email_blind_index("+919812345678")
    # A blind index is NOT a plain SHA-256 of the number.
    assert a != sha256_hex(b"+919812345678")
