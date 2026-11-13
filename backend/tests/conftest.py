"""Shared test fixtures.

Security and concurrency behaviour is validated against a real PostgreSQL
instance (never SQLite) so that CHECK constraints, triggers, ``FOR UPDATE`` row
locks and the blind-index/append-only guarantees are exercised exactly as in
production. The async database URL is taken from ``TEST_DATABASE_URL``.

Environment is configured BEFORE any application import so the cached settings
and the module-level engine pick up the test configuration.
"""

from __future__ import annotations

import base64
import os
import pathlib
from collections.abc import AsyncGenerator, Iterator

import psycopg2
import pytest
import pytest_asyncio

# --------------------------------------------------------------------------- #
# Configure environment before importing the application.
# --------------------------------------------------------------------------- #
_ASYNC_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres@127.0.0.1:55432/gov_forms_test",
)
_SYNC_URL = _ASYNC_URL.replace("+asyncpg", "")

_TEST_SEED_B64 = base64.b64encode(bytes(range(32))).decode()
_TEST_TEMPLATE_SEED_B64 = base64.b64encode(bytes(range(32, 64))).decode()
_TEST_ELT_SEED_B64 = base64.b64encode(bytes(range(64, 96))).decode()

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DATABASE_URL": _ASYNC_URL,
        "BLIND_INDEX_HMAC_KEY": "test-blind-index-key",
        "OTP_HMAC_KEY": "test-otp-hmac-key",
        "IP_SUBNET_HASH_KEY": "test-ip-subnet-key",
        "ACCESS_JWT_ED25519_SEED_B64": _TEST_SEED_B64,
        "TEMPLATE_SIGNING_ED25519_SEED_B64": _TEST_TEMPLATE_SEED_B64,
        "TEMPLATE_STORAGE_DIR": "/tmp/govforms_templates_test",
        "LEASE_SIGNING_SEED_B64": _TEST_ELT_SEED_B64,
        "LEASE_SIGNING_KEY_ID": "ed25519-elt-2026-v1",
        "RAZORPAY_KEY_ID": "rzp_test_key_id",
        "RAZORPAY_KEY_SECRET": "rzp_test_key_secret",
        "RAZORPAY_WEBHOOK_SECRET": "rzp_test_webhook_secret",
        "RAZORPAY_GATEWAY_MODE": "fake",
        # COM-01 is provisional; tests explicitly configure the quota so the
        # quota/restoration logic can be exercised. Production leaves it unset.
        "DEFAULT_FREE_QUOTA_TOTAL": "2",
        "OTP_DELIVERY_PROVIDER": "mock",
        # Hermetic: never inherit a developer's local backend/.env.
        "DEBUG": "false",
        "TEMPLATE_SIGNING_KEY_ID": "ed25519-template-2026-v1",
        "TEMPLATE_SIGNING_PREV_SEED_B64": "",
        "TEMPLATE_SIGNING_PREV_KEY_ID": "",
        "TEMPLATE_STORAGE_PROVIDER": "local",
        "TEMPLATE_CDN_BASE_URL": "https://cdn.govforms.in",
        "CORS_ALLOWED_ORIGINS": "",
        "LEASE_SIGNING_PREV_SEED_B64": "",
        "LEASE_SIGNING_PREV_KEY_ID": "",
        # Limits and lifetimes the tests assert on; a local .env may tune them.
        "ACCESS_TOKEN_TTL_MINUTES": "15",
        "REFRESH_TOKEN_TTL_DAYS": "30",
        "OTP_TTL_SECONDS": "300",
        "OTP_MAX_ATTEMPTS": "3",
        "OTP_RESEND_COOLDOWN_SECONDS": "60",
        "OTP_REQUEST_MAX_PER_HOUR": "5",
        "OTP_VERIFY_MAX_PER_MINUTE_PER_IP": "5",
        "LEASE_RESERVATION_TTL_HOURS": "48",
        "LEASE_CORRECTION_WINDOW_HOURS": "24",
        "ESIGN_MODULE_ENABLED": "false",
        "PAYMENT_MAX_QUANTITY": "1",
        "RAZORPAY_ENVIRONMENT": "test",
    }
)

