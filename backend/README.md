# Indian Government Forms Automation Platform — Backend

Zero-Knowledge Split-Trust backend (Python 3.12 · FastAPI · Pydantic v2 ·
SQLAlchemy 2.x async · PostgreSQL 16 · Alembic), implementing architecture spec
**v2.1.0**. This is the Milestone-1 foundation: full configuration, all canonical
enums, all **19** ORM models, the complete DDL migration (CHECK constraints,
validation functions, append-only / immutability triggers), the route-aware
`PrivacyBoundaryMiddleware`, and the API surface with request/response schemas.
Business-logic service layers are stubbed (`501`) behind a stable contract.

## Layout

```
app/
  config.py                 Env-driven Settings (commercial params & retention windows configurable)
  core/
    constants.py            All canonical StrEnums + structural constants (no rupee prices/quotas)
    hashing.py              Blind indexes, OTP HMAC, IP-subnet hashing
    security.py             Access/refresh JWT helpers
    ledger.py               Tamper-evident ledger hash-chain
    errors.py               RFC 7807 problem+json handlers
    schemas.py              StrictModel (extra="forbid") base
  db/
    base.py  session.py  models.py   Declarative base, async engine, model aggregator
  middleware/
    privacy_boundary.py     3-profile route-aware payload gate
  modules/
    auth/  templates/  entitlements/  payments/  esign_gateway/  audit/
alembic/
  env.py  versions/0001_initial_schema.{py,sql}
tests/
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # then edit secrets

# Create schema
alembic upgrade head

# Run the API
uvicorn app.main:app --reload
# → http://localhost:8000/docs   ·   GET /health

# Run the expired-lease reaper (Milestone 5)
python -m app.workers.reaper --once          # single sweep (use from cron / CronJob)
python -m app.workers.reaper --interval 300  # loop every 5 min (dev convenience)
```

The reaper is idempotent and safe to run as several concurrent replicas; in
production run it as a dedicated worker or a scheduled `--once` job rather than an
in-process timer.

## Quality gates

```bash
ruff check .
ruff format --check .
mypy app
pytest
```

## Design guardrails baked in

- **19 canonical tables** — see `app/db/models.py::CANONICAL_TABLES`.
- **`users.is_mobile_verified` defaults `FALSE`** — only an OTP verify flips it.
- **Approved-domain enforcement** on both `government_sources.official_portal_url`
  and `template_versions.source_pdf_download_url` via DB triggers.
- **Untrusted `completed_at_client_iso`** — stored as diagnostic metadata only;
  never used to grant credit or extend a correction window.
- **Route-aware privacy boundary** — 4 KB JSON citizen cap, 10 MB authenticated
  admin multipart upload, 64 KB signed webhook.
- **Commercial parameters are provisional/configurable** — free quota and the
  ₹20/₹50/₹30 tiers live in `pricing_catalog` + env `Settings`, never in
  `constants.py`.

> Regulatory/retention sections are technical privacy-by-design controls, subject
> to formal legal-counsel review before production launch.
