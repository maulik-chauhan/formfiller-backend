"""Reusable FastAPI auth dependencies and RBAC guards.

Every authenticated request is validated against BOTH the signed access token
and live database state: the session must still be active (not revoked, not
expired) and the account must be ACTIVE. A role claim alone is never trusted —
a subsequently suspended account or revoked session is rejected even if its
previously-issued token has not yet expired.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import AccountStatus, UserRole
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.hashing import ip_subnet_hash
from app.core.security import decode_access_token, extract_bearer
from app.db.session import get_session
from app.modules.auth.models import AuthSession, User


@dataclass(frozen=True)
class RequestContext:
    """Per-request, non-PII context for rate-limiting and audit."""

    ip_subnet_hash: str
    request_id: str


def get_request_context(request: Request) -> RequestContext:
    """Derive the coarse IP-subnet hash and a request id (never the raw IP)."""
    client_ip = request.client.host if request.client else "0.0.0.0"
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    return RequestContext(ip_subnet_hash=ip_subnet_hash(client_ip), request_id=request_id[:64])


@dataclass(frozen=True)
class AuthContext:
    """Resolved, DB-validated authenticated principal for a request."""

    user: User
    session: AuthSession
    role: UserRole


async def _resolve_auth(request: Request, session: AsyncSession) -> AuthContext:
    token = extract_bearer(request.headers.get("authorization"))
    claims = decode_access_token(token)

    try:
        session_id = uuid.UUID(claims["sid"])
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError("Malformed access token") from exc

    auth_session = await session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != user_id:
        raise UnauthorizedError("Session not found")
    if auth_session.revoked_at is not None:
        raise UnauthorizedError("Session revoked")
    if auth_session.expires_at <= dt.datetime.now(tz=dt.UTC):
        raise UnauthorizedError("Session expired")

    user = await session.get(User, user_id)
    if user is None:
        raise UnauthorizedError("User not found")
    if user.account_status != AccountStatus.ACTIVE:
        raise UnauthorizedError("Account is not active")

    return AuthContext(user=user, session=auth_session, role=UserRole(user.role))


async def get_auth_context(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    """Full authenticated context (user + live session + role)."""
    return await _resolve_auth(request, session)


async def get_current_user(ctx: AuthContext = Depends(get_auth_context)) -> User:
    return ctx.user


async def get_current_session(ctx: AuthContext = Depends(get_auth_context)) -> AuthSession:
    return ctx.session


async def require_authenticated_user(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    """Dependency asserting a valid authenticated principal."""
    return ctx


def require_roles(*roles: UserRole) -> Callable[..., Awaitable[AuthContext]]:
    """Return a dependency that requires the user hold one of ``roles``.

    The role is taken from the live ``users`` row (re-validated above), not from
    the token claim alone.
    """
    allowed = frozenset(roles)

    async def _guard(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if ctx.role not in allowed:
            raise ForbiddenError("Insufficient role for this operation")
        return ctx

    return _guard
