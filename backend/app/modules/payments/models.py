"""Pricing, payments, refunds & webhook events (Tables 14-17)."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
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
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import PaymentOrderStatus, RefundReconciliationAction
from app.db.base import Base


class PricingCatalog(Base):
    __tablename__ = "pricing_catalog"

    sku_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    credit_bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    base_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    esp_surcharge_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    gst_rate_bps: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1800")
    )
    is_gst_inclusive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    total_payable_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    hsn_sac_code: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'998314'")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'INR'"))
    esign_provider_code: Mapped[str | None] = mapped_column(String(40))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    effective_from: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    effective_until: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sku_code: Mapped[str] = mapped_column(
        String(40), ForeignKey("pricing_catalog.sku_code", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("1"))
    base_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    esp_surcharge_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tax_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    refunded_amount_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'INR'"))
    gateway_provider: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'RAZORPAY'")
    )
    gateway_order_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(80), unique=True)
    payment_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{PaymentOrderStatus.CREATED}'")
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    paid_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_payment_orders_user_idempotency"),
        Index("idx_payment_orders_user_created", "user_id", text("created_at DESC")),
    )


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    payment_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payment_orders.id", ondelete="RESTRICT"), nullable=False
    )
    gateway_refund_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    refund_amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    base_component_refunded_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    surcharge_component_refunded_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    tax_component_refunded_paise: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    refund_status: Mapped[str] = mapped_column(String(20), nullable=False)
    wallet_reconciliation_action: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text(f"'{RefundReconciliationAction.NONE}'")
    )
    revoked_lease_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("entitlement_leases.id", ondelete="SET NULL")
    )
    failure_reason: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class PaymentWebhookEvent(Base):
    __tablename__ = "payment_webhook_events"

    gateway_event_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    gateway_provider: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text("'RAZORPAY'")
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway_order_id: Mapped[str | None] = mapped_column(String(80))
    payload_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    processing_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    received_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_payment_webhook_events_received_at", "received_at"),)
