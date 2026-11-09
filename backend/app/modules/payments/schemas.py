"""Payment request/response schemas (citizen routes — 4 KB JSON cap).

Request models inherit ``StrictModel`` (``extra='forbid'``). The server never
trusts a client-supplied amount, GST, or total — all monetary values are computed
from ``pricing_catalog`` and returned in integer paise. The Razorpay secret and
webhook secret are never present in any response schema.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field

from app.core.constants import PaymentOrderStatus, SkuCode
from app.core.schemas import ResponseModel, StrictModel


# --------------------------------------------------------------------------- #
# Pricing
# --------------------------------------------------------------------------- #
class PricingSkuEntry(ResponseModel):
    sku_code: str
    display_name: str
    credit_bucket: str
    currency: str
    base_amount_paise: int
    esp_surcharge_paise: int
    gst_rate_bps: int
    is_gst_inclusive: bool
    tax_amount_paise: int
    total_payable_paise: int
    hsn_sac_code: str


class PricingCatalogResponse(ResponseModel):
    skus: list[PricingSkuEntry]


# --------------------------------------------------------------------------- #
# Order creation
# --------------------------------------------------------------------------- #
class CreatePaymentOrderPayload(StrictModel):
    sku_code: SkuCode
    quantity: int = Field(default=1, ge=1, le=1)  # DB fixes quantity = 1


class PaymentOrderCreatedResponse(ResponseModel):
    order_id: uuid.UUID
    gateway_order_id: str
    sku_code: str
    quantity: int
    base_amount_paise: int
    esp_surcharge_paise: int
    tax_amount_paise: int
    total_amount_paise: int
    currency: str
    razorpay_key_id: str
    payment_status: PaymentOrderStatus


# --------------------------------------------------------------------------- #
# Synchronous verification
# --------------------------------------------------------------------------- #
class VerifyRazorpayPaymentPayload(StrictModel):
    razorpay_order_id: str = Field(min_length=4, max_length=80)
    razorpay_payment_id: str = Field(min_length=4, max_length=80)
    razorpay_signature: str = Field(min_length=16, max_length=128)


class PaidWalletSnapshot(ResponseModel):
    paid_std_20_available: int
    paid_sig_50_available: int
    paid_upgrade_30_available: int


class PaymentVerifiedResponse(ResponseModel):
    order_id: uuid.UUID
    payment_status: PaymentOrderStatus
    credited: bool
    wallet_snapshot: PaidWalletSnapshot


# --------------------------------------------------------------------------- #
# Webhook
# --------------------------------------------------------------------------- #
class WebhookAckResponse(ResponseModel):
    status: Literal["PROCESSED", "ALREADY_PROCESSED", "IGNORED"]
    event_type: str | None = None
    processed_at: dt.datetime | None = None
