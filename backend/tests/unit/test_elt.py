"""Unit tests for Ed25519 Entitlement Lease Token signing, verify & rotation."""

from __future__ import annotations

import base64

import pytest

from app.config import get_settings
from app.core import elt


def _claims() -> dict[str, object]:
    return {
        "iss": "https://api.govforms.in",
        "sub": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        "jti": "c72a1d90-55e1-4c88-b912-887766554433",
        "tid": "4f8b9c12-7a11-4e20-9c3d-1a2b3c4d5e6f",
        "tier": "FREE_UPGRADED_SIG_50",
        "st": "ISSUED",
        "dfd": "a" * 64,
        "did": "b" * 64,
        "cap": {"pdf": True, "sig_img": True, "esign": False},
        "cwe_hours": 24,
        "iat": 1794213900,
        "exp": 1794386700,
    }


def test_sign_verify_round_trip() -> None:
    token = elt.sign_elt(_claims())
    claims = elt.verify_elt(token)
    assert claims["tier"] == "FREE_UPGRADED_SIG_50"
    assert claims["cap"]["sig_img"] is True


def test_tampered_token_rejected() -> None:
    token = elt.sign_elt(_claims())
    bad = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(ValueError):
        elt.verify_elt(bad)


def test_unknown_kid_rejected() -> None:
    header = base64.urlsafe_b64encode(b'{"alg":"EdDSA","typ":"ELT+jwt","kid":"nope"}').rstrip(b"=")
    forged = header.decode() + ".e30.AAAA"
    with pytest.raises(ValueError):
        elt.verify_elt(forged)


def test_public_key_set_has_no_private_material() -> None:
    keys = elt.public_key_set()
    assert keys and keys[0]["status"] == "active"
    blob = str(keys)
    assert "private" not in blob.lower()
    assert "seed" not in blob.lower()


def test_key_rotation_keeps_previous_tokens_verifiable() -> None:
    settings = get_settings()
    old_seed = settings.lease_signing_seed_b64
    old_kid = settings.lease_signing_key_id
    try:
        # Sign a token under the original (soon-to-be previous) key X.
        token_x = elt.sign_elt(_claims())

        # Rotate: Y becomes active, X becomes the previous verification key.
        new_seed = base64.b64encode(bytes(range(96, 128))).decode()
        settings.lease_signing_seed_b64 = new_seed
        settings.lease_signing_key_id = "ed25519-elt-2027-v1"
        settings.lease_signing_prev_seed_b64 = old_seed
        settings.lease_signing_prev_key_id = old_kid
        elt.reset_key_cache()

        token_y = elt.sign_elt(_claims())
        # Both the new active token and the old previous-key token verify.
        assert elt.verify_elt(token_y)["tier"] == "FREE_UPGRADED_SIG_50"
        assert elt.verify_elt(token_x)["tier"] == "FREE_UPGRADED_SIG_50"
        assert {k["status"] for k in elt.public_key_set()} == {"active", "previous"}
    finally:
        settings.lease_signing_seed_b64 = old_seed
        settings.lease_signing_key_id = old_kid
        settings.lease_signing_prev_seed_b64 = ""
        settings.lease_signing_prev_key_id = ""
        elt.reset_key_cache()