_ALL_TABLES = (
    "users",
    "otp_challenges",
    "auth_sessions",
    "deleted_account_quotas",
    "approved_source_domains",
    "government_sources",
    "government_source_checks",
    "form_templates",
    "template_versions",
    "template_approval_logs",
    "pricing_catalog",
    "payment_orders",
    "payment_refunds",
    "payment_webhook_events",
    "entitlement_wallets",
    "entitlement_leases",
    "entitlement_ledger_entries",
    "esign_hash_sessions",
    "audit_logs",
)


@pytest.fixture(scope="session", autouse=True)
def _pg_schema() -> Iterator[None]:
    """Apply the full Alembic migration chain (0001 + 0002…) once per session."""
    import subprocess

    conn = psycopg2.connect(_SYNC_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        conn.close()
    backend_root = pathlib.Path(__file__).resolve().parent.parent
    subprocess.run(["alembic", "upgrade", "head"], cwd=backend_root, check=True, env={**os.environ})
    yield


@pytest.fixture(autouse=True)
def _truncate() -> Iterator[None]:
    """Truncate all tables before each test for isolation (sync, no event loop)."""
    conn = psycopg2.connect(_SYNC_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE {', '.join(_ALL_TABLES)} RESTART IDENTITY CASCADE;")
    finally:
        conn.close()
    yield


@pytest.fixture(autouse=True)
def _reset_runtime_state() -> Iterator[None]:
    """Reset in-memory rate limiter and mock OTP store between tests."""
    from app.core.rate_limit import auth_rate_limiter
    from app.modules.auth.sms_provider import get_mock_provider

    auth_rate_limiter.reset()
    get_mock_provider().clear()
    yield
    auth_rate_limiter.reset()
    get_mock_provider().clear()


@pytest_asyncio.fixture
async def db_engine():  # type: ignore[no-untyped-def]
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(_ASYNC_URL, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):  # type: ignore[no-untyped-def]
    from sqlalchemy.ext.asyncio import async_sessionmaker

    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def make_principal(db_session):  # type: ignore[no-untyped-def]
    """Factory creating a persisted user + active session, returning (id, token)."""
    import datetime as dt
    import uuid

    from app.config import get_settings
    from app.core.constants import ClientPlatform, UserRole
    from app.core.security import create_access_token
    from app.modules.auth.models import AuthSession, User
    from app.modules.entitlements.models import EntitlementWallet

    counter = {"n": 0}

    async def _make(role: UserRole = UserRole.CITIZEN) -> tuple[uuid.UUID, str]:
        counter["n"] += 1
        n = counter["n"]
        now = dt.datetime.now(tz=dt.UTC)
        uid = uuid.uuid4()
        sid = uuid.uuid4()
        db_session.add(
            User(
                id=uid,
                mobile_blind_index=f"{n:064x}",
                blind_index_key_version=1,
                role=role,
                account_status="ACTIVE",
                is_mobile_verified=True,
                mobile_verified_at=now,
                last_login_at=now,
            )
        )
        await db_session.flush()  # ensure the user row exists before its session
        db_session.add(
            EntitlementWallet(
                user_id=uid,
                free_quota_total=get_settings().resolved_free_quota_total,
                free_quota_used=0,
                free_quota_reserved=0,
            )
        )
        db_session.add(
            AuthSession(
                id=sid,
                user_id=uid,
                token_family_id=uuid.uuid4(),
                refresh_token_sha256=f"{(n + 9_000_000):064x}",
                device_fingerprint_hash="a" * 64,
                platform=ClientPlatform.WEB,
                expires_at=now + dt.timedelta(days=1),
                last_used_at=now,
            )
        )
        await db_session.commit()
        token = create_access_token(user_id=uid, session_id=sid, role=role)
        return uid, token

    return _make


@pytest_asyncio.fixture
async def pricing(db_session):  # type: ignore[no-untyped-def]
    """Seed an active pricing catalog (test-controlled; production stays unseeded)."""
    from app.modules.payments.models import PricingCatalog

    rows: list[dict[str, object]] = [
        # STD_FORM_20: GST exclusive → tax = 2360-2000 = 360.
        {
            "sku_code": "STD_FORM_20",
            "display_name": "Standard Form",
            "credit_bucket": "STANDARD_20",
            "base_amount_paise": 2000,
            "esp_surcharge_paise": 0,
            "gst_rate_bps": 1800,
            "is_gst_inclusive": False,
            "total_payable_paise": 2360,
        },
        # SIG_IMAGE_50: inclusive with a surcharge component.
        {
            "sku_code": "SIG_IMAGE_50",
            "display_name": "Signature Bundle",
            "credit_bucket": "SIGNATURE_50",
            "base_amount_paise": 5000,
            "esp_surcharge_paise": 500,
            "gst_rate_bps": 1800,
            "is_gst_inclusive": True,
            "total_payable_paise": 5500,
        },
        {
            "sku_code": "SIG_UPGRADE_30",
            "display_name": "Signature Upgrade",
            "credit_bucket": "SIGNATURE_UPGRADE_30",
            "base_amount_paise": 3000,
            "esp_surcharge_paise": 0,
            "gst_rate_bps": 1800,
            "is_gst_inclusive": True,
            "total_payable_paise": 3000,
        },
        # eSign SKU present but INACTIVE and gated during M6.
        {
            "sku_code": "ESIGN_OTP_CDAC",
            "display_name": "eSign",
            "credit_bucket": "ESIGN",
            "base_amount_paise": 10000,
            "esp_surcharge_paise": 0,
            "gst_rate_bps": 1800,
            "is_gst_inclusive": True,
            "total_payable_paise": 10000,
            "is_active": False,
        },
    ]
    for r in rows:
        db_session.add(PricingCatalog(**r))
    await db_session.commit()
    return {r["sku_code"]: r for r in rows}


@pytest_asyncio.fixture
async def published_template_version(db_session):  # type: ignore[no-untyped-def]
    """Insert a PUBLISHED template version directly and return its UUID."""
    import datetime as dt
    import uuid

    from app.modules.auth.models import User
    from app.modules.templates.models import (
        ApprovedSourceDomain,
        FormTemplate,
        GovernmentSource,
        TemplateVersion,
    )

    now = dt.datetime.now(tz=dt.UTC)
    admins = [uuid.uuid4() for _ in range(3)]
    for i, aid in enumerate(admins):
        db_session.add(
            User(
                id=aid,
                mobile_blind_index=f"{(5_000_000 + i):064x}",
                role="SUPER_ADMIN",
                account_status="ACTIVE",
                is_mobile_verified=True,
                mobile_verified_at=now,
            )
        )
    db_session.add(
        ApprovedSourceDomain(domain_pattern="gov.in", domain_type="GOV_TLD_SUFFIX", rationale="c")
    )
    await db_session.flush()
    source_id = uuid.uuid4()
    db_session.add(
        GovernmentSource(
            id=source_id,
            source_code="MORD",
            jurisdiction="CENTRAL_INDIA",
            ministry_or_department="MoRD",
            official_portal_url="https://rural.gov.in",
            matched_domain_pattern="gov.in",
            source_type="OFFICIAL_GOV_PORTAL",
        )
    )
    await db_session.flush()
    template_id = uuid.uuid4()
    db_session.add(
        FormTemplate(
            id=template_id,
            government_source_id=source_id,
            slug="ror-7-12",
            jurisdiction="CENTRAL_INDIA",
            official_form_code="RTC-7/12",
            category="REVENUE",
            localized_titles={"en": "RoR"},
            localized_descriptions={"en": "RoR"},
        )
    )
    await db_session.flush()
    version_id = uuid.uuid4()
    db_session.add(
        TemplateVersion(
            id=version_id,
            template_id=template_id,
            semver="1.0.0",
            status="PUBLISHED",
            source_pdf_download_url="https://rural.gov.in/forms/ror.pdf",
            source_url_matched_domain="gov.in",
            source_downloaded_at=now,
            blank_pdf_object_key="blank_pdfs/x.pdf",
            blank_pdf_sha256="d" * 64,
            blank_pdf_size_bytes=100,
            page_count=1,
            field_mapping_manifest={},
            manifest_sha256="e" * 64,
            bundle_sha256="f" * 64,
            publisher_ed25519_sig="sig",
            signing_key_id="kid",
            created_by_admin_id=admins[0],
            verified_by_admin_id=admins[1],
            approved_by_admin_id=admins[2],
            published_at=now,
        )
    )
    await db_session.commit()
    return version_id


@pytest_asyncio.fixture
async def client(db_engine) -> AsyncGenerator[object, None]:
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db.session import get_session
    from app.main import create_app

    maker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _override_session() -> AsyncGenerator[object, None]:
        async with maker() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client
    app.dependency_overrides.clear()
