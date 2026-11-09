"""Thin helper for appending audit-log rows (append-only; never updated)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import UserRole
from app.modules.audit.models import AuditLog


async def record_event(
    session: AsyncSession,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str,
    request_id: str,
    actor_user_id: uuid.UUID | None = None,
    actor_role: UserRole = UserRole.CITIZEN,
    ip_subnet_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    """Append a single audit row. Caller controls the transaction boundary."""
    row = AuditLog(
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=request_id,
        actor_user_id=actor_user_id,
        actor_role=str(actor_role),
        ip_subnet_hash=ip_subnet_hash,
        metadata_jsonb=metadata or {},
    )
    session.add(row)
    return row
