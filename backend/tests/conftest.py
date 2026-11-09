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
        # COM-01 is provisional; tests explicitly configure the quota so the
        # quota/restoration logic can be exercised. Production leaves it unset.
        "DEFAULT_FREE_QUOTA_TOTAL": "2",
        "OTP_DELIVERY_PROVIDER": "mock",
    }
)

_SCHEMA_SQL = (
    pathlib.Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "0001_initial_schema.sql"
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
    """Create the full schema once per test session via sync psycopg2."""
    conn = psycopg2.connect(_SYNC_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            cur.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
        yield
    finally:
        conn.close()


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

    from app.core.constants import ClientPlatform, UserRole
    from app.core.security import create_access_token
    from app.modules.auth.models import AuthSession, User

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
