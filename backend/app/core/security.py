"""JWT access/refresh helpers and the admin-upload authentication gate."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, cast

from jose import JWTError, jwt

from app.config import get_settings
from app.core.constants import UserRole
from app.core.errors import UnauthorizedError


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def create_access_token(*, user_id: uuid.UUID, role: UserRole, jti: str | None = None) -> str:
    """Mint a short-lived access JWT carrying the user's role."""
    settings = get_settings()
    issued = _now()
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": str(role),
        "jti": jti or str(uuid.uuid4()),
        "iat": int(issued.timestamp()),
        "exp": int((issued + dt.timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
        "typ": "access",
    }
    return cast(str, jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm))


def decode_token(token: str) -> dict[str, Any]:
    """Decode and verify a JWT, raising :class:`UnauthorizedError` on failure."""
    settings = get_settings()
    try:
        decoded = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:  # pragma: no cover - thin wrapper
        raise UnauthorizedError("Invalid or expired token") from exc
    return cast(dict[str, Any], decoded)


def extract_bearer(authorization_header: str | None) -> str:
    """Return the raw token from an ``Authorization: Bearer <token>`` header."""
    if not authorization_header or not authorization_header.lower().startswith("bearer "):
        raise UnauthorizedError("Missing bearer token")
    return authorization_header.split(" ", 1)[1].strip()
