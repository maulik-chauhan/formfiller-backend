"""Entitlement service — reservation, upgrade, commit, release, recovery.

All operations that touch both a wallet and a lease follow the mandated lock
order: ``entitlement_wallets`` ``SELECT ... FOR UPDATE`` first, then
``entitlement_leases`` ``SELECT ... FOR UPDATE``; then validate, mutate balances,
append the hash-chained ledger entry, write the audit event, and commit as one
transaction. Financial correctness never relies on Python-level locks.

``completed_at_client_iso`` is treated strictly as untrusted diagnostic metadata
(spec §3.3): credit is only ever granted against an authoritative wallet balance,
never on the basis of a client timestamp, and a client timestamp can never extend
a correction window beyond the server-authoritative reservation window + 24h.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.constants import (
    CreditBucket,
    GrantedEntitlementTier,
    LeaseStatus,
    LedgerEntryType,
    RequestedLeaseTier,
    SkuCode,
    TemplateVersionStatus,
)
from app.core.dependencies import AuthContext, RequestContext
from app.core.elt import active_key_id, sign_elt
from app.core.errors import (
    ConflictError,
    ForbiddenError,
    GoneError,
    NotFoundError,
    PaymentRequiredError,
    ValidationFailedError,
)
from app.core.ledger import GENESIS_HASH, compute_entry_hash
from app.modules.audit import service as audit
from app.modules.entitlements.models import (
    EntitlementLease,
    EntitlementLedgerEntry,
    EntitlementWallet,
)
from app.modules.templates.models import TemplateVersion

CB = CreditBucket
GT = GrantedEntitlementTier

#: requested tier → (base credit bucket, granted tier)
_TIER_MAP: dict[RequestedLeaseTier, tuple[CreditBucket, GrantedEntitlementTier]] = {
    RequestedLeaseTier.FREE_STANDARD: (CB.FREE_LIFETIME, GT.FREE_STANDARD),
    RequestedLeaseTier.PAID_STANDARD_20: (CB.STANDARD_20, GT.PAID_STANDARD_20),
    RequestedLeaseTier.PAID_SIGNATURE_50: (CB.SIGNATURE_50, GT.PAID_SIGNATURE_50),
}

#: base granted tier → upgraded tier (via SIG_UPGRADE_30)
_UPGRADE_MAP: dict[GrantedEntitlementTier, GrantedEntitlementTier] = {
    GT.FREE_STANDARD: GT.FREE_UPGRADED_SIG_50,
    GT.PAID_STANDARD_20: GT.PAID_UPGRADED_SIG_50,
}

_SIG_TIERS = frozenset({GT.PAID_SIGNATURE_50, GT.FREE_UPGRADED_SIG_50, GT.PAID_UPGRADED_SIG_50})

#: paid bucket → (available column, reserved column) on entitlement_wallets
_PAID_COLS: dict[CreditBucket, tuple[str, str]] = {
    CB.STANDARD_20: ("paid_std_base_available", "paid_std_base_reserved"),
    CB.SIGNATURE_50: ("paid_sig_bundle_available", "paid_sig_bundle_reserved"),
    CB.SIGNATURE_UPGRADE_30: ("paid_sig_upgrade_available", "paid_sig_upgrade_reserved"),
}

_ELIGIBLE_BASE_SKUS = [SkuCode.STD_FORM_20.value, SkuCode.SIG_IMAGE_50.value]


class EntitlementEvent:
    LEASE_RESERVE = "ENTITLEMENT_LEASE_RESERVE"
    LEASE_COMMIT = "ENTITLEMENT_LEASE_COMMIT"
    LEASE_RECOMMIT = "ENTITLEMENT_LEASE_RECOMMIT"
    LEASE_UPGRADE = "ENTITLEMENT_LEASE_UPGRADE"
    LEASE_RELEASE = "ENTITLEMENT_LEASE_RELEASE"
    LEASE_EXPIRED_RELEASE = "ENTITLEMENT_LEASE_EXPIRED_RELEASE"
    LATE_COMMIT_RECOVERED = "ENTITLEMENT_LATE_OFFLINE_COMMIT_RECOVERED"
    LATE_COMMIT_CREDIT_SPENT = "LATE_OFFLINE_COMMIT_CREDIT_ALREADY_SPENT"


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


@dataclass
class LeaseResult:
    lease: EntitlementLease
    wallet: EntitlementWallet
    elt: str


def capabilities_for(tier: GrantedEntitlementTier) -> tuple[bool, bool, bool]:
    """Return (pdf, sig_img, esign) capability booleans for a granted tier."""
    return True, tier in _SIG_TIERS, False


# ---- wallet bucket arithmetic -------------------------------------------- #
def _free_available(wallet: EntitlementWallet) -> int:
    return wallet.free_quota_total - wallet.free_quota_used - wallet.free_quota_reserved


def _bucket_balances(wallet: EntitlementWallet, bucket: CreditBucket) -> tuple[int, int]:
    if bucket == CB.FREE_LIFETIME:
        return _free_available(wallet), wallet.free_quota_reserved
    avail_col, res_col = _PAID_COLS[bucket]
    return getattr(wallet, avail_col), getattr(wallet, res_col)


def _reserve(wallet: EntitlementWallet, bucket: CreditBucket) -> bool:
    if bucket == CB.FREE_LIFETIME:
        if _free_available(wallet) < 1:
            return False
        wallet.free_quota_reserved += 1
    else:
        avail_col, res_col = _PAID_COLS[bucket]
        if getattr(wallet, avail_col) < 1:
            return False
        setattr(wallet, avail_col, getattr(wallet, avail_col) - 1)
        setattr(wallet, res_col, getattr(wallet, res_col) + 1)
    wallet.version_lock += 1
    return True


def _commit_reserved(wallet: EntitlementWallet, bucket: CreditBucket) -> None:
    if bucket == CB.FREE_LIFETIME:
        wallet.free_quota_reserved -= 1
        wallet.free_quota_used += 1
    else:
        _, res_col = _PAID_COLS[bucket]
        setattr(wallet, res_col, getattr(wallet, res_col) - 1)
    wallet.version_lock += 1


def _release_reserved(wallet: EntitlementWallet, bucket: CreditBucket) -> None:
    if bucket == CB.FREE_LIFETIME:
        wallet.free_quota_reserved -= 1
    else:
        avail_col, res_col = _PAID_COLS[bucket]
        setattr(wallet, res_col, getattr(wallet, res_col) - 1)
        setattr(wallet, avail_col, getattr(wallet, avail_col) + 1)
    wallet.version_lock += 1


def _debit_available(wallet: EntitlementWallet, bucket: CreditBucket) -> bool:
    """Consume one credit directly from the available balance (recovery/upgrade)."""
    if bucket == CB.FREE_LIFETIME:
        if _free_available(wallet) < 1:
            return False
        wallet.free_quota_used += 1
    else:
        avail_col, _ = _PAID_COLS[bucket]
        if getattr(wallet, avail_col) < 1:
            return False
        setattr(wallet, avail_col, getattr(wallet, avail_col) - 1)
    wallet.version_lock += 1
    return True


def credit_available(wallet: EntitlementWallet, bucket: CreditBucket, n: int = 1) -> None:
    """Credit ``n`` units to a paid bucket's available balance (M6 fulfilment)."""
    if bucket == CB.FREE_LIFETIME:
        raise ValueError("free bucket is not credited via payment")
    avail_col, _ = _PAID_COLS[bucket]
    setattr(wallet, avail_col, getattr(wallet, avail_col) + n)
    wallet.version_lock += 1


