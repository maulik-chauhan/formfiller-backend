# Indian Government Forms Automation Platform — Backend

Zero-Knowledge Split-Trust backend (Python 3.12 · FastAPI · Pydantic v2 ·
SQLAlchemy 2.x async · PostgreSQL 16 · Alembic), implementing architecture spec
**v2.1.0**. Implemented through M6 (auth, templates with maker-checker, entitlements,
payments) plus the F3 template-verification contract (publisher key discovery and
rotation, v2 signatures, lifecycle status, CORS) and the F4 form-manifest contract
gate. eSign (M7) is disabled.

Local setup, seeding and the full-stack walkthrough: `../docs/LOCAL_DEVELOPMENT.md`.
Shared contract (manifest spec, JSON Schema, fixtures, OpenAPI): `../docs/contracts/`.
Open decisions: `../docs/DECISION_REGISTER.md`.

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
mypy app scripts
TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/gov_forms_test pytest
python -m scripts.build_contract_fixtures --check
python -m scripts.export_openapi --check
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
- **Form-manifest contract v1** — an uploaded manifest must validate against
  `docs/contracts/form-manifest-v1.md`, match the template identity, and map only to
  fields/pages that exist in the uploaded PDF; published manifests are immutable.
- **Publisher signatures v2** — domain-separated, length-prefixed digest binding the
  full template identity; legacy v1 rows still verify; key rotation by `kid`.
- **Pinned API surface** — `tests/security/test_privacy_surface.py` fails if a route,
  upload path or column that could hold applicant data is added.
- **Commercial parameters are provisional/configurable** — free quota and the
  ₹20/₹50/₹30 tiers live in `pricing_catalog` + env `Settings`, never in
  `constants.py`.

> Regulatory/retention sections are technical privacy-by-design controls, subject
> to formal legal-counsel review before production launch.
