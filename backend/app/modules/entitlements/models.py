"""Entitlement wallets, leases & ledger (Tables 11, 12, 13)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import LeaseStatus
from app.db.base import Base


class EntitlementWallet(Base):
    __tablename__ = "entitlement_wallets"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    free_quota_total: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    free_quota_used: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    free_quota_reserved: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    paid_std_base_available: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    paid_std_base_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    paid_sig_bundle_available: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    paid_sig_bundle_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    paid_sig_upgrade_available: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    paid_sig_upgrade_reserved: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    esign_credits_by_sku: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    post_consumption_refund_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    version_lock: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EntitlementLease(Base):
    __tablename__ = "entitlement_leases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    base_credit_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    is_upgraded_with_sig_30: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    lease_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text(f"'{LeaseStatus.ISSUED}'")
    )
    device_fingerprint_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    draft_instance_id_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    jwt_jti: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    signing_key_id: Mapped[str] = mapped_column(String(32), nullable=False)
    client_completion_receipt_hash: Mapped[str | None] = mapped_column(CHAR(64))
    commit_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    create_idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    issued_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    reservation_expires_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    first_committed_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    correction_window_expires_at: Mapped[dt.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    last_committed_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    # Diagnostic only; NEVER authoritative (see spec 3.3).
    client_reported_completed_at: Mapped[dt.datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )
    released_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "user_id", "create_idempotency_key", name="uq_leases_user_create_idempotency"
        ),
        Index(
            "uq_active_lease_per_draft",
            "user_id",
            "template_version_id",
            "draft_instance_id_hash",
            unique=True,
            postgresql_where=text("lease_status IN ('ISSUED', 'COMMITTED')"),
        ),
        Index(
            "idx_leases_reaper_expired",
            "reservation_expires_at",
            postgresql_where=text("lease_status = 'ISSUED'"),
        ),
    )


class EntitlementLedgerEntry(Base):
    __tablename__ = "entitlement_ledger_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[str] = mapped_column(String(32), nullable=False)
    credit_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    delta_available: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    delta_reserved: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    available_balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_payment_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_orders.id", ondelete="RESTRICT")
    )
    reference_refund_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_refunds.id", ondelete="RESTRICT")
    )
    reference_lease_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entitlement_leases.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_entry_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "sequence_no", name="uq_ledger_user_seq"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_ledger_user_idempotency"),
        Index(
            "uq_ledger_paid_credit_per_order",
            "reference_payment_order_id",
            unique=True,
            postgresql_where=text("entry_type = 'PAID_CREDIT'"),
        ),
    )
