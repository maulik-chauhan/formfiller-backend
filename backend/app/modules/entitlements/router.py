"""Entitlement API routes (``/api/v1/entitlements/*``)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import GrantedEntitlementTier, LeaseStatus
from app.core.dependencies import (
    AuthContext,
    RequestContext,
    get_request_context,
    require_authenticated_user,
    require_idempotency_key,
)
from app.core.elt import public_key_set
from app.db.session import get_session
from app.modules.entitlements import schemas
from app.modules.entitlements.models import EntitlementLease, EntitlementWallet
from app.modules.entitlements.service import (
    EntitlementService,
    LeaseResult,
    capabilities_for,
)

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


def _service(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
) -> EntitlementService:
    return EntitlementService(session, ctx=ctx)


def _free_available(w: EntitlementWallet) -> int:
    return w.free_quota_total - w.free_quota_used - w.free_quota_reserved


def _snapshot(w: EntitlementWallet) -> schemas.WalletSnapshot:
    return schemas.WalletSnapshot(
        lifetime_free_remaining=_free_available(w),
        paid_std_20_available=w.paid_std_base_available,
        paid_sig_50_available=w.paid_sig_bundle_available,
        paid_upgrade_30_available=w.paid_sig_upgrade_available,
    )


def _capabilities(lease: EntitlementLease) -> schemas.LeaseCapabilities:
    pdf, sig_img, esign = capabilities_for(GrantedEntitlementTier(lease.granted_tier))
    return schemas.LeaseCapabilities(
        allow_local_pdf_export=pdf,
        allow_handwritten_signature_image=sig_img,
        allow_cca_esign=esign,
    )


def _issued_response(result: LeaseResult) -> schemas.LeaseIssuedResponse:
    lease = result.lease
    return schemas.LeaseIssuedResponse(
        lease_id=lease.id,
        template_version_id=lease.template_version_id,
        granted_tier=GrantedEntitlementTier(lease.granted_tier),
        is_upgraded_with_sig_30=lease.is_upgraded_with_sig_30,
        lease_status=LeaseStatus(lease.lease_status),
        capabilities=_capabilities(lease),
        reservation_expires_at=lease.reservation_expires_at,
        correction_window_expires_at=lease.correction_window_expires_at,
        offline_lease_jwt=result.elt,
        signing_key_id=lease.signing_key_id,
        wallet_snapshot=_snapshot(result.wallet),
    )


@router.get("/public-keys", response_model=schemas.PublicKeySetResponse)
async def get_public_keys() -> schemas.PublicKeySetResponse:
    keys = [schemas.PublicKeyEntry(**k) for k in public_key_set()]
    return schemas.PublicKeySetResponse(keys=keys)


@router.get("/wallet", response_model=schemas.WalletBalanceResponse)
async def get_wallet(
    auth: AuthContext = Depends(require_authenticated_user),
    service: EntitlementService = Depends(_service),
) -> schemas.WalletBalanceResponse:
    w = await service.get_wallet(auth.user.id)
    seq, _ = await service._ledger_tip(auth.user.id)
    return schemas.WalletBalanceResponse(
        free_quota_total=w.free_quota_total,
        free_quota_used=w.free_quota_used,
        free_quota_reserved=w.free_quota_reserved,
        free_quota_available=_free_available(w),
        paid_std_20_available=w.paid_std_base_available,
        paid_std_20_reserved=w.paid_std_base_reserved,
        paid_sig_50_available=w.paid_sig_bundle_available,
        paid_sig_50_reserved=w.paid_sig_bundle_reserved,
        paid_upgrade_30_available=w.paid_sig_upgrade_available,
        paid_upgrade_30_reserved=w.paid_sig_upgrade_reserved,
        ledger_sequence_no=seq,
    )


@router.post(
    "/leases",
    response_model=schemas.LeaseIssuedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_lease(
    payload: schemas.CreateLeasePayload,
    auth: AuthContext = Depends(require_authenticated_user),
    idempotency_key: str = Depends(require_idempotency_key),
    service: EntitlementService = Depends(_service),
) -> schemas.LeaseIssuedResponse:
    result = await service.create_lease(auth, payload, idempotency_key)
    return _issued_response(result)


@router.post("/leases/{lease_id}/upgrade", response_model=schemas.LeaseIssuedResponse)
async def upgrade_lease(
    lease_id: uuid.UUID,
    payload: schemas.UpgradeLeasePayload,
    auth: AuthContext = Depends(require_authenticated_user),
    idempotency_key: str = Depends(require_idempotency_key),
    service: EntitlementService = Depends(_service),
) -> schemas.LeaseIssuedResponse:
    result = await service.upgrade_lease(auth, lease_id, payload, idempotency_key)
    return _issued_response(result)


@router.post("/leases/{lease_id}/commit", response_model=schemas.LeaseCommittedResponse)
async def commit_lease(
    lease_id: uuid.UUID,
    payload: schemas.CommitLeasePayload,
    auth: AuthContext = Depends(require_authenticated_user),
    idempotency_key: str = Depends(require_idempotency_key),
    service: EntitlementService = Depends(_service),
) -> schemas.LeaseCommittedResponse:
    result = await service.commit_lease(auth, lease_id, payload, idempotency_key)
    lease = result.lease
    return schemas.LeaseCommittedResponse(
        lease_id=lease.id,
        lease_status=LeaseStatus(lease.lease_status),
        commit_count=lease.commit_count,
        first_committed_at=lease.first_committed_at,
        last_committed_at=lease.last_committed_at,
        correction_window_expires_at=lease.correction_window_expires_at,
        re_export_allowed_until=lease.correction_window_expires_at,
        refreshed_offline_lease_jwt=result.elt,
        signing_key_id=lease.signing_key_id,
    )


@router.post("/leases/{lease_id}/release", response_model=schemas.LeaseReleasedResponse)
async def release_lease(
    lease_id: uuid.UUID,
    payload: schemas.ReleaseLeasePayload,
    auth: AuthContext = Depends(require_authenticated_user),
    idempotency_key: str = Depends(require_idempotency_key),
    service: EntitlementService = Depends(_service),
) -> schemas.LeaseReleasedResponse:
    lease = await service.release_lease(auth, lease_id, payload, idempotency_key)
    wallet = await service.get_wallet(auth.user.id)
    return schemas.LeaseReleasedResponse(
        lease_id=lease.id,
        lease_status=LeaseStatus(lease.lease_status),
        released_at=lease.released_at,
        wallet_snapshot=_snapshot(wallet),
    )
