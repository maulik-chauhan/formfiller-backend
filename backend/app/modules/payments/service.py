"""Payment services: pricing, order creation, exactly-once fulfilment,
refund reconciliation (7 scenarios), and signed webhook handling.

Monetary values are computed server-side from ``pricing_catalog`` in integer
paise; a client-supplied amount is never authoritative. Fulfilment and refund
reconciliation lock ``payment_orders`` then ``entitlement_wallets`` (then the
eligible ``entitlement_leases`` row for reserved-credit refunds), append the
hash-chained ledger, write an allowlisted audit event, and commit atomically.
Exactly-once wallet crediting is guaranteed by ``SELECT ... FOR UPDATE`` on the
order plus the partial unique index ``uq_ledger_paid_credit_per_order``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.constants import (
    CreditBucket,
    LeaseStatus,
    LedgerEntryType,
    PaymentOrderStatus,
    RefundReconciliationAction,
    RefundRecordStatus,
    WebhookProcessingOutcome,
)
from app.core.dependencies import AuthContext, RequestContext
from app.core.errors import (
    ForbiddenError,
    GatewayError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from app.modules.audit import service as audit
from app.modules.entitlements.models import EntitlementLease, EntitlementWallet
from app.modules.entitlements.service import (
    append_ledger,
    bucket_balances,
    credit_available,
    debit_available,
    remove_reserved,
)
from app.modules.payments import schemas
from app.modules.payments.gateway import (
    RazorpayGateway,
    get_gateway,
    verify_payment_signature,
    verify_webhook_signature,
)
from app.modules.payments.models import (
    PaymentOrder,
    PaymentRefund,
    PaymentWebhookEvent,
    PricingCatalog,
)


class PaymentEvent:
    ORDER_CREATED = "PAYMENT_ORDER_CREATED"
    CAPTURED = "PAYMENT_CAPTURED"
    REFUND_PROCESSED = "PAYMENT_REFUND_PROCESSED"
    REFUND_POST_CONSUMPTION = "PAYMENT_REFUND_POST_CONSUMPTION"
    REFUND_FAILED = "PAYMENT_REFUND_FAILED"
    WEBHOOK_RECEIVED = "PAYMENT_WEBHOOK_RECEIVED"


_CAPTURE_EVENTS = frozenset({"payment.captured", "order.paid"})


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def _tax_of(base: int, surcharge: int, total: int) -> int:
    return total - base - surcharge


async def _audit(
    session: AsyncSession,
    ctx: RequestContext,
    *,
    event_type: str,
    resource_type: str,
    resource_id: str,
    actor_user_id: uuid.UUID | None,
    metadata: dict[str, object],
) -> None:
    await audit.record_event(
        session,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        request_id=ctx.request_id,
        actor_user_id=actor_user_id,
        ip_subnet_hash=ctx.ip_subnet_hash,
        metadata=metadata,
    )


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
async def list_active_pricing(session: AsyncSession) -> list[schemas.PricingSkuEntry]:
    now = _now()
    rows = (
        (
            await session.execute(
                select(PricingCatalog).where(
                    PricingCatalog.is_active.is_(True),
                    PricingCatalog.effective_from <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    entries: list[schemas.PricingSkuEntry] = []
    for r in rows:
        if r.effective_until is not None and r.effective_until <= now:
            continue
        if r.credit_bucket == CreditBucket.ESIGN:
            continue  # eSign SKUs stay gated in M6
        tax = _tax_of(r.base_amount_paise, r.esp_surcharge_paise, r.total_payable_paise)
        entries.append(
            schemas.PricingSkuEntry(
                sku_code=r.sku_code,
                display_name=r.display_name,
                credit_bucket=r.credit_bucket,
                currency=r.currency,
                base_amount_paise=r.base_amount_paise,
                esp_surcharge_paise=r.esp_surcharge_paise,
                gst_rate_bps=r.gst_rate_bps,
                is_gst_inclusive=r.is_gst_inclusive,
                tax_amount_paise=tax,
                total_payable_paise=r.total_payable_paise,
                hsn_sac_code=r.hsn_sac_code,
            )
        )
    return entries


# --------------------------------------------------------------------------- #
# Order creation
# --------------------------------------------------------------------------- #
class PaymentOrderService:
    def __init__(
        self, session: AsyncSession, *, ctx: RequestContext, gateway: RazorpayGateway | None = None
    ) -> None:
        self.session = session
        self.ctx = ctx
        self.settings = get_settings()
        self.gateway = gateway or get_gateway()

    async def create_order(
        self, auth: AuthContext, payload: schemas.CreatePaymentOrderPayload, idempotency_key: str
    ) -> PaymentOrder:
        qty = payload.quantity
        if qty < 1 or qty > self.settings.payment_max_quantity:
            raise ValidationFailedError("Unsupported quantity")

        catalog = await self.session.get(PricingCatalog, str(payload.sku_code))
        now = _now()
        if (
            catalog is None
            or not catalog.is_active
            or catalog.effective_from > now
            or (catalog.effective_until is not None and catalog.effective_until <= now)
        ):
            raise ValidationFailedError("SKU is not an active, purchasable product")
        if catalog.credit_bucket == CreditBucket.ESIGN:
            raise ForbiddenError("eSign SKUs are disabled until Milestone 7")

        existing = (
            await self.session.execute(
                select(PaymentOrder).where(
                    PaymentOrder.user_id == auth.user.id,
                    PaymentOrder.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        base = catalog.base_amount_paise * qty
        surcharge = catalog.esp_surcharge_paise * qty
        total = catalog.total_payable_paise * qty
        tax = _tax_of(base, surcharge, total)

        # Call the gateway BEFORE persisting so a timeout leaves no order row.
        gw = await self.gateway.create_order(
            amount_paise=total,
            currency=catalog.currency,
            receipt=f"rcpt_{uuid.uuid4().hex[:18]}",
            notes={"sku_code": catalog.sku_code},
        )
        gateway_order_id = str(gw.get("id") or "")
        if not gateway_order_id:
            raise GatewayError("Gateway did not return an order id")

        order = PaymentOrder(
            id=uuid.uuid4(),
            user_id=auth.user.id,
            sku_code=catalog.sku_code,
            quantity=qty,
            base_amount_paise=base,
            esp_surcharge_paise=surcharge,
            tax_amount_paise=tax,
            total_amount_paise=total,
            currency=catalog.currency,
            gateway_order_id=gateway_order_id,
            payment_status=PaymentOrderStatus.CREATED,
            idempotency_key=idempotency_key,
        )
        self.session.add(order)
        try:
            await self.session.flush()
        except IntegrityError:
            # Concurrent create with the same idempotency key won the race.
            await self.session.rollback()
            dup = (
                await self.session.execute(
                    select(PaymentOrder).where(
                        PaymentOrder.user_id == auth.user.id,
                        PaymentOrder.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one()
            return dup

        await _audit(
            self.session,
            self.ctx,
            event_type=PaymentEvent.ORDER_CREATED,
            resource_type="payment_order",
            resource_id=str(order.id),
            actor_user_id=auth.user.id,
            metadata={
                "order_id": str(order.id),
                "sku_code": catalog.sku_code,
                "quantity": qty,
                "total_amount_paise": total,
            },
        )
        await self.session.commit()
        return order


# --------------------------------------------------------------------------- #
# Shared exactly-once fulfilment
# --------------------------------------------------------------------------- #
@dataclass
class FulfilmentResult:
    order: PaymentOrder
    credited: bool


async def _catalog_bucket(session: AsyncSession, sku_code: str) -> CreditBucket:
    catalog = await session.get(PricingCatalog, sku_code)
    if catalog is None:
        raise NotFoundError("Pricing SKU no longer exists")
    return CreditBucket(catalog.credit_bucket)


async def credit_order_idempotently(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    ctx: RequestContext,
    gateway_payment_id: str | None = None,
) -> FulfilmentResult:
    """Lock the order and credit the wallet exactly once. Caller commits."""
    order = (
        await session.execute(
            select(PaymentOrder)
            .where(PaymentOrder.id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)  # refresh under the lock
        )
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundError("Payment order not found")
    if order.payment_status == PaymentOrderStatus.CAPTURED:
        return FulfilmentResult(order, False)

    bucket = await _catalog_bucket(session, order.sku_code)
    if bucket == CreditBucket.ESIGN:
        raise ForbiddenError("eSign entitlements are disabled until Milestone 7")

    wallet = (
        await session.execute(
            select(EntitlementWallet)
            .where(EntitlementWallet.user_id == order.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    credit_available(wallet, bucket, order.quantity)
    avail_after, res_after = bucket_balances(wallet, bucket)
    await append_ledger(
        session,
        user_id=order.user_id,
        entry_type=LedgerEntryType.PAID_CREDIT,
        credit_bucket=bucket,
        delta_available=order.quantity,
        delta_reserved=0,
        available_after=avail_after,
        reserved_after=res_after,
        idempotency_key=f"paid:{order.id}",
        reference_payment_order_id=order.id,
    )
    order.payment_status = PaymentOrderStatus.CAPTURED
    order.paid_at = _now()
    if gateway_payment_id and not order.gateway_payment_id:
        order.gateway_payment_id = gateway_payment_id
    await _audit(
        session,
        ctx,
        event_type=PaymentEvent.CAPTURED,
        resource_type="payment_order",
        resource_id=str(order.id),
        actor_user_id=order.user_id,
        metadata={
            "order_id": str(order.id),
            "payment_status": str(order.payment_status),
            "credit_bucket": str(bucket),
        },
    )
    return FulfilmentResult(order, True)


# --------------------------------------------------------------------------- #
# Synchronous verification
# --------------------------------------------------------------------------- #
class PaymentVerifyService:
    def __init__(self, session: AsyncSession, *, ctx: RequestContext) -> None:
        self.session = session
        self.ctx = ctx

    async def verify(
        self, auth: AuthContext, order_id: uuid.UUID, payload: schemas.VerifyRazorpayPaymentPayload
    ) -> schemas.PaymentVerifiedResponse:
        order = await self.session.get(PaymentOrder, order_id)
        if order is None or order.user_id != auth.user.id:
            raise NotFoundError("Payment order not found")
        if payload.razorpay_order_id != order.gateway_order_id:
            raise ValidationFailedError("razorpay_order_id does not match this order")
        if not verify_payment_signature(
            payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature
        ):
            raise UnauthorizedError("Invalid payment signature")

        result = await credit_order_idempotently(
            self.session,
            order_id=order.id,
            ctx=self.ctx,
            gateway_payment_id=payload.razorpay_payment_id,
        )
        await self.session.commit()
        wallet = await self.session.get(EntitlementWallet, auth.user.id)
        return schemas.PaymentVerifiedResponse(
            order_id=result.order.id,
            payment_status=PaymentOrderStatus(result.order.payment_status),
            credited=result.credited,
            wallet_snapshot=_paid_snapshot(wallet),
        )


def _paid_snapshot(wallet: EntitlementWallet | None) -> schemas.PaidWalletSnapshot:
    if wallet is None:
        return schemas.PaidWalletSnapshot(
            paid_std_20_available=0, paid_sig_50_available=0, paid_upgrade_30_available=0
        )
    return schemas.PaidWalletSnapshot(
        paid_std_20_available=wallet.paid_std_base_available,
        paid_sig_50_available=wallet.paid_sig_bundle_available,
        paid_upgrade_30_available=wallet.paid_sig_upgrade_available,
    )


# --------------------------------------------------------------------------- #
# Refund reconciliation
# --------------------------------------------------------------------------- #
async def _reconcile_one_credit(
    session: AsyncSession,
    wallet: EntitlementWallet,
    bucket: CreditBucket,
    order: PaymentOrder,
    refund_id: uuid.UUID,
) -> tuple[str, uuid.UUID | None]:
    """Reconcile a single refunded form credit; returns (action, revoked_lease_id)."""
    avail, _ = bucket_balances(wallet, bucket)
    idem = f"refund:{refund_id}"
    if avail >= 1:
        debit_available(wallet, bucket)
        a, r = bucket_balances(wallet, bucket)
        await append_ledger(
            session,
            user_id=order.user_id,
            entry_type=LedgerEntryType.REFUND_DEBIT_AVAILABLE,
            credit_bucket=bucket,
            delta_available=-1,
            delta_reserved=0,
            available_after=a,
            reserved_after=r,
            idempotency_key=idem,
            reference_payment_order_id=order.id,
            reference_refund_id=refund_id,
        )
        return RefundReconciliationAction.DEBITED_AVAILABLE_CREDIT, None

    lease = (
        (
            await session.execute(
                select(EntitlementLease)
                .where(
                    EntitlementLease.user_id == order.user_id,
                    EntitlementLease.base_credit_bucket == bucket,
                    EntitlementLease.lease_status == LeaseStatus.ISSUED,
                )
                .order_by(EntitlementLease.issued_at)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    _, reserved = bucket_balances(wallet, bucket)
    if lease is not None and reserved >= 1:
        lease.lease_status = LeaseStatus.REVOKED_REFUND
        lease.released_at = _now()
        remove_reserved(wallet, bucket)
        a, r = bucket_balances(wallet, bucket)
        await append_ledger(
            session,
            user_id=order.user_id,
            entry_type=LedgerEntryType.REFUND_REVOKE_RESERVED,
            credit_bucket=bucket,
            delta_available=0,
            delta_reserved=-1,
            available_after=a,
            reserved_after=r,
            idempotency_key=idem,
            reference_payment_order_id=order.id,
            reference_refund_id=refund_id,
            reference_lease_id=lease.id,
        )
        return RefundReconciliationAction.REVOKED_ISSUED_LEASE, lease.id

    # Post-consumption: never drive the wallet negative.
    wallet.post_consumption_refund_count += 1
    a, r = bucket_balances(wallet, bucket)
    await append_ledger(
        session,
        user_id=order.user_id,
        entry_type=LedgerEntryType.REFUND_POST_CONSUMPTION,
        credit_bucket=bucket,
        delta_available=0,
        delta_reserved=0,
        available_after=a,
        reserved_after=r,
        idempotency_key=idem,
        reference_payment_order_id=order.id,
        reference_refund_id=refund_id,
    )
    return RefundReconciliationAction.FLAGGED_POST_CONSUMPTION, None


def _refund_components(order: PaymentOrder, refund_amount: int) -> tuple[int, int, int]:
    total = order.total_amount_paise
    if total <= 0:
        return refund_amount, 0, 0
    tax = round(refund_amount * order.tax_amount_paise / total)
    surcharge = round(refund_amount * order.esp_surcharge_paise / total)
    base = refund_amount - tax - surcharge
    return base, surcharge, tax


async def reconcile_processed_refund(
    session: AsyncSession,
    *,
    ctx: RequestContext,
    order: PaymentOrder,
    gateway_refund_id: str,
    refund_amount: int,
) -> bool:
    """Reconcile a refund.processed event. ``order`` is already FOR UPDATE locked.

    Returns True if newly reconciled, False if a duplicate refund id.
    """
    wallet = (
        await session.execute(
            select(EntitlementWallet)
            .where(EntitlementWallet.user_id == order.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    base_c, surcharge_c, tax_c = _refund_components(order, refund_amount)
    refund = PaymentRefund(
        id=uuid.uuid4(),
        payment_order_id=order.id,
        gateway_refund_id=gateway_refund_id,
        refund_amount_paise=refund_amount,
        base_component_refunded_paise=base_c,
        surcharge_component_refunded_paise=surcharge_c,
        tax_component_refunded_paise=tax_c,
        refund_status=RefundRecordStatus.PROCESSED,
        wallet_reconciliation_action=RefundReconciliationAction.NONE,
        processed_at=_now(),
    )
    # SAVEPOINT: a duplicate refund id must undo only this insert, never the
    # surrounding webhook transaction (which holds the dedup event row + locks).
    savepoint = await session.begin_nested()
    session.add(refund)
    try:
        await session.flush()
    except IntegrityError:
        await savepoint.rollback()
        return False  # duplicate refund id → already reconciled

    bucket = await _catalog_bucket(session, order.sku_code)
    cumulative = order.refunded_amount_paise + refund_amount
    already_revoked = (
        await session.scalar(
            select(func.count())
            .select_from(PaymentRefund)
            .where(
                PaymentRefund.payment_order_id == order.id,
                PaymentRefund.id != refund.id,
                PaymentRefund.wallet_reconciliation_action.in_(
                    [
                        RefundReconciliationAction.DEBITED_AVAILABLE_CREDIT,
                        RefundReconciliationAction.REVOKED_ISSUED_LEASE,
                        RefundReconciliationAction.FLAGGED_POST_CONSUMPTION,
                    ]
                ),
            )
        )
    ) or 0

    crosses_threshold = already_revoked == 0 and cumulative >= order.base_amount_paise
    revoked_lease_id: uuid.UUID | None = None
    if crosses_threshold:
        action, revoked_lease_id = await _reconcile_one_credit(
            session, wallet, bucket, order, refund.id
        )
    elif cumulative < order.total_amount_paise:
        action = RefundReconciliationAction.PARTIAL_SURCHARGE_ADJUSTMENT
    else:
        action = RefundReconciliationAction.NONE

    refund.wallet_reconciliation_action = action
    refund.revoked_lease_id = revoked_lease_id
    order.refunded_amount_paise = cumulative

    if action == RefundReconciliationAction.FLAGGED_POST_CONSUMPTION:
        order.payment_status = PaymentOrderStatus.REFUNDED_POST_CONSUMPTION
        audit_event = PaymentEvent.REFUND_POST_CONSUMPTION
    elif cumulative >= order.total_amount_paise:
        order.payment_status = PaymentOrderStatus.REFUNDED
        audit_event = PaymentEvent.REFUND_PROCESSED
    else:
        order.payment_status = PaymentOrderStatus.PARTIALLY_REFUNDED
        audit_event = PaymentEvent.REFUND_PROCESSED

    await _audit(
        session,
        ctx,
        event_type=audit_event,
        resource_type="payment_refund",
        resource_id=str(refund.id),
        actor_user_id=order.user_id,
        metadata={
            "order_id": str(order.id),
            "refund_id": str(refund.id),
            "refund_amount_paise": refund_amount,
            "reconciliation_action": str(action),
            "payment_status": str(order.payment_status),
        },
    )
    return True


async def record_failed_refund(
    session: AsyncSession,
    *,
    ctx: RequestContext,
    order: PaymentOrder,
    gateway_refund_id: str,
    refund_amount: int,
    failure_reason: str,
) -> None:
    """Record a refund.failed event; never alters wallet or order status."""
    reason = failure_reason[:160]
    existing = (
        await session.execute(
            select(PaymentRefund).where(PaymentRefund.gateway_refund_id == gateway_refund_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.refund_status = RefundRecordStatus.FAILED
        existing.failure_reason = reason
    else:
        session.add(
            PaymentRefund(
                id=uuid.uuid4(),
                payment_order_id=order.id,
                gateway_refund_id=gateway_refund_id,
                refund_amount_paise=max(refund_amount, 1),
                base_component_refunded_paise=max(refund_amount, 1),
                refund_status=RefundRecordStatus.FAILED,
                wallet_reconciliation_action=RefundReconciliationAction.NONE,
                failure_reason=reason,
            )
        )
    await _audit(
        session,
        ctx,
        event_type=PaymentEvent.REFUND_FAILED,
        resource_type="payment_refund",
        resource_id=gateway_refund_id,
        actor_user_id=order.user_id,
        metadata={"order_id": str(order.id), "refund_status": "FAILED"},
    )


# --------------------------------------------------------------------------- #
# Webhook handling
# --------------------------------------------------------------------------- #
@dataclass
class _ParsedEvent:
    event_type: str
    gateway_order_id: str | None
    gateway_payment_id: str | None
    refund_id: str | None
    refund_amount: int | None
    refund_status: str | None
    failure_reason: str | None


def _parse_event(payload: dict[str, Any]) -> _ParsedEvent:
    event_type = str(payload.get("event") or "")
    data = payload.get("payload") or {}
    payment = (data.get("payment") or {}).get("entity") or {}
    order = (data.get("order") or {}).get("entity") or {}
    refund = (data.get("refund") or {}).get("entity") or {}
    return _ParsedEvent(
        event_type=event_type,
        gateway_order_id=payment.get("order_id") or order.get("id"),
        gateway_payment_id=payment.get("id") or refund.get("payment_id"),
        refund_id=refund.get("id"),
        refund_amount=refund.get("amount"),
        refund_status=refund.get("status"),
        failure_reason=(refund.get("error_description") or refund.get("notes", {}).get("reason")),
    )


class WebhookService:
    def __init__(self, session: AsyncSession, *, ctx: RequestContext) -> None:
        self.session = session
        self.ctx = ctx

    async def _find_order_by_gateway_order(self, gateway_order_id: str) -> PaymentOrder | None:
        return (
            await self.session.execute(
                select(PaymentOrder)
                .where(PaymentOrder.gateway_order_id == gateway_order_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def _find_order_by_payment(self, gateway_payment_id: str) -> PaymentOrder | None:
        return (
            await self.session.execute(
                select(PaymentOrder)
                .where(PaymentOrder.gateway_payment_id == gateway_payment_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()

    async def handle(
        self, *, raw_body: bytes, signature: str | None, event_id: str | None
    ) -> schemas.WebhookAckResponse:
        if not signature or not verify_webhook_signature(raw_body, signature):
            raise UnauthorizedError("Invalid webhook signature")
        if not event_id:
            raise ValidationFailedError("Missing X-Razorpay-Event-Id")
        try:
            payload = json.loads(raw_body)
            if not isinstance(payload, dict):
                raise ValueError
        except ValueError as exc:
            raise ValidationFailedError("Malformed webhook payload") from exc

        parsed = _parse_event(payload)

        # Insert the dedup row in the SAME transaction as processing: a genuine
        # duplicate collides on the PK; a processing failure rolls both back.
        event_row = PaymentWebhookEvent(
            gateway_event_id=event_id,
            event_type=parsed.event_type,
            gateway_order_id=parsed.gateway_order_id,
            payload_sha256=hashlib.sha256(raw_body).hexdigest(),
            processing_outcome=WebhookProcessingOutcome.PROCESSED,
        )
        self.session.add(event_row)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return schemas.WebhookAckResponse(
                status="ALREADY_PROCESSED", event_type=parsed.event_type
            )

        outcome = await self._dispatch(parsed)
        event_row.processing_outcome = outcome
        await self.session.commit()
        status = "PROCESSED" if outcome == WebhookProcessingOutcome.PROCESSED else "IGNORED"
        return schemas.WebhookAckResponse(
            status=status, event_type=parsed.event_type, processed_at=_now()
        )

    async def _dispatch(self, parsed: _ParsedEvent) -> str:
        if parsed.event_type in _CAPTURE_EVENTS:
            order = None
            if parsed.gateway_order_id:
                order = await self._find_order_by_gateway_order(parsed.gateway_order_id)
            if order is None and parsed.gateway_payment_id:
                order = await self._find_order_by_payment(parsed.gateway_payment_id)
            if order is None:
                return WebhookProcessingOutcome.IGNORED_UNTRACKED_EVENT
            await credit_order_idempotently(
                self.session,
                order_id=order.id,
                ctx=self.ctx,
                gateway_payment_id=parsed.gateway_payment_id,
            )
            return WebhookProcessingOutcome.PROCESSED

        if parsed.event_type == "refund.processed":
            if not (parsed.gateway_payment_id and parsed.refund_id and parsed.refund_amount):
                return WebhookProcessingOutcome.IGNORED_UNTRACKED_EVENT
            order = await self._find_order_by_payment(parsed.gateway_payment_id)
            if order is None:
                return WebhookProcessingOutcome.IGNORED_UNTRACKED_EVENT
            await reconcile_processed_refund(
                self.session,
                ctx=self.ctx,
                order=order,
                gateway_refund_id=parsed.refund_id,
                refund_amount=int(parsed.refund_amount),
            )
            return WebhookProcessingOutcome.PROCESSED

        if parsed.event_type == "refund.failed":
            if not (parsed.gateway_payment_id and parsed.refund_id):
                return WebhookProcessingOutcome.IGNORED_UNTRACKED_EVENT
            order = await self._find_order_by_payment(parsed.gateway_payment_id)
            if order is None:
                return WebhookProcessingOutcome.IGNORED_UNTRACKED_EVENT
            await record_failed_refund(
                self.session,
                ctx=self.ctx,
                order=order,
                gateway_refund_id=parsed.refund_id,
                refund_amount=int(parsed.refund_amount or 0),
                failure_reason=parsed.failure_reason or "unspecified",
            )
            return WebhookProcessingOutcome.PROCESSED

        return WebhookProcessingOutcome.IGNORED_UNTRACKED_EVENT
