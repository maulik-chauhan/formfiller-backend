"""Entitlement request/response schemas (citizen routes — 4 KB JSON cap).

Request models inherit ``StrictModel`` (``extra='forbid'``). No applicant data,
form answers, PDF bytes, base64, or data-URIs are ever accepted — only the
random, device-local ``draft_instance_id_hash`` and device fingerprint hash.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import Field

from app.core.constants import GrantedEntitlementTier, LeaseStatus, RequestedLeaseTier
from app.core.schemas import ResponseModel, StrictModel

_HEX64 = r"^[a-f0-9]{64}$"


# --------------------------------------------------------------------------- #
# Public key discovery
# --------------------------------------------------------------------------- #
class PublicKeyEntry(ResponseModel):
    kid: str
    alg: str
    crv: str
    public_key_b64: str
    status: str


class PublicKeySetResponse(ResponseModel):
    keys: list[PublicKeyEntry]
    elt_typ: str = "ELT+jwt"


# --------------------------------------------------------------------------- #
# Wallet
# --------------------------------------------------------------------------- #
class WalletBalanceResponse(ResponseModel):
    free_quota_total: int
    free_quota_used: int
    free_quota_reserved: int
    free_quota_available: int
    paid_std_20_available: int
    paid_std_20_reserved: int
    paid_sig_50_available: int
    paid_sig_50_reserved: int
    paid_upgrade_30_available: int
    paid_upgrade_30_reserved: int
    ledger_sequence_no: int


# --------------------------------------------------------------------------- #
# Lease capability + wallet snapshot (shared fragments)
# --------------------------------------------------------------------------- #
class LeaseCapabilities(ResponseModel):
    allow_local_pdf_export: bool
    allow_handwritten_signature_image: bool
    allow_cca_esign: bool


class WalletSnapshot(ResponseModel):
    lifetime_free_remaining: int
    paid_std_20_available: int
    paid_sig_50_available: int
    paid_upgrade_30_available: int


# --------------------------------------------------------------------------- #
# Create / upgrade / commit / release
# --------------------------------------------------------------------------- #
class CreateLeasePayload(StrictModel):
    template_version_id: uuid.UUID
    requested_tier: RequestedLeaseTier
    draft_instance_id_hash: str = Field(pattern=_HEX64)
    device_fingerprint_hash: str = Field(pattern=_HEX64)


class LeaseIssuedResponse(ResponseModel):
    lease_id: uuid.UUID
    template_version_id: uuid.UUID
    granted_tier: GrantedEntitlementTier
    is_upgraded_with_sig_30: bool
    lease_status: LeaseStatus
    capabilities: LeaseCapabilities
    reservation_expires_at: dt.datetime
    correction_window_expires_at: dt.datetime | None
    offline_lease_jwt: str
    signing_key_id: str
    wallet_snapshot: WalletSnapshot


class UpgradeLeasePayload(StrictModel):
    upgrade_sku: Literal["SIG_UPGRADE_30"]
    draft_instance_id_hash: str = Field(pattern=_HEX64)
    device_fingerprint_hash: str = Field(pattern=_HEX64)


class CommitLeasePayload(StrictModel):
    draft_instance_id_hash: str = Field(pattern=_HEX64)
    client_completion_receipt_hash: str = Field(pattern=_HEX64)
    # Untrusted, diagnostic-only; NEVER authoritative (spec §3.3).
    completed_at_client_iso: dt.datetime | None = None


class LeaseCommittedResponse(ResponseModel):
    lease_id: uuid.UUID
    lease_status: LeaseStatus
    commit_count: int
    first_committed_at: dt.datetime | None
    last_committed_at: dt.datetime | None
    correction_window_expires_at: dt.datetime | None
    re_export_allowed_until: dt.datetime | None
    refreshed_offline_lease_jwt: str
    signing_key_id: str


class ReleaseLeasePayload(StrictModel):
    draft_instance_id_hash: str = Field(pattern=_HEX64)
    unconsumed_attestation_hmac: str = Field(pattern=_HEX64)


class LeaseReleasedResponse(ResponseModel):
    lease_id: uuid.UUID
    lease_status: LeaseStatus
    released_at: dt.datetime
    wallet_snapshot: WalletSnapshot
