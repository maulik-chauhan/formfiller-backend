"""Append-only operational & security audit log (Table 19)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    ip_subnet_hash: Mapped[str | None] = mapped_column(CHAR(64))
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index("idx_audit_logs_occurred_at", text("occurred_at DESC")),
        Index(
            "idx_audit_logs_resource",
            "resource_type",
            "resource_id",
            text("occurred_at DESC"),
        ),
        Index("idx_audit_logs_actor", "actor_user_id", text("occurred_at DESC")),
    )
