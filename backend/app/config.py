"""Environment-driven application settings.

All commercial parameters (free quota, pricing) and retention windows are
environment-configurable per the v2.1.0 Decision Register — never hardcoded in
``app/core/constants.py``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated runtime configuration loaded from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    # ----- Core -----------------------------------------------------------
    app_name: str = "gov-forms-backend"
    environment: str = Field(default="development")
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # ----- Database -------------------------------------------------------
    # Kept as ``str`` so the SQLAlchemy async driver URL (``+asyncpg``) passes
    # through unaltered; format is validated at engine-creation time.
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/gov_forms",
    )
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    # ----- Cryptography / blind indexes -----------------------------------
    blind_index_hmac_key: str = Field(default="dev-blind-index-key-change-me")
    blind_index_key_version: int = 1
    otp_hmac_key: str = Field(default="dev-otp-hmac-key-change-me")
    ip_subnet_hash_key: str = Field(default="dev-ip-subnet-hash-key-change-me")

    # Access/refresh JWT (user auth) — HS256 by default for dev.
    jwt_secret_key: str = Field(default="dev-jwt-secret-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # Ed25519 lease-signing key (base64, 32-byte seed). Dev-only default empty.
    lease_signing_seed_b64: str = Field(default="")
    lease_signing_key_id: str = "dev-lease-key-1"

    # ----- OTP ------------------------------------------------------------
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 3
    otp_resend_cooldown_seconds: int = 60

    # ----- Commercial (PROVISIONAL — pending product-owner sign-off) ------
    # COM-01 / COM-02 / ASM-02 — configurable, never seeded unconditionally.
    default_free_quota_total: int = 2
    lease_reservation_ttl_hours: int = 48
    lease_correction_window_hours: int = 24

    # ----- Retention windows (ASM-05 — subject to legal review) -----------
    deleted_quota_retention_days: int = 730
    audit_log_retention_days: int = 1095
    audit_ip_hash_retention_days: int = 90
    webhook_event_retention_days: int = 90
    otp_challenge_retention_days: int = 7
    financial_ledger_retention_days: int = 2920

    # ----- Payments (Razorpay) -------------------------------------------
    razorpay_key_id: str = Field(default="")
    razorpay_key_secret: str = Field(default="")
    razorpay_webhook_secret: str = Field(default="")

    # ----- Module gating --------------------------------------------------
    esign_module_enabled: bool = False

    # ----- Privacy boundary payload limits (bytes) ------------------------
    citizen_max_body_bytes: int = 4_096
    admin_upload_max_body_bytes: int = 10_485_760
    webhook_max_body_bytes: int = 65_536

    @field_validator("environment")
    @classmethod
    def _normalise_env(cls, value: str) -> str:
        return value.strip().lower()

    @property
    def is_production(self) -> bool:
        return self.environment in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
