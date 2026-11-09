"""Authentication & session-security service.

Implements the OTP lifecycle, blind-index identity, rotating refresh tokens with
token-family reuse detection, session management and account deletion with an
anti-abuse quota tombstone. All multi-row state changes use row locks and run in
a single transaction committed by the service.
"""

from __future__ import annotations

import datetime as dt
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.constants import (
    AccountStatus,
    ClientPlatform,
    OtpChallengeStatus,
    OtpChannel,
    OtpPurpose,
    SessionRevocationReason,
    UserRole,
)
from app.core.dependencies import AuthContext, RequestContext
from app.core.errors import (
    ConflictError,
    NotFoundError,
    TooManyRequestsError,
    UnauthorizedError,
    ValidationFailedError,
)
from app.core.hashing import (
    generate_otp_code,
    mobile_blind_index,
    otp_code_hmac,
    sha256_hex,
    verify_otp_hmac,
)
from app.core.rate_limit import auth_rate_limiter
from app.core.security import create_access_token
from app.modules.audit import service as audit
from app.modules.auth import schemas
from app.modules.auth.models import AuthSession, DeletedAccountQuota, OtpChallenge, User
from app.modules.auth.sms_provider import OtpDeliveryProvider
from app.modules.entitlements.models import EntitlementWallet


class AuthEvent:
    """Canonical auth audit event types (no PII ever attached)."""

    OTP_REQUESTED = "AUTH_OTP_REQUESTED"
    OTP_VERIFY_SUCCESS = "AUTH_OTP_VERIFY_SUCCESS"
    OTP_VERIFY_FAILED = "AUTH_OTP_VERIFY_FAILED"
    ACCOUNT_REGISTERED = "AUTH_ACCOUNT_REGISTERED"
    LOGIN_SUCCESS = "AUTH_LOGIN_SUCCESS"
    REFRESH_ROTATED = "AUTH_REFRESH_TOKEN_ROTATED"
    TOKEN_REUSE_DETECTED = "SECURITY_TOKEN_REUSE_DETECTED"
    LOGOUT = "AUTH_LOGOUT"
    SESSION_REVOKED = "AUTH_SESSION_REVOKED"
    ACCOUNT_DELETED = "AUTH_ACCOUNT_DELETED"


@dataclass
class IssuedAuth:
    """Result of a successful authentication / rotation."""

    user: User
    session: AuthSession
    access_token: str
    refresh_token: str
    wallet: EntitlementWallet
    is_new_user: bool


