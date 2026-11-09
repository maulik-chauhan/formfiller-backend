"""Entitlement API routes (``/api/v1/entitlements/*``)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session
from app.modules.entitlements import schemas

router = APIRouter(prefix="/entitlements", tags=["entitlements"])


class NotImplementedYet(AppError):
    status_code = status.HTTP_501_NOT_IMPLEMENTED
    code = "NOT_IMPLEMENTED"
    title = "Endpoint not implemented yet"


@router.get("/wallet", response_model=schemas.WalletSummaryResponse)
async def get_wallet(
    session: AsyncSession = Depends(get_session),
) -> schemas.WalletSummaryResponse:
    raise NotImplementedYet("Wallet summary pending entitlement service")


@router.post("/leases", response_model=schemas.LeaseIssuedResponse)
async def create_lease(
    payload: schemas.CreateLeasePayload,
    session: AsyncSession = Depends(get_session),
) -> schemas.LeaseIssuedResponse:
    """Reserve one credit and issue an offline lease (available-1, reserved+1)."""
    raise NotImplementedYet("Lease creation pending entitlement service")


@router.post("/leases/{lease_id}/commit", response_model=schemas.LeaseIssuedResponse)
async def commit_lease(
    lease_id: uuid.UUID,
    payload: schemas.CommitLeasePayload,
    session: AsyncSession = Depends(get_session),
) -> schemas.LeaseIssuedResponse:
    """Commit a lease using server clock only; apply delayed-commit policy (3.3)."""
    raise NotImplementedYet("Lease commit pending entitlement service")


@router.post("/leases/{lease_id}/upgrade", response_model=schemas.LeaseIssuedResponse)
async def upgrade_lease(
    lease_id: uuid.UUID,
    payload: schemas.UpgradeLeasePayload,
    session: AsyncSession = Depends(get_session),
) -> schemas.LeaseIssuedResponse:
    raise NotImplementedYet("Lease upgrade pending entitlement service")


@router.post("/leases/{lease_id}/release", status_code=status.HTTP_204_NO_CONTENT)
async def release_lease(
    lease_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Release an uncommitted lease (reserved-1, available+1)."""
    raise NotImplementedYet("Lease release pending entitlement service")
