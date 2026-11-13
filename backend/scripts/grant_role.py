"""Assign a template-administration role to an existing account (development only).

    python -m scripts.grant_role +919876500001 TEMPLATE_CURATOR

The account must already exist (sign in once with that mobile number first). The
mobile number is looked up by its blind index; it is never stored or printed.

Refuses to run in staging or production: how admin roles are provisioned there
(who approves, how it is recorded, how access is reviewed) is an open decision —
see docs/DECISION_REGISTER.md (DEC-8).
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.constants import UserRole
from app.core.hashing import mobile_blind_index
from app.modules.audit import service as audit
from app.modules.auth.models import User

_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")


async def grant(mobile_e164: str, role: UserRole) -> str:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = (
                await session.execute(
                    select(User).where(User.mobile_blind_index == mobile_blind_index(mobile_e164))
                )
            ).scalar_one_or_none()
            if user is None:
                raise SystemExit("No account for that number. Sign in once first, then retry.")
            previous = user.role
            user.role = role
            await audit.record_event(
                session,
                event_type="ADMIN_ROLE_ASSIGNED",
                resource_type="user",
                resource_id=str(user.id),
                request_id=f"grant-role-{uuid.uuid4()}",
                actor_user_id=None,
                actor_role=UserRole.SUPER_ADMIN,
                metadata={"role": str(role), "reason": f"cli: was {previous}"},
            )
            await session.commit()
            return f"{user.id} {previous} -> {role}"
    finally:
        await engine.dispose()


def main() -> None:
    if get_settings().is_shared_environment:
        raise SystemExit("grant_role is for local development only (see DEC-8)")
    if len(sys.argv) != 3 or not _MOBILE.match(sys.argv[1]):
        raise SystemExit("usage: python -m scripts.grant_role +91XXXXXXXXXX ROLE")
    try:
        role = UserRole(sys.argv[2])
    except ValueError:
        raise SystemExit(f"role must be one of {', '.join(r.value for r in UserRole)}") from None
    print(asyncio.run(grant(sys.argv[1], role)))


if __name__ == "__main__":
    main()
