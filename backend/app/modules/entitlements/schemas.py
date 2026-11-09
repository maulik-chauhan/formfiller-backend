"""Entitlement lease schemas (offline reservation lifecycle)."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import Field

from app.core.constants import GrantedEntitlementTier, LeaseStatus, RequestedLeaseTier
from app.core.schemas import ResponseModel, StrictModel

_HEX64 = r"^[a-f0-9]{64}$"


class CreateLeasePayload(StrictModel):
    template_version_id: uuid.UUID
    requested_tier: RequestedLeaseTier
    draft_instance_id_hash: str = Field(pattern=_HEX64)
    device_fingerprint_hash: str = Field(pattern=_HEX64)
    create_idempotency_key: str = Field(min_length=8, max_length=64)


class CommitLeasePayload(StrictModel):
    client_completion_receipt_hash: str = Field(pattern=_HEX64)
    # Untrusted, diagnostic-only; NEVER authoritative (spec 3.3).
    completed_at_client_iso: dt.datetime | None = None


class UpgradeLeasePayload(StrictModel):
    client_completion_receipt_hash: str | None = Field(default=None, pattern=_HEX64)


class LeaseIssuedResponse(ResponseModel):
    lease_id: uuid.UUID
    granted_tier: GrantedEntitlementTier
    lease_status: LeaseStatus
    lease_jwt: str
    reservation_expires_at: dt.datetime


class WalletSummaryResponse(ResponseModel):
    free_quota_total: int
    free_quota_used: int
    free_quota_reserved: int
    paid_std_base_available: int
    paid_sig_bundle_available: int
    paid_sig_upgrade_available: int
