"""Ed25519 publisher signing for template bundles.

Published template versions carry an Ed25519 signature over the ASCII bytes of a
64-char lowercase-hex bundle digest. Two digest formats coexist, selected per row
by ``template_versions.signature_format_version``:

* **v1 (legacy)** — ``sha256(f"{template_id}|{semver}|{pdf}|{manifest}|{pages}")``.
  Preserved for historical rows; never re-signed.
* **v2** — domain-separated, length-prefixed, binding the full published identity
  (see :func:`template_bundle_digest_v2`).

Key management: the *active* key signs new publications. An optional *previous*
key keeps historical signatures verifiable across a rotation. Verification always
selects the key by the row's stored ``signing_key_id``; an unknown or retired key
id fails verification and is never silently accepted.

The private seeds come from the environment and are never committed; an ephemeral
active key is generated per process only outside shared environments. This key set
is kept strictly separate from the access-token and entitlement-lease keys.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import get_settings

logger = logging.getLogger("app.signing")

#: Domain-separation tag for the v2 bundle digest.
BUNDLE_V2_DOMAIN = "govforms-template-bundle-v2"
#: Signature format written by new publications.
CURRENT_SIGNATURE_FORMAT_VERSION = 2
SUPPORTED_SIGNATURE_FORMAT_VERSIONS = frozenset({1, 2})
PUBLISHER_KEY_PURPOSE = "template-publisher"


@dataclass(frozen=True)
class PublisherKey:
    """A public template-publisher verification key (never the private half)."""

    kid: str
    public_key_b64: str
    status: str  # "active" | "previous"


def _load_seed(seed_b64: str, env_name: str) -> Ed25519PrivateKey:
    try:
        seed = base64.b64decode(seed_b64, validate=True)
    except ValueError as exc:
        raise RuntimeError(f"{env_name} must be valid base64") from exc
    if len(seed) != 32:
        raise RuntimeError(f"{env_name} must decode to 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(seed)


@lru_cache(maxsize=1)
def _private_key() -> Ed25519PrivateKey:
    seed_b64 = get_settings().template_signing_ed25519_seed_b64
    if seed_b64:
        return _load_seed(seed_b64, "TEMPLATE_SIGNING_ED25519_SEED_B64")
    logger.warning(
        "No TEMPLATE_SIGNING_ED25519_SEED_B64 configured; generating an ephemeral "
        "template-publisher key for this process (development only)."
    )
    return Ed25519PrivateKey.generate()


@lru_cache(maxsize=1)
def _public_key() -> Ed25519PublicKey:
    return _private_key().public_key()


@lru_cache(maxsize=1)
def _previous_public_key() -> tuple[str, Ed25519PublicKey] | None:
    settings = get_settings()
    seed_b64 = settings.template_signing_prev_seed_b64
    kid = settings.template_signing_prev_key_id
    if not seed_b64 and not kid:
        return None
    if not (seed_b64 and kid):
        raise RuntimeError(
            "TEMPLATE_SIGNING_PREV_SEED_B64 and TEMPLATE_SIGNING_PREV_KEY_ID must be set together"
        )
    if kid == settings.template_signing_key_id:
        raise RuntimeError("Previous publisher key id must differ from the active key id")
    return kid, _load_seed(seed_b64, "TEMPLATE_SIGNING_PREV_SEED_B64").public_key()


def reset_key_cache() -> None:
    """Clear cached keys (test helper after changing configuration)."""
    _private_key.cache_clear()
    _public_key.cache_clear()
    _previous_public_key.cache_clear()


def _raw_b64(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw).decode("ascii")


def signing_key_id() -> str:
    return get_settings().template_signing_key_id


def public_key_b64() -> str:
    """Base64 of the raw 32-byte active Ed25519 public key."""
    return _raw_b64(_public_key())


def publisher_key_set() -> list[PublisherKey]:
    """Active (+ optional previous) publisher keys for distribution/rotation.

    This is a discovery aid only. Clients must verify against keys pinned in
    their own build and must never treat this list as a trust anchor.
    """
    keys = [PublisherKey(kid=signing_key_id(), public_key_b64=public_key_b64(), status="active")]
    previous = _previous_public_key()
    if previous is not None:
        keys.append(
            PublisherKey(kid=previous[0], public_key_b64=_raw_b64(previous[1]), status="previous")
        )
    return keys


def _verification_key(kid: str) -> Ed25519PublicKey | None:
    if kid == signing_key_id():
        return _public_key()
    previous = _previous_public_key()
    if previous is not None and kid == previous[0]:
        return previous[1]
    return None


# --------------------------------------------------------------------------- #
# Digest formats
# --------------------------------------------------------------------------- #
def template_bundle_digest_v1(
    *,
    template_id: str,
    semver: str,
    blank_pdf_sha256: str,
    manifest_sha256: str,
    page_count: int,
) -> str:
    """Legacy pipe-joined digest (historical rows only)."""
    preimage = f"{template_id}|{semver}|{blank_pdf_sha256}|{manifest_sha256}|{page_count}"
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


def _netstr(value: str) -> str:
    return f"{len(value.encode('utf-8'))}:{value}"


def template_bundle_digest_v2(
    *,
    template_id: str,
    slug: str,
    jurisdiction: str,
    category: str,
    official_form_code: str,
    semver: str,
    published_version_id: str,
    blank_pdf_sha256: str,
    manifest_sha256: str,
    page_count: int,
) -> str:
    """Domain-separated, UTF-8 byte-length-prefixed digest of the full identity.

    Length-prefixing makes the encoding injective (a ``|`` inside a field cannot
    forge a field boundary) and the domain tag prevents cross-context reuse.
    """
    fields = [
        template_id,
        slug,
        jurisdiction,
        category,
        official_form_code,
        semver,
        published_version_id,
        blank_pdf_sha256,
        manifest_sha256,
        str(page_count),
    ]
    preimage = BUNDLE_V2_DOMAIN + "|" + "|".join(_netstr(f) for f in fields)
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Sign / verify
# --------------------------------------------------------------------------- #
def sign_publisher_digest(digest_hex: str) -> tuple[str, str]:
    """Sign ``digest_hex`` with the active key; return ``(signature_b64, kid)``."""
    signature = _private_key().sign(digest_hex.encode("ascii"))
    return base64.b64encode(signature).decode("ascii"), signing_key_id()


def verify_publisher_digest(digest_hex: str, signature_b64: str, kid: str) -> bool:
    """Verify against the key identified by ``kid``; unknown/retired kid → False."""
    key = _verification_key(kid)
    if key is None:
        return False
    try:
        key.verify(base64.b64decode(signature_b64, validate=True), digest_hex.encode("ascii"))
    except (InvalidSignature, ValueError):
        return False
    return True


def sign_bundle(bundle_sha256_hex: str) -> str:
    """Legacy helper: base64 Ed25519 signature (active key) over the digest's ASCII bytes."""
    return sign_publisher_digest(bundle_sha256_hex)[0]


def verify_bundle(bundle_sha256_hex: str, signature_b64: str) -> bool:
    """Legacy helper: verify against the active key only."""
    return verify_publisher_digest(bundle_sha256_hex, signature_b64, signing_key_id())
