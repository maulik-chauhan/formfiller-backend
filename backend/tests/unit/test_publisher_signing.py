"""Unit tests for Ed25519 template-bundle publisher signing."""

from __future__ import annotations

from app.core.signing import public_key_b64, sign_bundle, signing_key_id, verify_bundle

BUNDLE = "a" * 64


def test_sign_verify_round_trip() -> None:
    sig = sign_bundle(BUNDLE)
    assert verify_bundle(BUNDLE, sig) is True


def test_signature_rejects_tampered_bundle() -> None:
    sig = sign_bundle(BUNDLE)
    assert verify_bundle("b" * 64, sig) is False


def test_signature_rejects_garbage() -> None:
    assert verify_bundle(BUNDLE, "not-base64-sig!") is False


def test_public_key_and_kid_available() -> None:
    assert len(public_key_b64()) > 0
    assert signing_key_id() == "ed25519-template-2026-v1"
