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
    # Previous publisher key (rotation): keeps already-published signatures
    # verifiable. Set BOTH or neither. Remove it to retire/revoke the old key.
    template_signing_prev_seed_b64: str = Field(default="")
    template_signing_prev_key_id: str = Field(default="")

    # ----- Template blank-PDF storage ------------------------------------
    # Provider-neutral port (see app/modules/templates/storage.py):
    #   "local"       filesystem + static CDN base URL, non-expiring URLs
    #   "mock_signed" filesystem + HMAC-signed expiring URLs (tests only)
    # A production object-store/CDN adapter is pending DEC-3.
    template_storage_provider: str = Field(default="local")
    template_storage_dir: str = Field(default="/tmp/govforms_templates")
    template_cdn_base_url: str = Field(default="https://cdn.govforms.in")
    template_download_url_ttl_seconds: int = 900
    template_mock_url_signing_key: str = Field(default="dev-mock-url-signing-key")

    # ----- CORS -----------------------------------------------------------
    # Comma-separated exact origins (scheme://host[:port]). Empty => no CORS
    # headers at all. Credentials are never allowed (bearer-token model).
    cors_allowed_origins: str = Field(default="")

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
    razorpay_environment: str = Field(default="test")  # "test" | "live"
    razorpay_api_base: str = Field(default="https://api.razorpay.com/v1")
    # Gateway network mode: "fake" (in-memory, dev/tests) | "http" (real API) |
    # "auto" (http in production with creds, fake otherwise).
    razorpay_gateway_mode: str = Field(default="auto")
    razorpay_http_timeout_seconds: float = 15.0
    # DB fixes payment_orders.quantity = 1; this bounds the accepted value.
    payment_max_quantity: int = 1

    @property
    def razorpay_use_fake_gateway(self) -> bool:
        mode = self.razorpay_gateway_mode.lower()
        if mode == "fake":
            return True
        if mode == "http":
            return False
        # auto
        return not (self.is_production and self.razorpay_key_id and self.razorpay_key_secret)

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
    def is_shared_environment(self) -> bool:
        """Production or staging: long-lived, multi-instance, real clients."""
        return self.environment in {"production", "prod", "staging", "stage"}

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def resolved_free_quota_total(self) -> int:
        """Free quota to grant a new wallet.

        COM-01 is unapproved: when the quota is not explicitly configured the
        platform initialises wallets safely with ZERO free credits rather than
        assuming the provisional two-form allowance.
        """
        return self.default_free_quota_total or 0

    def validate_production_secrets(self) -> None:
        """Fail fast at startup if a shared environment is missing mandatory secrets.

        In production **and staging**: the blind-index, OTP and IP-subnet HMAC keys
        and all three Ed25519 seeds (access-token, entitlement-lease, template
        publisher) must be set to real values, every seed must decode to 32 bytes,
        and all six secrets must be mutually distinct so compromise of one does not
        expose another. A stable seed is mandatory: a per-process ephemeral key
        would invalidate every token/lease/template signature on restart and
        differ between instances. Production additionally rejects a CORS wildcard.
        """
        if not self.is_shared_environment:
            return
        defaults = {
            "blind_index_hmac_key": "dev-blind-index-key-change-me",
            "otp_hmac_key": "dev-otp-hmac-key-change-me",
            "ip_subnet_hash_key": "dev-ip-subnet-hash-key-change-me",
        }
        missing = [name for name, dev in defaults.items() if getattr(self, name) in {"", dev}]
        seeds = {
            "access_jwt_ed25519_seed_b64": self.access_jwt_ed25519_seed_b64,
            "lease_signing_seed_b64": self.lease_signing_seed_b64,
            "template_signing_ed25519_seed_b64": self.template_signing_ed25519_seed_b64,
        }
        missing.extend(name for name, value in seeds.items() if not value)
        if missing:
            raise RuntimeError(f"Missing/insecure production secrets: {', '.join(sorted(missing))}")
        import base64

        for name, value in seeds.items():
            try:
                decoded = base64.b64decode(value, validate=True)
            except ValueError as exc:
                raise RuntimeError(f"{name} must be valid base64") from exc
            if len(decoded) != 32:
                raise RuntimeError(f"{name} must decode to exactly 32 bytes")
        key_classes = {
            self.blind_index_hmac_key,
            self.otp_hmac_key,
            self.ip_subnet_hash_key,
            *seeds.values(),
        }
        if len(key_classes) < 6:
            raise RuntimeError(
                "Blind-index, OTP, IP-subnet keys and the access-token, lease and "
                "template-publisher seeds must all be distinct"
            )
        if self.template_signing_prev_seed_b64 and (
            self.template_signing_prev_seed_b64 in key_classes
        ):
            raise RuntimeError("Previous publisher seed must differ from every active secret")
        if self.is_production:
            origins = self.cors_allowed_origins_list
            if any("*" in origin for origin in origins):
                raise RuntimeError("CORS_ALLOWED_ORIGINS must not contain a wildcard in production")
            if any(not origin.startswith("https://") for origin in origins):
                raise RuntimeError("CORS_ALLOWED_ORIGINS must be https:// origins in production")
            if self.template_storage_provider != "local":
                raise RuntimeError(
                    "TEMPLATE_STORAGE_PROVIDER must be a production adapter (mock is test-only)"
                )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance."""
    return Settings()
