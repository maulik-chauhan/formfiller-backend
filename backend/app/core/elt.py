"""Ed25519 Entitlement Lease Token (ELT) signing, verification & key discovery.

The ELT is a compact EdDSA JWS carrying only non-PII entitlement claims (spec
§8.2). It is verifiable fully offline using the public keys published by
``GET /api/v1/entitlements/public-keys``. Key rotation is supported: an active
signing key issues new tokens, and a configured previous key keeps still-valid
tokens verifiable until they expire. Private seeds come from the environment and
are never exposed.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import get_settings

logger = logging.getLogger("app.elt")

_ELT_TYP = "ELT+jwt"
_ELT_ALG = "EdDSA"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


@dataclass(frozen=True)
class EltKey:
    key_id: str
    private_key: Ed25519PrivateKey | None  # None for verify-only (previous) keys
    public_key: Ed25519PublicKey
    status: str  # "active" | "previous"


def _key_from_seed(seed_b64: str) -> Ed25519PrivateKey:
    seed = base64.b64decode(seed_b64)
    if len(seed) != 32:
        raise RuntimeError("Ed25519 seed must decode to 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


@lru_cache(maxsize=1)
def _active_key() -> EltKey:
    settings = get_settings()
    if settings.lease_signing_seed_b64:
        priv = _key_from_seed(settings.lease_signing_seed_b64)
    else:
        logger.warning(
            "No LEASE_SIGNING_SEED_B64 configured; generating an ephemeral ELT "
            "signing key for this process (development only)."
        )
        priv = Ed25519PrivateKey.generate()
    return EltKey(settings.lease_signing_key_id, priv, priv.public_key(), "active")


@lru_cache(maxsize=1)
def _previous_key() -> EltKey | None:
    settings = get_settings()
    if settings.lease_signing_prev_seed_b64 and settings.lease_signing_prev_key_id:
        priv = _key_from_seed(settings.lease_signing_prev_seed_b64)
        return EltKey(settings.lease_signing_prev_key_id, priv, priv.public_key(), "previous")
    return None


def reset_key_cache() -> None:
    """Clear cached keys (test helper after changing configuration)."""
    _active_key.cache_clear()
    _previous_key.cache_clear()


def active_key_id() -> str:
    return _active_key().key_id


def _verification_keys() -> dict[str, EltKey]:
    keys = {_active_key().key_id: _active_key()}
    prev = _previous_key()
    if prev is not None:
        keys[prev.key_id] = prev
    return keys


def public_key_set() -> list[dict[str, str]]:
    """Public keys for offline verification (never private material)."""
    out: list[dict[str, str]] = []
    for key in _verification_keys().values():
        raw = key.public_key.public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
        out.append(
            {
                "kid": key.key_id,
                "alg": _ELT_ALG,
                "crv": "Ed25519",
                "public_key_b64": base64.b64encode(raw).decode("ascii"),
                "status": key.status,
            }
        )
    return out


def sign_elt(claims: dict[str, Any]) -> str:
    """Sign ELT ``claims`` with the active key and return the compact JWS."""
    key = _active_key()
    assert key.private_key is not None
    header = {"alg": _ELT_ALG, "typ": _ELT_TYP, "kid": key.key_id}
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(claims, separators=(',', ':')).encode())}"
    )
    signature = key.private_key.sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64url_encode(signature)}"


def verify_elt(token: str) -> dict[str, Any]:
    """Verify an ELT against the active or previous key; return its claims.

    Raises :class:`ValueError` on any failure. Used for offline-equivalent
    server-side verification and in tests.
    """
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(_b64url_decode(header_b64))
        kid = header.get("kid")
        key = _verification_keys().get(kid)
        if key is None:
            raise ValueError("Unknown signing key id")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        key.public_key.verify(_b64url_decode(sig_b64), signing_input)
        claims: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
    except (ValueError, InvalidSignature, json.JSONDecodeError) as exc:
        raise ValueError("Invalid ELT") from exc
    return claims