def remove_reserved(wallet: EntitlementWallet, bucket: CreditBucket) -> None:
    """Remove one reserved paid credit without restoring availability (refund)."""
    if bucket == CB.FREE_LIFETIME:
        wallet.free_quota_reserved -= 1
    else:
        _, res_col = _PAID_COLS[bucket]
        setattr(wallet, res_col, getattr(wallet, res_col) - 1)
    wallet.version_lock += 1


# Public aliases for cross-module reuse (e.g. the reaper worker and payments).
bucket_balances = _bucket_balances
release_reserved = _release_reserved
debit_available = _debit_available


# ---- shared ledger append (used by the service and the reaper) ----------- #
async def ledger_tip(session: AsyncSession, user_id: uuid.UUID) -> tuple[int, str]:
    last = (
        await session.execute(
            select(EntitlementLedgerEntry)
            .where(EntitlementLedgerEntry.user_id == user_id)
            .order_by(EntitlementLedgerEntry.sequence_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if last is None:
        return 0, GENESIS_HASH
    return last.sequence_no, last.entry_hash


async def append_ledger(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    entry_type: str,
    credit_bucket: str,
    delta_available: int,
    delta_reserved: int,
    available_after: int,
    reserved_after: int,
    idempotency_key: str,
    reference_lease_id: uuid.UUID | None = None,
    reference_payment_order_id: uuid.UUID | None = None,
    reference_refund_id: uuid.UUID | None = None,
) -> None:
    """Append one hash-chained ledger entry (caller controls the transaction)."""
    seq, prev = await ledger_tip(session, user_id)
    seq += 1
    entry_hash = compute_entry_hash(
        user_id=str(user_id),
        sequence_no=seq,
        entry_type=entry_type,
        credit_bucket=credit_bucket,
        delta_available=delta_available,
        delta_reserved=delta_reserved,
        available_balance_after=available_after,
        reserved_balance_after=reserved_after,
        idempotency_key=idempotency_key,
        prev_entry_hash=prev,
    )
    session.add(
        EntitlementLedgerEntry(
            id=uuid.uuid4(),
            user_id=user_id,
            sequence_no=seq,
            entry_type=entry_type,
            credit_bucket=credit_bucket,
            delta_available=delta_available,
            delta_reserved=delta_reserved,
            available_balance_after=available_after,
            reserved_balance_after=reserved_after,
            reference_lease_id=reference_lease_id,
            reference_payment_order_id=reference_payment_order_id,
            reference_refund_id=reference_refund_id,
            idempotency_key=idempotency_key,
            prev_entry_hash=prev,
            entry_hash=entry_hash,
        )
    )
    await session.flush()


class EntitlementService:
    def __init__(self, session: AsyncSession, *, ctx: RequestContext) -> None:
        self.session = session
        self.ctx = ctx
        self.settings = get_settings()

    # ------------------------------------------------------------------ #
    # Wallet
    # ------------------------------------------------------------------ #
    async def get_wallet(self, user_id: uuid.UUID) -> EntitlementWallet:
        wallet = await self.session.get(EntitlementWallet, user_id)
        if wallet is None:
            wallet = EntitlementWallet(
                user_id=user_id,
                free_quota_total=self.settings.resolved_free_quota_total,
                free_quota_used=0,
                free_quota_reserved=0,
            )
            self.session.add(wallet)
            await self.session.flush()
        return wallet

    async def _lock_wallet(self, user_id: uuid.UUID) -> EntitlementWallet:
        wallet = (
            await self.session.execute(
                select(EntitlementWallet)
                .where(EntitlementWallet.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if wallet is None:
            await self.get_wallet(user_id)
            wallet = (
                await self.session.execute(
                    select(EntitlementWallet)
                    .where(EntitlementWallet.user_id == user_id)
                    .with_for_update()
                )
            ).scalar_one()
        return wallet

    async def _lock_lease(self, lease_id: uuid.UUID, user_id: uuid.UUID) -> EntitlementLease:
        lease = (
            await self.session.execute(
                select(EntitlementLease).where(EntitlementLease.id == lease_id).with_for_update()
            )
        ).scalar_one_or_none()
        # Do not leak existence of another user's lease.
        if lease is None or lease.user_id != user_id:
            raise NotFoundError("Lease not found")
        return lease

    async def _ledger_tip(self, user_id: uuid.UUID) -> tuple[int, str]:
        return await ledger_tip(self.session, user_id)

    async def _append_ledger(
        self,
        *,
        user_id: uuid.UUID,
        entry_type: str,
        credit_bucket: str,
        delta_available: int,
        delta_reserved: int,
        available_after: int,
        reserved_after: int,
        idempotency_key: str,
        reference_lease_id: uuid.UUID | None = None,
    ) -> None:
        await append_ledger(
            self.session,
            user_id=user_id,
            entry_type=entry_type,
            credit_bucket=credit_bucket,
            delta_available=delta_available,
            delta_reserved=delta_reserved,
            available_after=available_after,
            reserved_after=reserved_after,
            idempotency_key=idempotency_key,
            reference_lease_id=reference_lease_id,
        )

    async def _is_replay(self, user_id: uuid.UUID, idempotency_key: str) -> bool:
        existing = (
            await self.session.execute(
                select(EntitlementLedgerEntry.id).where(
                    EntitlementLedgerEntry.user_id == user_id,
                    EntitlementLedgerEntry.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        return existing is not None

    # ------------------------------------------------------------------ #
    # ELT
    # ------------------------------------------------------------------ #
    def _build_elt(self, lease: EntitlementLease, *, exp: dt.datetime) -> str:
        pdf, sig_img, esign = capabilities_for(GrantedEntitlementTier(lease.granted_tier))
        claims = {
            "iss": self.settings.jwt_issuer,
            "sub": str(lease.user_id),
            "jti": str(lease.id),
            "tid": str(lease.template_version_id),
            "tier": lease.granted_tier,
            "st": lease.lease_status,
            "dfd": lease.device_fingerprint_hash,
            "did": lease.draft_instance_id_hash,
            "cap": {"pdf": pdf, "sig_img": sig_img, "esign": esign},
            "cwe_hours": self.settings.lease_correction_window_hours,
            "iat": int(_now().timestamp()),
            "exp": int(exp.timestamp()),
        }
        return sign_elt(claims)

    def _elt_exp(self, lease: EntitlementLease) -> dt.datetime:
        if lease.lease_status == LeaseStatus.COMMITTED and lease.correction_window_expires_at:
            return lease.correction_window_expires_at
        return lease.reservation_expires_at

    # ------------------------------------------------------------------ #
    # Create lease
    # ------------------------------------------------------------------ #
    async def create_lease(
        self, auth: AuthContext, payload: object, idempotency_key: str
    ) -> LeaseResult:
        from app.modules.entitlements.schemas import CreateLeasePayload

        assert isinstance(payload, CreateLeasePayload)
        if payload.requested_tier == RequestedLeaseTier.PAID_ESIGN:
            raise ForbiddenError("eSign tier is disabled until Milestone 7")

        version = await self.session.get(TemplateVersion, payload.template_version_id)
        if version is None or version.status != TemplateVersionStatus.PUBLISHED:
            raise ValidationFailedError("template_version_id is not an eligible published version")

        bucket, granted = _TIER_MAP[payload.requested_tier]
        wallet = await self._lock_wallet(auth.user.id)

        # Idempotent replay (same create key) → return the existing lease.
        existing = (
            await self.session.execute(
                select(EntitlementLease).where(
                    EntitlementLease.user_id == auth.user.id,
                    EntitlementLease.create_idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return LeaseResult(
                existing, wallet, self._build_elt(existing, exp=self._elt_exp(existing))
            )

        # One active lease per (user, template_version, draft) → return it if present.
        active = (
            await self.session.execute(
                select(EntitlementLease).where(
                    EntitlementLease.user_id == auth.user.id,
                    EntitlementLease.template_version_id == payload.template_version_id,
                    EntitlementLease.draft_instance_id_hash == payload.draft_instance_id_hash,
                    EntitlementLease.lease_status.in_([LeaseStatus.ISSUED, LeaseStatus.COMMITTED]),
                )
            )
        ).scalar_one_or_none()
        if active is not None:
            return LeaseResult(active, wallet, self._build_elt(active, exp=self._elt_exp(active)))

        if not _reserve(wallet, bucket):
            raise PaymentRequiredError(
                "No available entitlement for the requested tier",
                eligible_skus=_ELIGIBLE_BASE_SKUS,
            )

        now = _now()
        lease_id = uuid.uuid4()
        lease = EntitlementLease(
            id=lease_id,
            user_id=auth.user.id,
            template_version_id=payload.template_version_id,
            granted_tier=granted,
            base_credit_bucket=bucket,
            is_upgraded_with_sig_30=False,
            lease_status=LeaseStatus.ISSUED,
            device_fingerprint_hash=payload.device_fingerprint_hash,
            draft_instance_id_hash=payload.draft_instance_id_hash,
            jwt_jti=str(lease_id),
            signing_key_id=active_key_id(),
            create_idempotency_key=idempotency_key,
            issued_at=now,
            reservation_expires_at=now
            + dt.timedelta(hours=self.settings.lease_reservation_ttl_hours),
        )
        self.session.add(lease)
        await self.session.flush()

        avail_after, res_after = _bucket_balances(wallet, bucket)
        await self._append_ledger(
            user_id=auth.user.id,
            entry_type=LedgerEntryType.LEASE_RESERVE,
            credit_bucket=bucket,
            delta_available=-1,
            delta_reserved=1,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idempotency_key,
            reference_lease_id=lease_id,
        )
        await self._audit(
            auth,
            EntitlementEvent.LEASE_RESERVE,
            lease,
            {"requested_tier": str(payload.requested_tier), "credit_bucket": str(bucket)},
        )
        await self.session.commit()
        return LeaseResult(lease, wallet, self._build_elt(lease, exp=lease.reservation_expires_at))

    # ------------------------------------------------------------------ #
    # Upgrade lease
    # ------------------------------------------------------------------ #
    async def upgrade_lease(
        self, auth: AuthContext, lease_id: uuid.UUID, payload: object, idempotency_key: str
    ) -> LeaseResult:
        from app.modules.entitlements.schemas import UpgradeLeasePayload

        assert isinstance(payload, UpgradeLeasePayload)
        wallet = await self._lock_wallet(auth.user.id)
        lease = await self._lock_lease(lease_id, auth.user.id)
        self._assert_draft_and_device(
            lease, payload.draft_instance_id_hash, payload.device_fingerprint_hash
        )

        if await self._is_replay(auth.user.id, idempotency_key):
            return LeaseResult(lease, wallet, self._build_elt(lease, exp=self._elt_exp(lease)))

        current = LeaseStatus(lease.lease_status)
        now = _now()
        if current == LeaseStatus.ISSUED:
            if now > lease.reservation_expires_at:
                raise GoneError("Reservation window expired", code="RESERVATION_EXPIRED")
        elif current == LeaseStatus.COMMITTED:
            if lease.correction_window_expires_at and now > lease.correction_window_expires_at:
                raise GoneError("Correction window expired", code="CORRECTION_WINDOW_EXPIRED")
        else:
            raise ConflictError("Lease is not eligible for upgrade")

        base_tier = GrantedEntitlementTier(lease.granted_tier)
        if lease.is_upgraded_with_sig_30 or base_tier not in _UPGRADE_MAP:
            raise ConflictError("Lease tier is not eligible for a signature upgrade")

        if not _debit_available(wallet, CB.SIGNATURE_UPGRADE_30):
            raise PaymentRequiredError(
                "No available SIG_UPGRADE_30 credit",
                eligible_skus=[SkuCode.SIG_UPGRADE_30.value],
            )

        lease.granted_tier = _UPGRADE_MAP[base_tier]
        lease.is_upgraded_with_sig_30 = True
        lease.signing_key_id = active_key_id()

        avail_after, res_after = _bucket_balances(wallet, CB.SIGNATURE_UPGRADE_30)
        await self._append_ledger(
            user_id=auth.user.id,
            entry_type=LedgerEntryType.LEASE_UPGRADE_COMMIT,
            credit_bucket=CB.SIGNATURE_UPGRADE_30,
            delta_available=-1,
            delta_reserved=0,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idempotency_key,
            reference_lease_id=lease.id,
        )
        await self._audit(
            auth,
            EntitlementEvent.LEASE_UPGRADE,
            lease,
            {"upgrade_sku": payload.upgrade_sku, "granted_tier": lease.granted_tier},
        )
        await self.session.commit()
        return LeaseResult(lease, wallet, self._build_elt(lease, exp=self._elt_exp(lease)))

    # ------------------------------------------------------------------ #
    # Commit lease
    # ------------------------------------------------------------------ #
    async def commit_lease(
        self, auth: AuthContext, lease_id: uuid.UUID, payload: object, idempotency_key: str
    ) -> LeaseResult:
        from app.modules.entitlements.schemas import CommitLeasePayload

        assert isinstance(payload, CommitLeasePayload)
        wallet = await self._lock_wallet(auth.user.id)
        lease = await self._lock_lease(lease_id, auth.user.id)
        if lease.draft_instance_id_hash != payload.draft_instance_id_hash:
            raise ValidationFailedError("draft_instance_id_hash does not match this lease")

        if await self._is_replay(auth.user.id, idempotency_key):
            return LeaseResult(lease, wallet, self._build_elt(lease, exp=self._elt_exp(lease)))

        lease.client_completion_receipt_hash = payload.client_completion_receipt_hash
        lease.client_reported_completed_at = payload.completed_at_client_iso
        bucket = CreditBucket(lease.base_credit_bucket)
        current = LeaseStatus(lease.lease_status)
        now = _now()

        if current == LeaseStatus.ISSUED:
            await self._commit_first(auth, wallet, lease, bucket, idempotency_key, now)
        elif current == LeaseStatus.EXPIRED_RELEASED:
            await self._commit_recovery(auth, wallet, lease, bucket, idempotency_key, now, payload)
        elif current == LeaseStatus.COMMITTED:
            await self._commit_within_window(auth, wallet, lease, bucket, idempotency_key, now)
        else:
            raise ConflictError("Lease cannot be committed from its current state")

        await self.session.commit()
        return LeaseResult(lease, wallet, self._build_elt(lease, exp=self._elt_exp(lease)))

    async def _commit_first(
        self,
        auth: AuthContext,
        wallet: EntitlementWallet,
        lease: EntitlementLease,
        bucket: CreditBucket,
        idem: str,
        now: dt.datetime,
    ) -> None:
        _commit_reserved(wallet, bucket)
        lease.lease_status = LeaseStatus.COMMITTED
        lease.first_committed_at = now
        lease.last_committed_at = now
        lease.commit_count = 1
        lease.correction_window_expires_at = now + dt.timedelta(
            hours=self.settings.lease_correction_window_hours
        )
        avail_after, res_after = _bucket_balances(wallet, bucket)
        await self._append_ledger(
            user_id=auth.user.id,
            entry_type=LedgerEntryType.LEASE_COMMIT,
            credit_bucket=bucket,
            delta_available=0,
            delta_reserved=-1,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idem,
            reference_lease_id=lease.id,
        )
        await self._audit(
            auth, EntitlementEvent.LEASE_COMMIT, lease, {"commit_count": lease.commit_count}
        )

    async def _commit_recovery(
        self,
        auth: AuthContext,
        wallet: EntitlementWallet,
        lease: EntitlementLease,
        bucket: CreditBucket,
        idem: str,
        now: dt.datetime,
        payload: object,
    ) -> None:
        # Authoritative gate: credit must still be AVAILABLE in the wallet.
        if not _debit_available(wallet, bucket):
            await self._audit(
                auth,
                EntitlementEvent.LATE_COMMIT_CREDIT_SPENT,
                lease,
                {"credit_bucket": str(bucket)},
            )
            await self.session.commit()
            raise ConflictError(
                "Restored credit already consumed", code="LEASE_EXPIRED_CREDIT_CONSUMED"
            )
        lease.lease_status = LeaseStatus.COMMITTED
        lease.first_committed_at = now
        lease.last_committed_at = now
        lease.commit_count = 1
        # Client timestamp is untrusted: cap it at the server reservation window
        # before adding 24h, so a forward-dated client clock cannot extend it.
        from app.modules.entitlements.schemas import CommitLeasePayload

        assert isinstance(payload, CommitLeasePayload)
        client_ts = payload.completed_at_client_iso or lease.reservation_expires_at
        capped = min(client_ts, lease.reservation_expires_at)
        window = max(capped + dt.timedelta(hours=self.settings.lease_correction_window_hours), now)
        lease.correction_window_expires_at = window
        avail_after, res_after = _bucket_balances(wallet, bucket)
        await self._append_ledger(
            user_id=auth.user.id,
            entry_type=LedgerEntryType.LEASE_COMMIT,
            credit_bucket=bucket,
            delta_available=-1,
            delta_reserved=0,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idem,
            reference_lease_id=lease.id,
        )
        await self._audit(
            auth,
            EntitlementEvent.LATE_COMMIT_RECOVERED,
            lease,
            {"commit_count": lease.commit_count},
        )

    async def _commit_within_window(
        self,
        auth: AuthContext,
        wallet: EntitlementWallet,
        lease: EntitlementLease,
        bucket: CreditBucket,
        idem: str,
        now: dt.datetime,
    ) -> None:
        if lease.correction_window_expires_at and now > lease.correction_window_expires_at:
            raise GoneError("Correction window expired", code="CORRECTION_WINDOW_EXPIRED")
        lease.commit_count += 1
        lease.last_committed_at = now
        # Zero-delta ledger entry records the re-export and enforces idempotency;
        # wallet balances are unchanged within the correction window.
        avail_after, res_after = _bucket_balances(wallet, bucket)
        await self._append_ledger(
            user_id=auth.user.id,
            entry_type=LedgerEntryType.LEASE_COMMIT,
            credit_bucket=bucket,
            delta_available=0,
            delta_reserved=0,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idem,
            reference_lease_id=lease.id,
        )
        await self._audit(
            auth, EntitlementEvent.LEASE_RECOMMIT, lease, {"commit_count": lease.commit_count}
        )

    # ------------------------------------------------------------------ #
    # Release lease
    # ------------------------------------------------------------------ #
    async def release_lease(
        self, auth: AuthContext, lease_id: uuid.UUID, payload: object, idempotency_key: str
    ) -> EntitlementLease:
        from app.modules.entitlements.schemas import ReleaseLeasePayload

        assert isinstance(payload, ReleaseLeasePayload)
        wallet = await self._lock_wallet(auth.user.id)
        lease = await self._lock_lease(lease_id, auth.user.id)
        if lease.draft_instance_id_hash != payload.draft_instance_id_hash:
            raise ValidationFailedError("draft_instance_id_hash does not match this lease")

        if lease.lease_status == LeaseStatus.RELEASED:
            return lease  # idempotent
        if await self._is_replay(auth.user.id, idempotency_key):
            return lease
        if lease.lease_status != LeaseStatus.ISSUED or lease.commit_count != 0:
            raise ConflictError("Only an uncommitted ISSUED lease can be released")

        bucket = CreditBucket(lease.base_credit_bucket)
        _release_reserved(wallet, bucket)
        lease.lease_status = LeaseStatus.RELEASED
        lease.released_at = _now()
        avail_after, res_after = _bucket_balances(wallet, bucket)
        await self._append_ledger(
            user_id=auth.user.id,
            entry_type=LedgerEntryType.LEASE_RELEASE,
            credit_bucket=bucket,
            delta_available=1,
            delta_reserved=-1,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idempotency_key,
            reference_lease_id=lease.id,
        )
        await self._audit(auth, EntitlementEvent.LEASE_RELEASE, lease, {})
        await self.session.commit()
        return lease

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _assert_draft_and_device(self, lease: EntitlementLease, draft: str, device: str) -> None:
        if lease.draft_instance_id_hash != draft:
            raise ValidationFailedError("draft_instance_id_hash does not match this lease")
        if lease.device_fingerprint_hash != device:
            raise ValidationFailedError("device_fingerprint_hash does not match this lease")

    async def _audit(
        self, auth: AuthContext, event_type: str, lease: EntitlementLease, meta: dict[str, object]
    ) -> None:
        payload = {
            "lease_id": str(lease.id),
            "template_version_id": str(lease.template_version_id),
            "lease_status": str(lease.lease_status),
            **meta,
        }
        await audit.record_event(
            self.session,
            event_type=event_type,
            resource_type="entitlement_lease",
            resource_id=str(lease.id),
            request_id=self.ctx.request_id,
            actor_user_id=auth.user.id,
            actor_role=auth.role,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata=payload,
        )
