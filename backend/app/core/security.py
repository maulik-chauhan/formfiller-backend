"""Ed25519 (EdDSA) access-token signing/verification and bearer extraction.

Access tokens are compact JWS (JWT) signed with Ed25519. Only non-PII claims are
carried: ``sub`` (user id), ``sid`` (session id), ``role``, ``iat``, ``exp`` and
``iss``. The private signing seed comes from the environment and is never
committed; in a non-production environment an ephemeral keypair is generated per
process so local development and tests work without a configured key.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import uuid
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import get_settings
from app.core.constants import UserRole
from app.core.errors import UnauthorizedError

logger = logging.getLogger("app.security")

_ACCESS_TYP = "at+jwt"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


@lru_cache(maxsize=1)
def _signing_key() -> Ed25519PrivateKey:
    """Return the process Ed25519 signing key.

    Uses the configured base64 32-byte seed when present; otherwise, in a
    non-production environment, generates an ephemeral key (logged as a warning).
    Production without a configured seed is rejected at startup by
    ``Settings.validate_production_secrets``.
    """
    settings = get_settings()
    seed_b64 = settings.access_jwt_ed25519_seed_b64
    if seed_b64:
        seed = base64.b64decode(seed_b64)
        if len(seed) != 32:
            raise RuntimeError("ACCESS_JWT_ED25519_SEED_B64 must decode to 32 bytes")
        return Ed25519PrivateKey.from_private_bytes(seed)
    logger.warning(
        "No ACCESS_JWT_ED25519_SEED_B64 configured; generating an ephemeral "
        "access-token key for this process (development only)."
    )
    return Ed25519PrivateKey.generate()


@lru_cache(maxsize=1)
def _verify_key() -> Ed25519PublicKey:
    return _signing_key().public_key()


def reset_key_cache() -> None:
    """Clear cached keys (used by tests after changing configuration)."""
    _signing_key.cache_clear()
    _verify_key.cache_clear()


def create_access_token(*, user_id: uuid.UUID, session_id: uuid.UUID, role: UserRole) -> str:
    """Mint a 15-minute Ed25519-signed access token with non-PII claims only."""
    settings = get_settings()
    issued = _now()
    header = {"alg": "EdDSA", "typ": _ACCESS_TYP, "kid": settings.access_jwt_kid}
    payload: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "sub": str(user_id),
        "sid": str(session_id),
        "role": str(role),
        "iat": int(issued.timestamp()),
        "exp": int((issued + dt.timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
    }
    signing_input = (
        f"{_b64url_encode(json.dumps(header, separators=(',', ':')).encode())}."
        f"{_b64url_encode(json.dumps(payload, separators=(',', ':')).encode())}"
    )
    signature = _signing_key().sign(signing_input.encode("ascii"))
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify signature, issuer and expiry; return the claims.

    Raises :class:`UnauthorizedError` on any failure, never echoing the token.
    """
    settings = get_settings()
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
        _verify_key().verify(_b64url_decode(sig_b64), signing_input)
        claims: dict[str, Any] = json.loads(_b64url_decode(payload_b64))
    except (ValueError, InvalidSignature, json.JSONDecodeError) as exc:
        raise UnauthorizedError("Invalid access token") from exc

    if claims.get("iss") != settings.jwt_issuer:
        raise UnauthorizedError("Invalid token issuer")
    exp = claims.get("exp")
    if not isinstance(exp, int) or exp < int(_now().timestamp()):
        raise UnauthorizedError("Access token expired")
    for required in ("sub", "sid", "role"):
        if required not in claims:
            raise UnauthorizedError("Malformed access token")
    return claims


def extract_bearer(authorization_header: str | None) -> str:
    """Return the raw token from an ``Authorization: Bearer <token>`` header."""
    if not authorization_header or not authorization_header.lower().startswith("bearer "):
        raise UnauthorizedError("Missing bearer token")
    return authorization_header.split(" ", 1)[1].strip()