@dataclass
class DeletionResult:
    user_id: uuid.UUID
    deleted_at: dt.datetime
    revoked_session_count: int
    free_quota_tombstoned: int
    tombstone_expires_at: dt.datetime


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        ctx: RequestContext,
        otp_provider: OtpDeliveryProvider,
    ) -> None:
        self.session = session
        self.ctx = ctx
        self.otp_provider = otp_provider
        self.settings = get_settings()

    # ------------------------------------------------------------------ #
    # OTP request
    # ------------------------------------------------------------------ #
    async def request_otp(self, payload: schemas.OtpRequestPayload) -> OtpChallenge:
        target_bi = mobile_blind_index(payload.mobile_e164)
        now = _now()
        hour_ago = now - dt.timedelta(hours=1)

        # Hourly request caps per mobile and per IP subnet (DB-backed).
        per_mobile = await self.session.scalar(
            select(func.count())
            .select_from(OtpChallenge)
            .where(
                OtpChallenge.target_blind_index == target_bi, OtpChallenge.created_at >= hour_ago
            )
        )
        per_ip = await self.session.scalar(
            select(func.count())
            .select_from(OtpChallenge)
            .where(
                OtpChallenge.client_ip_subnet_hash == self.ctx.ip_subnet_hash,
                OtpChallenge.created_at >= hour_ago,
            )
        )
        limit = self.settings.otp_request_max_per_hour
        if (per_mobile or 0) >= limit or (per_ip or 0) >= limit:
            raise TooManyRequestsError("OTP request limit reached; try again later")

        # Resend cooldown: only an unused (PENDING) recent challenge blocks a
        # resend, so a freshly consumed login OTP never blocks a new request.
        last_created = await self.session.scalar(
            select(func.max(OtpChallenge.created_at)).where(
                OtpChallenge.target_blind_index == target_bi,
                OtpChallenge.status == OtpChallengeStatus.PENDING,
            )
        )
        cooldown = self.settings.otp_resend_cooldown_seconds
        if last_created is not None and (now - last_created).total_seconds() < cooldown:
            raise TooManyRequestsError("Please wait before requesting another OTP")

        existing_user = await self._find_active_user_by_mobile(target_bi)
        challenge_id = uuid.uuid4()
        code = generate_otp_code()
        challenge = OtpChallenge(
            id=challenge_id,
            user_id=existing_user.id if existing_user else None,
            target_blind_index=target_bi,
            channel=OtpChannel.MOBILE_SMS,
            purpose=payload.purpose,
            otp_code_hmac=otp_code_hmac(str(challenge_id), code),
            max_attempts=self.settings.otp_max_attempts,
            status=OtpChallengeStatus.PENDING,
            client_ip_subnet_hash=self.ctx.ip_subnet_hash,
            expires_at=now + dt.timedelta(seconds=self.settings.otp_ttl_seconds),
        )
        self.session.add(challenge)
        await self.session.flush()

        await self.otp_provider.send_otp(
            mobile_e164=payload.mobile_e164, code=code, challenge_id=str(challenge_id)
        )
        await audit.record_event(
            self.session,
            event_type=AuthEvent.OTP_REQUESTED,
            resource_type="otp_challenge",
            resource_id=str(challenge_id),
            request_id=self.ctx.request_id,
            actor_user_id=existing_user.id if existing_user else None,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"purpose": str(payload.purpose), "channel": str(OtpChannel.MOBILE_SMS)},
        )
        await self.session.commit()
        return challenge

    # ------------------------------------------------------------------ #
    # OTP verify (login / register)
    # ------------------------------------------------------------------ #
    async def verify_otp(self, payload: schemas.OtpVerifyPayload) -> IssuedAuth:
        allowed = await auth_rate_limiter.check_and_consume(
            f"otp_verify:{self.ctx.ip_subnet_hash}",
            limit=self.settings.otp_verify_max_per_minute_per_ip,
            window_seconds=60,
        )
        if not allowed:
            raise TooManyRequestsError("Too many verification attempts; slow down")

        challenge = await self._lock_challenge(payload.challenge_id)
        self._assert_challenge_consumable(challenge)
        if challenge.purpose != OtpPurpose.LOGIN_OR_REGISTER:
            raise ValidationFailedError("Challenge purpose mismatch")

        await self._consume_attempt_or_fail(challenge, payload.otp_code)

        challenge.status = OtpChallengeStatus.VERIFIED
        challenge.consumed_at = _now()

        target_bi = challenge.target_blind_index
        user = await self._find_active_user_by_mobile(target_bi)
        is_new_user = user is None
        if user is None:
            user, wallet = await self._register_user(target_bi)
        else:
            wallet = await self._get_wallet(user.id)
            user.last_login_at = _now()
            if not user.is_mobile_verified:
                user.is_mobile_verified = True
                user.mobile_verified_at = _now()

        issued = await self._issue_session(
            user=user,
            platform=payload.platform,
            device_fingerprint_hash=payload.device_fingerprint_hash,
        )

        await audit.record_event(
            self.session,
            event_type=AuthEvent.LOGIN_SUCCESS,
            resource_type="user",
            resource_id=str(user.id),
            request_id=self.ctx.request_id,
            actor_user_id=user.id,
            actor_role=UserRole(user.role),
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"session_id": str(issued.id), "is_new_user": is_new_user},
        )
        await self.session.commit()
        access = create_access_token(
            user_id=user.id, session_id=issued.id, role=UserRole(user.role)
        )
        return IssuedAuth(
            user=user,
            session=issued,
            access_token=access,
            refresh_token=self._pending_refresh_plain,
            wallet=wallet,
            is_new_user=is_new_user,
        )

    # ------------------------------------------------------------------ #
    # Refresh-token rotation with family reuse detection
    # ------------------------------------------------------------------ #
    async def refresh(self, payload: schemas.TokenRefreshPayload) -> IssuedAuth:
        token_hash = sha256_hex(payload.refresh_token.encode("utf-8"))
        session_row = (
            await self.session.execute(
                select(AuthSession)
                .where(AuthSession.refresh_token_sha256 == token_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if session_row is None:
            raise UnauthorizedError("Invalid refresh token")

        # Reuse detection: a token whose session is already revoked is a replay
        # of a rotated/expired token → revoke the whole family.
        if session_row.revoked_at is not None:
            await self._revoke_family(
                session_row.token_family_id, SessionRevocationReason.TOKEN_REUSE_DETECTED
            )
            await audit.record_event(
                self.session,
                event_type=AuthEvent.TOKEN_REUSE_DETECTED,
                resource_type="token_family",
                resource_id=str(session_row.token_family_id),
                request_id=self.ctx.request_id,
                actor_user_id=session_row.user_id,
                ip_subnet_hash=self.ctx.ip_subnet_hash,
                metadata={"token_family_id": str(session_row.token_family_id)},
            )
            await self.session.commit()
            raise UnauthorizedError("Refresh token reuse detected; all sessions revoked")

        if session_row.expires_at <= _now():
            raise UnauthorizedError("Refresh token expired")
        if not secrets.compare_digest(
            session_row.device_fingerprint_hash, payload.device_fingerprint_hash
        ):
            raise UnauthorizedError("Device fingerprint mismatch")

        user = await self.session.get(User, session_row.user_id)
        if user is None or user.account_status != AccountStatus.ACTIVE:
            raise UnauthorizedError("Account is not active")

        new_session = await self._issue_session(
            user=user,
            platform=ClientPlatform(session_row.platform),
            device_fingerprint_hash=session_row.device_fingerprint_hash,
            token_family_id=session_row.token_family_id,
        )
        session_row.revoked_at = _now()
        session_row.revocation_reason = SessionRevocationReason.TOKEN_ROTATED
        session_row.replaced_by_session_id = new_session.id

        wallet = await self._get_wallet(user.id)
        await audit.record_event(
            self.session,
            event_type=AuthEvent.REFRESH_ROTATED,
            resource_type="auth_session",
            resource_id=str(new_session.id),
            request_id=self.ctx.request_id,
            actor_user_id=user.id,
            actor_role=UserRole(user.role),
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={
                "session_id": str(new_session.id),
                "previous_session_id": str(session_row.id),
                "token_family_id": str(session_row.token_family_id),
            },
        )
        await self.session.commit()
        access = create_access_token(
            user_id=user.id, session_id=new_session.id, role=UserRole(user.role)
        )
        return IssuedAuth(
            user=user,
            session=new_session,
            access_token=access,
            refresh_token=self._pending_refresh_plain,
            wallet=wallet,
            is_new_user=False,
        )

    # ------------------------------------------------------------------ #
    # Logout / session management
    # ------------------------------------------------------------------ #
    async def logout(self, auth: AuthContext, *, revoke_all_devices: bool) -> None:
        now = _now()
        if revoke_all_devices:
            rows = (
                (
                    await self.session.execute(
                        select(AuthSession)
                        .where(
                            AuthSession.user_id == auth.user.id,
                            AuthSession.revoked_at.is_(None),
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.revoked_at = now
                row.revocation_reason = SessionRevocationReason.USER_LOGOUT
            count = len(rows)
        else:
            current = await self.session.get(AuthSession, auth.session.id, with_for_update=True)
            if current is not None and current.revoked_at is None:
                current.revoked_at = now
                current.revocation_reason = SessionRevocationReason.USER_LOGOUT
            count = 1

        await audit.record_event(
            self.session,
            event_type=AuthEvent.LOGOUT,
            resource_type="user",
            resource_id=str(auth.user.id),
            request_id=self.ctx.request_id,
            actor_user_id=auth.user.id,
            actor_role=auth.role,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"revoked_session_count": count, "reason": "USER_LOGOUT"},
        )
        await self.session.commit()

    async def list_sessions(self, auth: AuthContext) -> list[schemas.SessionInfo]:
        rows = (
            (
                await self.session.execute(
                    select(AuthSession)
                    .where(
                        AuthSession.user_id == auth.user.id,
                        AuthSession.revoked_at.is_(None),
                        AuthSession.expires_at > _now(),
                    )
                    .order_by(AuthSession.last_used_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [
            schemas.SessionInfo(
                session_id=row.id,
                platform=ClientPlatform(row.platform),
                device_fingerprint_hash=row.device_fingerprint_hash,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
                expires_at=row.expires_at,
                is_current=row.id == auth.session.id,
            )
            for row in rows
        ]

    async def revoke_session(self, auth: AuthContext, session_id: uuid.UUID) -> None:
        row = await self.session.get(AuthSession, session_id, with_for_update=True)
        if row is None or row.user_id != auth.user.id:
            raise NotFoundError("Session not found")
        if row.revoked_at is None:
            row.revoked_at = _now()
            row.revocation_reason = SessionRevocationReason.USER_LOGOUT
            await audit.record_event(
                self.session,
                event_type=AuthEvent.SESSION_REVOKED,
                resource_type="auth_session",
                resource_id=str(session_id),
                request_id=self.ctx.request_id,
                actor_user_id=auth.user.id,
                actor_role=auth.role,
                ip_subnet_hash=self.ctx.ip_subnet_hash,
                metadata={"session_id": str(session_id)},
            )
        await self.session.commit()

    # ------------------------------------------------------------------ #
    # Account deletion
    # ------------------------------------------------------------------ #
    async def delete_account(
        self, auth: AuthContext, payload: schemas.AccountDeletePayload
    ) -> DeletionResult:
        user = await self.session.get(User, auth.user.id, with_for_update=True)
        if user is None:
            raise NotFoundError("User not found")

        # Fresh OTP verification bound to this account's mobile + ACCOUNT_DELETE.
        challenge = await self._lock_challenge(payload.otp_challenge_id)
        self._assert_challenge_consumable(challenge)
        if challenge.purpose != OtpPurpose.ACCOUNT_DELETE:
            raise ValidationFailedError("Challenge purpose mismatch")
        if user.mobile_blind_index is None or not secrets.compare_digest(
            challenge.target_blind_index, user.mobile_blind_index
        ):
            raise ValidationFailedError("Challenge does not match this account")
        await self._consume_attempt_or_fail(challenge, payload.otp_code)
        challenge.status = OtpChallengeStatus.VERIFIED
        challenge.consumed_at = _now()

        original_bi = user.mobile_blind_index
        key_version = user.blind_index_key_version
        wallet = await self._get_wallet(user.id)
        consumed = wallet.free_quota_used if wallet is not None else 0

        now = _now()
        active_sessions = (
            (
                await self.session.execute(
                    select(AuthSession)
                    .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        for row in active_sessions:
            row.revoked_at = now
            row.revocation_reason = SessionRevocationReason.ACCOUNT_DELETED
        revoked_count = len(active_sessions)

        retention_days = self.settings.deleted_quota_retention_days
        expires_at = now + dt.timedelta(days=retention_days)
        stmt = (
            pg_insert(DeletedAccountQuota)
            .values(
                mobile_blind_index=original_bi,
                blind_index_key_version=key_version,
                free_quota_consumed_total=consumed,
                deleted_at=now,
                expires_at=expires_at,
            )
            .on_conflict_do_update(
                index_elements=["mobile_blind_index"],
                set_={
                    "free_quota_consumed_total": func.greatest(
                        DeletedAccountQuota.free_quota_consumed_total, consumed
                    ),
                    "blind_index_key_version": key_version,
                    "deleted_at": now,
                    "expires_at": expires_at,
                },
            )
        )
        await self.session.execute(stmt)

        # Anonymise account metadata; preserve wallet + ledger for integrity.
        user.mobile_blind_index = None
        user.email_blind_index = None
        user.account_status = AccountStatus.DELETED
        user.deleted_at = now

        await audit.record_event(
            self.session,
            event_type=AuthEvent.ACCOUNT_DELETED,
            resource_type="user",
            resource_id=str(user.id),
            request_id=self.ctx.request_id,
            actor_user_id=user.id,
            actor_role=UserRole(user.role),
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"revoked_session_count": revoked_count},
        )
        await self.session.commit()
        return DeletionResult(
            user_id=user.id,
            deleted_at=now,
            revoked_session_count=revoked_count,
            free_quota_tombstoned=consumed,
            tombstone_expires_at=expires_at,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    async def _find_active_user_by_mobile(self, blind_index: str) -> User | None:
        return (
            await self.session.execute(
                select(User).where(
                    User.mobile_blind_index == blind_index,
                    User.account_status == AccountStatus.ACTIVE,
                )
            )
        ).scalar_one_or_none()

    async def _register_user(self, blind_index: str) -> tuple[User, EntitlementWallet]:
        now = _now()
        user = User(
            id=uuid.uuid4(),
            mobile_blind_index=blind_index,
            blind_index_key_version=self.settings.blind_index_key_version,
            role=UserRole.CITIZEN,
            account_status=AccountStatus.ACTIVE,
            is_mobile_verified=True,
            mobile_verified_at=now,
            last_login_at=now,
        )
        self.session.add(user)
        await self.session.flush()

        # Anti-abuse: restore previously consumed free quota if a non-expired
        # tombstone exists for this mobile blind index.
        restored_used = 0
        tombstone = await self.session.get(DeletedAccountQuota, blind_index)
        if tombstone is not None and tombstone.expires_at > now:
            restored_used = tombstone.free_quota_consumed_total

        total = self.settings.resolved_free_quota_total
        used = min(restored_used, total)
        wallet = EntitlementWallet(
            user_id=user.id,
            free_quota_total=total,
            free_quota_used=used,
            free_quota_reserved=0,
        )
        self.session.add(wallet)
        await self.session.flush()

        await audit.record_event(
            self.session,
            event_type=AuthEvent.ACCOUNT_REGISTERED,
            resource_type="user",
            resource_id=str(user.id),
            request_id=self.ctx.request_id,
            actor_user_id=user.id,
            actor_role=UserRole.CITIZEN,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"quota_restored": used > 0},
        )
        return user, wallet

    async def _get_wallet(self, user_id: uuid.UUID) -> EntitlementWallet:
        wallet = await self.session.get(EntitlementWallet, user_id)
        if wallet is None:
            # Defensive: every account should have a wallet from registration.
            wallet = EntitlementWallet(
                user_id=user_id,
                free_quota_total=self.settings.resolved_free_quota_total,
                free_quota_used=0,
                free_quota_reserved=0,
            )
            self.session.add(wallet)
            await self.session.flush()
        return wallet

    _pending_refresh_plain: str = ""

    async def _issue_session(
        self,
        *,
        user: User,
        platform: ClientPlatform,
        device_fingerprint_hash: str,
        token_family_id: uuid.UUID | None = None,
    ) -> AuthSession:
        now = _now()
        refresh_plain = secrets.token_urlsafe(32)
        self._pending_refresh_plain = refresh_plain
        auth_session = AuthSession(
            id=uuid.uuid4(),
            user_id=user.id,
            token_family_id=token_family_id or uuid.uuid4(),
            refresh_token_sha256=sha256_hex(refresh_plain.encode("utf-8")),
            device_fingerprint_hash=device_fingerprint_hash,
            platform=platform,
            expires_at=now + dt.timedelta(days=self.settings.refresh_token_ttl_days),
            last_used_at=now,
        )
        self.session.add(auth_session)
        await self.session.flush()
        return auth_session

    async def _revoke_family(
        self, token_family_id: uuid.UUID, reason: SessionRevocationReason
    ) -> None:
        rows = (
            (
                await self.session.execute(
                    select(AuthSession)
                    .where(
                        AuthSession.token_family_id == token_family_id,
                        AuthSession.revoked_at.is_(None),
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        now = _now()
        for row in rows:
            row.revoked_at = now
            row.revocation_reason = reason

    async def _lock_challenge(self, challenge_id: uuid.UUID) -> OtpChallenge:
        row = (
            await self.session.execute(
                select(OtpChallenge).where(OtpChallenge.id == challenge_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise UnauthorizedError("Invalid OTP challenge")
        return row

    def _assert_challenge_consumable(self, challenge: OtpChallenge) -> None:
        if challenge.status == OtpChallengeStatus.VERIFIED:
            raise ConflictError("OTP challenge already used")
        if challenge.status != OtpChallengeStatus.PENDING:
            raise UnauthorizedError("OTP challenge is not usable")
        if challenge.expires_at <= _now():
            challenge.status = OtpChallengeStatus.EXPIRED
            raise UnauthorizedError("OTP challenge expired")
        if challenge.attempt_count >= challenge.max_attempts:
            challenge.status = OtpChallengeStatus.LOCKED
            raise UnauthorizedError("OTP challenge locked")

    async def _consume_attempt_or_fail(self, challenge: OtpChallenge, code: str) -> None:
        if verify_otp_hmac(str(challenge.id), code, challenge.otp_code_hmac):
            return
        challenge.attempt_count += 1
        if challenge.attempt_count >= challenge.max_attempts:
            challenge.status = OtpChallengeStatus.LOCKED
        await audit.record_event(
            self.session,
            event_type=AuthEvent.OTP_VERIFY_FAILED,
            resource_type="otp_challenge",
            resource_id=str(challenge.id),
            request_id=self.ctx.request_id,
            actor_user_id=challenge.user_id,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"attempt_count": challenge.attempt_count},
        )
        await self.session.commit()
        raise UnauthorizedError("Invalid OTP code")
