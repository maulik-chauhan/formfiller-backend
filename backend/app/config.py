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

    # ----- Access JWT (EdDSA / Ed25519) -----------------------------------
    # Access tokens are Ed25519-signed (EdDSA). The signing key is a base64
    # 32-byte seed kept OUT of git; when empty in a non-production environment
    # an ephemeral keypair is generated per process (see app/core/security.py).
    jwt_issuer: str = Field(default="https://api.govforms.in")
    access_jwt_ed25519_seed_b64: str = Field(default="")
    access_jwt_kid: str = Field(default="ed25519-access-2026-v1")
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 30

    # Ed25519 Entitlement-Lease-Token (ELT) signing key (base64, 32-byte seed).
    # Empty => ephemeral dev key per process. The previous key (optional) keeps
    # already-issued ELTs verifiable across a key rotation.
    lease_signing_seed_b64: str = Field(default="")
    lease_signing_key_id: str = Field(default="ed25519-elt-2026-v1")
    lease_signing_prev_seed_b64: str = Field(default="")
    lease_signing_prev_key_id: str = Field(default="")

    # ----- Template publisher signing (EdDSA / Ed25519) -------------------
    # Signs published template bundles. Base64 32-byte seed; empty => ephemeral
    # dev key per process. Kept distinct from the access-token key.
    template_signing_ed25519_seed_b64: str = Field(default="")
    template_signing_key_id: str = Field(default="ed25519-template-2026-v1")

    # ----- Template blank-PDF storage ------------------------------------
    template_storage_dir: str = Field(default="/tmp/govforms_templates")
    template_cdn_base_url: str = Field(default="https://cdn.govforms.in")

    # ----- OTP ------------------------------------------------------------
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 3
    otp_resend_cooldown_seconds: int = 60
    # Rate limits (ASM-05): OTP request 5/hr per mobile & per IP subnet;
    # OTP verify 5/min per IP subnet.
    otp_request_max_per_hour: int = 5
    otp_verify_max_per_minute_per_ip: int = 5
    # OTP delivery: "mock" (in-memory, tests) | "console" (dev logs) | "sms" (prod).
    otp_delivery_provider: str = Field(default="mock")
    sms_provider_api_key: str = Field(default="")
    sms_provider_api_secret: str = Field(default="")
    sms_provider_sender_id: str = Field(default="")

    # ----- Commercial (PROVISIONAL — pending product-owner sign-off) ------
    # COM-01: lifetime free quota is NOT finally approved. Left unset (None) it
    # grants ZERO free credits; set DEFAULT_FREE_QUOTA_TOTAL only once approved.
    # Never hardcode a value here or in app/core/constants.py.
    default_free_quota_total: int | None = None
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

    @property
    def resolved_free_quota_total(self) -> int:
        """Free quota to grant a new wallet.

        COM-01 is unapproved: when the quota is not explicitly configured the
        platform initialises wallets safely with ZERO free credits rather than
        assuming the provisional two-form allowance.
        """
        return self.default_free_quota_total or 0

    def validate_production_secrets(self) -> None:
        """Fail fast at startup if production is missing mandatory secrets.

        Blind-index, OTP, IP-subnet and access-token signing keys must each be
        set to a real, non-default value in production, and the three key classes
        must be distinct so compromise of one does not expose the others.
        """
        if not self.is_production:
            return
        defaults = {
            "blind_index_hmac_key": "dev-blind-index-key-change-me",
            "otp_hmac_key": "dev-otp-hmac-key-change-me",
            "ip_subnet_hash_key": "dev-ip-subnet-hash-key-change-me",
        }
        missing = [name for name, dev in defaults.items() if getattr(self, name) in {"", dev}]
        if not self.access_jwt_ed25519_seed_b64:
            missing.append("access_jwt_ed25519_seed_b64")
        if missing:
            raise RuntimeError(f"Missing/insecure production secrets: {', '.join(sorted(missing))}")
        key_classes = {
            self.blind_index_hmac_key,
            self.otp_hmac_key,
            self.ip_subnet_hash_key,
            self.access_jwt_ed25519_seed_b64,
        }
        if len(key_classes) < 4:
            raise RuntimeError("Blind-index, OTP, IP-subnet and JWT keys must all be distinct")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
