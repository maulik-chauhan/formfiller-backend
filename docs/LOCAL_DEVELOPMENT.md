# Running the prototype locally

Two repositories side by side: `backend/` (this repo) and
`frontend/screenshot-to-sight-3914/` (its own git repo). No Docker. Everything below is
development-only: test keys generated on your machine, fictional data.

## Prerequisites

Python 3.12+, PostgreSQL 16, Node 22, and `bun` (or `npx bun`) for the frontend lockfile.

## 1. Database

Any PostgreSQL 16 works. A throwaway cluster:

```bash
export LC_ALL=en_US.UTF-8
initdb -D ~/.govforms-pg16 -U postgres -A trust
```

```bash
pg_ctl -D ~/.govforms-pg16 -o "-p 55432 -c listen_addresses=127.0.0.1" -l ~/.govforms-pg16.log start
```

```bash
psql -h 127.0.0.1 -p 55432 -U postgres -c "CREATE DATABASE gov_forms_dev" -c "CREATE DATABASE gov_forms_test"
```

## 2. Backend

```bash
cd backend && python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"
```

Create `backend/.env` (stable dev keys) and the frontend's `.env.local` (pins this
machine's dev publisher key, points at the dev CDN):

```bash
python -m scripts.make_dev_env --frontend ../frontend/screenshot-to-sight-3914
```

```bash
alembic upgrade head
```

Publish the test-fixture templates through the real maker-checker flow:

```bash
python -m scripts.seed_dev
```

Run the API and, in a second terminal, the CDN stand-in:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
python -m scripts.dev_cdn --port 8001
```

## 3. Frontend

```bash
cd frontend/screenshot-to-sight-3914 && npx bun install --frozen-lockfile
```

```bash
npx vite dev --port 8080 --strictPort
```

Open http://localhost:8080. Sign in with any `+91` mobile starting 6–9; the code is
printed in the API terminal (`DEV OTP …`, console provider, development only).

## 4. Walkthrough

Templates → open a `[TEST FIXTURE]` form → Saved templates (create the vault) → back to
the form → **Prepare template** → Profiles (labels such as "Full name", "Date of birth",
"Address line 1", "City", "State", "PIN code" match the fixture's profile keys) →
My drafts → New draft → fill, review, **Create PDF**, preview, **Export PDF**.

Withdraw a template to see lifecycle handling:

```bash
python -m scripts.seed_dev --deprecate test-fillable-form
```

## Admin console (F5)

Sign in once with each test number you want to use, then give it a role:

```bash
python -m scripts.grant_role +919876550001 TEMPLATE_CURATOR
```

Roles: `TEMPLATE_CURATOR` (maker), `TEMPLATE_VERIFIER` (checker), `TEMPLATE_APPROVER`,
`SUPER_ADMIN`. Sign in again so the session carries the role, then open
http://localhost:8080/admin. Publishing needs three different accounts (maker, checker,
approver). The OTP request limit is 5 per hour per network; for a long manual session
you can add `OTP_REQUEST_MAX_PER_HOUR=100` to your local `backend/.env` and remove it
afterwards.

## 5. Tests

Backend (needs the test database):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/gov_forms_test python -m pytest -q
```

```bash
ruff check . && ruff format --check . && mypy app scripts
```

Frontend:

```bash
npx vitest run && npx tsc --noEmit && npx eslint . && npx vite build
```

## 6. Changing the contract or the API

```bash
python -m scripts.build_contract_fixtures && python -m scripts.export_openapi && python -m scripts.sync_contracts ../frontend/screenshot-to-sight-3914
```

Both test suites fail if `docs/contracts` and the frontend's `contracts/` diverge.

## Notes

- A page reload signs you out (tokens are memory-only by design, DEC-7); the encrypted
  vault and drafts stay on the device.
- `.env`, `.env.local` and `.dev-storage/` are git-ignored. Never reuse their keys
  outside your machine.
