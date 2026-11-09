"""Ed25519 publisher signing for template bundles.

Published template versions carry an Ed25519 signature over their canonical
``bundle_sha256``. The private seed comes from the environment and is never
committed; an ephemeral key is generated per process in non-production when no
seed is configured. Kept separate from the access-token and lease keys.
"""

from __future__ import annotations

import base64
import logging
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import get_settings

logger = logging.getLogger("app.signing")


@lru_cache(maxsize=1)
def _private_key() -> Ed25519PrivateKey:
    seed_b64 = get_settings().template_signing_ed25519_seed_b64
    if seed_b64:
        seed = base64.b64decode(seed_b64)
        if len(seed) != 32:
            raise RuntimeError("TEMPLATE_SIGNING_ED25519_SEED_B64 must decode to 32 bytes")
        return Ed25519PrivateKey.from_private_bytes(seed)
    logger.warning(
        "No TEMPLATE_SIGNING_ED25519_SEED_B64 configured; generating an ephemeral "
        "template-publisher key for this process (development only)."
    )
    return Ed25519PrivateKey.generate()


@lru_cache(maxsize=1)
def _public_key() -> Ed25519PublicKey:
    return _private_key().public_key()


def reset_key_cache() -> None:
    """Clear cached keys (test helper after changing configuration)."""
    _private_key.cache_clear()
    _public_key.cache_clear()


def signing_key_id() -> str:
    return get_settings().template_signing_key_id


def public_key_b64() -> str:
    """Base64 of the raw 32-byte Ed25519 public key."""
    from cryptography.hazmat.primitives import serialization

    raw = _public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def sign_bundle(bundle_sha256_hex: str) -> str:
    """Return a base64 Ed25519 signature over the bundle digest's ASCII bytes."""
    signature = _private_key().sign(bundle_sha256_hex.encode("ascii"))
    return base64.b64encode(signature).decode("ascii")


def verify_bundle(bundle_sha256_hex: str, signature_b64: str) -> bool:
    """Verify a base64 Ed25519 signature over the bundle digest."""
    try:
        _public_key().verify(base64.b64decode(signature_b64), bundle_sha256_hex.encode("ascii"))
    except (InvalidSignature, ValueError):
        return False
    return True
