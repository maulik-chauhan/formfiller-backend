"""Payment API routes (``/api/v1/payments/*``)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.constants import PaymentOrderStatus
from app.core.dependencies import (
    AuthContext,
    RequestContext,
    get_request_context,
    require_authenticated_user,
    require_idempotency_key,
)
from app.db.session import get_session
from app.modules.payments import schemas, service

router = APIRouter(prefix="/payments", tags=["payments"])


@router.get("/pricing", response_model=schemas.PricingCatalogResponse)
async def get_pricing(
    session: AsyncSession = Depends(get_session),
) -> schemas.PricingCatalogResponse:
    skus = await service.list_active_pricing(session)
    return schemas.PricingCatalogResponse(skus=skus)


@router.post(
    "/orders",
    response_model=schemas.PaymentOrderCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_order(
    payload: schemas.CreatePaymentOrderPayload,
    auth: AuthContext = Depends(require_authenticated_user),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
) -> schemas.PaymentOrderCreatedResponse:
    order = await service.PaymentOrderService(session, ctx=ctx).create_order(
        auth, payload, idempotency_key
    )
    return schemas.PaymentOrderCreatedResponse(
        order_id=order.id,
        gateway_order_id=order.gateway_order_id,
        sku_code=order.sku_code,
        quantity=order.quantity,
        base_amount_paise=order.base_amount_paise,
        esp_surcharge_paise=order.esp_surcharge_paise,
        tax_amount_paise=order.tax_amount_paise,
        total_amount_paise=order.total_amount_paise,
        currency=order.currency,
        razorpay_key_id=get_settings().razorpay_key_id,
        payment_status=PaymentOrderStatus(order.payment_status),
    )


@router.post("/orders/{order_id}/verify", response_model=schemas.PaymentVerifiedResponse)
async def verify_payment(
    order_id: uuid.UUID,
    payload: schemas.VerifyRazorpayPaymentPayload,
    auth: AuthContext = Depends(require_authenticated_user),
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
) -> schemas.PaymentVerifiedResponse:
    return await service.PaymentVerifyService(session, ctx=ctx).verify(auth, order_id, payload)


@router.post("/webhooks/razorpay", response_model=schemas.WebhookAckResponse)
async def razorpay_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
) -> schemas.WebhookAckResponse:
    raw_body = await request.body()
    signature = request.headers.get("x-razorpay-signature")
    event_id = request.headers.get("x-razorpay-event-id")
    return await service.WebhookService(session, ctx=ctx).handle(
        raw_body=raw_body, signature=signature, event_id=event_id
    )
