"""Auth & identity models (Tables 1-4): users, otp_challenges,
auth_sessions, deleted_account_quotas."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import (
    AccountStatus,
    ClientPlatform,
    OtpChallengeStatus,
    OtpChannel,
    OtpPurpose,
    UserRole,
)
from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mobile_blind_index: Mapped[str | None] = mapped_column(CHAR(64), unique=True)
    email_blind_index: Mapped[str | None] = mapped_column(CHAR(64), unique=True)
    blind_index_key_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    role: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default=text(f"'{UserRole.CITIZEN}'")
    )
    account_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{AccountStatus.ACTIVE}'")
    )
    is_mobile_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    is_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    mobile_verified_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_login_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    deleted_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    target_blind_index: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    otp_code_hmac: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    attempt_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{OtpChallengeStatus.PENDING}'")
    )
    client_ip_subnet_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_otp_challenges_rate_limit", "target_blind_index", text("created_at DESC")),
        Index("idx_otp_challenges_ip_rate_limit", "client_ip_subnet_hash", text("created_at DESC")),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    refresh_token_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False, unique=True)
    replaced_by_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="SET NULL")
    )
    device_fingerprint_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    revocation_reason: Mapped[str | None] = mapped_column(String(28))
    expires_at: Mapped[dt.datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    revoked_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    last_used_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_auth_sessions_user_active",
            "user_id",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("idx_auth_sessions_family", "token_family_id"),
    )


class DeletedAccountQuota(Base):
    __tablename__ = "deleted_account_quotas"

    mobile_blind_index: Mapped[str] = mapped_column(CHAR(64), primary_key=True)
    blind_index_key_version: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("1")
    )
    free_quota_consumed_total: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    deleted_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[dt.datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (Index("idx_deleted_account_quotas_expires_at", "expires_at"),)


# Re-export enums for convenience at the module level.
__all__ = [
    "AccountStatus",
    "AuthSession",
    "ClientPlatform",
    "DeletedAccountQuota",
    "OtpChallenge",
    "OtpChallengeStatus",
    "OtpChannel",
    "OtpPurpose",
    "User",
    "UserRole",
]
