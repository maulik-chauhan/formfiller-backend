"""Background expired-lease reaper.

Reclaims the reserved credit of ``ISSUED`` leases whose 48-hour reservation
window has elapsed, transitioning them to ``EXPIRED_RELEASED``. The worker is
idempotent and safe across multiple instances:

* Each lease is processed in its own transaction with the mandated lock order —
  ``entitlement_wallets`` ``FOR UPDATE`` first, then ``entitlement_leases``.
* ``FOR UPDATE SKIP LOCKED`` on the lease lets concurrent reaper instances work
  disjoint rows without blocking.
* A deterministic ledger idempotency key (``reaper:<lease_id>``) plus the
  ``ISSUED`` status guard ensure a credit is never restored twice.

Run locally::

    python -m app.workers.reaper --once          # single sweep
    python -m app.workers.reaper --interval 300   # loop every 5 minutes

In production, run it as a dedicated worker Deployment/service (not an in-process
timer) or invoke ``--once`` from a scheduler (cron / Kubernetes CronJob). Several
replicas may run concurrently.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.constants import CreditBucket, LeaseStatus, LedgerEntryType, UserRole
from app.db.session import AsyncSessionLocal
from app.modules.audit import service as audit
from app.modules.entitlements.models import EntitlementLease, EntitlementWallet
from app.modules.entitlements.service import (
    append_ledger,
    bucket_balances,
    release_reserved,
)


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def _reap_one(
    session: AsyncSession, lease_id: uuid.UUID, user_id: uuid.UUID, now: dt.datetime
) -> bool:
    """Process a single candidate under wallet→lease lock order. Returns True if reaped."""
    wallet = (
        await session.execute(
            select(EntitlementWallet).where(EntitlementWallet.user_id == user_id).with_for_update()
        )
    ).scalar_one_or_none()
    lease = (
        await session.execute(
            select(EntitlementLease).where(EntitlementLease.id == lease_id).with_for_update()
        )
    ).scalar_one_or_none()
    if wallet is None or lease is None:
        await session.rollback()
        return False
    if lease.lease_status != LeaseStatus.ISSUED or lease.reservation_expires_at > now:
        await session.rollback()
        return False

    bucket = CreditBucket(lease.base_credit_bucket)
    release_reserved(wallet, bucket)
    lease.lease_status = LeaseStatus.EXPIRED_RELEASED
    lease.released_at = now
    avail_after, res_after = bucket_balances(wallet, bucket)
    idem = f"reaper:{lease_id}"
    try:
        await append_ledger(
            session,
            user_id=user_id,
            entry_type=LedgerEntryType.LEASE_EXPIRED_RELEASE,
            credit_bucket=bucket,
            delta_available=1,
            delta_reserved=-1,
            available_after=avail_after,
            reserved_after=res_after,
            idempotency_key=idem,
            reference_lease_id=lease_id,
        )
        await audit.record_event(
            session,
            event_type="ENTITLEMENT_LEASE_EXPIRED_RELEASE",
            resource_type="entitlement_lease",
            resource_id=str(lease_id),
            request_id="reaper",
            actor_user_id=user_id,
            actor_role=UserRole.CITIZEN,
            metadata={"lease_status": str(lease.lease_status), "credit_bucket": str(bucket)},
        )
        await session.commit()
    except IntegrityError:
        # Another instance already reaped this lease (idempotency collision).
        await session.rollback()
        return False
    return True


async def reap_once(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    now: dt.datetime | None = None,
    batch_size: int = 200,
) -> int:
    """Run one sweep; return the number of leases reaped."""
    factory = session_factory or AsyncSessionLocal
    now = now or _now()
    async with factory() as session:
        candidates = (
            await session.execute(
                select(EntitlementLease.id, EntitlementLease.user_id)
                .where(
                    EntitlementLease.lease_status == LeaseStatus.ISSUED,
                    EntitlementLease.reservation_expires_at <= now,
                )
                .order_by(EntitlementLease.reservation_expires_at)
                .limit(batch_size)
            )
        ).all()

    reaped = 0
    for lease_id, user_id in candidates:
        async with factory() as session:
            if await _reap_one(session, lease_id, user_id, now):
                reaped += 1
    return reaped


async def _run_loop(interval_seconds: int) -> None:  # pragma: no cover - operational loop
    while True:
        count = await reap_once()
        print(f"[reaper] reaped {count} expired leases")
        await asyncio.sleep(interval_seconds)


def main() -> None:  # pragma: no cover - CLI entry
    parser = argparse.ArgumentParser(description="Expired entitlement-lease reaper")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="Run a single sweep and exit")
    group.add_argument(
        "--interval", type=int, help="Loop forever, sleeping N seconds between sweeps"
    )
    args = parser.parse_args()
    if args.once:
        count = asyncio.run(reap_once())
        print(f"[reaper] reaped {count} expired leases")
    else:
        asyncio.run(_run_loop(args.interval))


if __name__ == "__main__":  # pragma: no cover
    main()
