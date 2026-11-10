# Architecture & Integration Audit (pre-implementation)

Date: 2026-11-10. Scope: `backend/` (this repo, branch `feat/payments-refunds`) and
`frontend/screenshot-to-sight-3914/` (separate git repo, Lovable-connected, `main`).
Findings come from reading the source and running both suites, not from earlier reports.

## Baseline (measured before any change)

| Repo | Command | Result |
|---|---|---|
| backend | `pytest -q` on PostgreSQL 16.14 | 162 passed |
| backend | `ruff check .`, `ruff format --check .`, `mypy app` | clean |
| frontend | `vitest run` | 229 passed (17 files) |
| frontend | `tsc --noEmit`, `eslint .` | clean (7 pre-existing warnings) |

## 1. What works

- Backend M1–M6: auth (OTP, EdDSA access JWT, rotating refresh with family revocation,
  sessions, account deletion), template maker-checker with v1 publisher signing,
  entitlements/leases, Razorpay payments/refunds, route-aware privacy middleware.
- Frontend F1–F3 + F4 part 1: API client (timeouts, 4 KB cap, no body logging,
  `credentials: "omit"`), in-memory auth session with single-flight refresh, catalogue,
  v1/v2 signature verification against build-pinned keys, Argon2id/PBKDF2 vault with
  AES-256-GCM + record AAD, encrypted template cache, saved templates, encrypted
  profiles and drafts (opaque key→value answers).

## 2. What is incomplete

- No form-manifest contract, form renderer, validation engine, PDF filling, preview
  or export in either repo. Drafts can be created but not opened for filling.
- Drafts created in the UI store `templateId: null` / `templateVersionId: null`.
- No dev/E2E environment: no seed data, no way to serve blank PDFs to a browser.

## 3. What is broken (integration blockers)

**B-1 (critical). The F3 backend remediation described in
`Claude outputs/F3_*.md` is not in the backend source.** No branch contains it. The
backend still has the 162-test baseline the report calls "pre-remediation". Missing:

| Frontend expects (and `openapi-1.json` documents) | Backend actually has |
|---|---|
| `GET /templates/publisher-keys` | route absent (matched by `/{slug}` → 404) |
| `GET /templates/{slug}/versions/{semver}/status` | route absent → 404 |
| bundle: `template_id`, `jurisdiction`, `category`, `official_form_code`, `signature_format_version`, `signature_alg`, `blank_pdf_download_url_expires` | absent → frontend zod rejects every bundle (`invalid_bundle`) |
| detail: `template_id` | absent → **every template detail page fails** (`invalid_response`) |
| v2 domain-separated signature | only v1 |
| CORS for the browser origin | no `CORSMiddleware` → every browser call fails |
| migration `0003`, key rotation, storage expiry flag, OpenAPI `BearerAuth` | absent |

Net effect: against the real backend the frontend could sign in only if served
same-origin, and could not open a single template.

**B-2.** The frontend CDN pin accepts only `https://<dns-host>/blank_pdfs/<sha>.pdf`.
There is no approved way to download a test PDF in local development.

## 4. Duplication

- `Claude outputs/*.md` are duplicated byte-for-byte in `frontend/.../docs/backend/`.
- Three near-identical `architecture_review_and_final_backend_spec*.md` at repo root.
- `openapi.json` (pre-F3) and `openapi-1.json` (post-F3 target) both present.
  None of these are modified here; they are untracked user files.

## 5. Cross-repository inconsistencies

- Contract docs/OpenAPI (post-F3) vs backend code (pre-F3) — see B-1.
- Frontend canonical JSON supports safe integers only; backend accepted any JSON
  (floats would hash differently across languages). Resolved by making the manifest
  contract integer-only and rejecting floats server-side.
- Frontend reads `Retry-After` on 429, which a cross-origin browser cannot see unless
  the backend exposes it via CORS.

## 6. Security requirements already satisfied

Token storage memory-only; refresh single-flight; no applicant data models on the
backend; citizen routes JSON-only with 4 KB cap and binary-marker scan; webhook raw-body
HMAC; vault KDF/AEAD parameters per checklist; pinned-key-only trust; fail-closed
download gate when no pin/CDN host is configured.

## 7. Decisions requiring external approval

DEC-1 production publisher key, DEC-2 compromise-recovery policy, DEC-3 storage/CDN
provider, production CORS origins, stable shared-environment secrets, SMS provider,
pricing/free quota (COM-01), eSign (M7). See `docs/DECISION_REGISTER.md`.

## 8. Prioritised plan

1. Re-implement the F3 backend remediation to the documented contract (B-1).
2. Dev-only CDN stand-in and loopback download origin, rejected outside development (B-2).
3. Shared, versioned form-manifest contract + fixtures; validators in both repos.
4. Frontend form engine, profile reuse, PDF fill, preview, export.
5. Seed script + live browser end-to-end run; privacy inspection of network traffic.
6. Docs, decision register, deployment checklist.
