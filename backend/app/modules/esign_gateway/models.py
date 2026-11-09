"""eSign hash-only session table (Table 18) - Milestone 7, gated off in M1-M6."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    ForeignKey,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import EsignSessionStatus
from app.db.base import Base


class EsignHashSession(Base):
    __tablename__ = "esign_hash_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lease_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("entitlement_leases.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    esp_provider_code: Mapped[str] = mapped_column(String(40), nullable=False)
    asp_txn_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # Only the detached ByteRange digest is held — never the document contents.
    document_byterange_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    generic_doc_info: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{EsignSessionStatus.INITIATED}'")
    )
    detached_pkcs7_signature: Mapped[str | None] = mapped_column(Text)
    esp_error_code: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
