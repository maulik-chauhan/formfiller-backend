# Development Status — Indian Government Forms Automation Platform (Backend)

Living milestone tracker. Spec baseline: **v2.1.0**.

| Milestone | Title | Status |
| :--- | :--- | :--- |
| M1 | Project Foundation | ✅ Complete |
| M2 | Database Models & Migrations | ✅ Complete |
| **M3** | **Authentication & Session Security** | ✅ **Complete** |
| M4 | Template Management & Maker-Checker | ⬜ Not started |
| M5 | Entitlements, Leases & Offline Lifecycle | ⬜ Not started |
| M6 | Payments, Webhooks & Refund Lifecycle | ⬜ Not started |
| M7 | eSign Gateway (Gated) | 🔒 Disabled until compliance sign-off |

---

## Milestone 1 — Project Foundation ✅
Project scaffold, env-driven settings, canonical enums, crypto helpers, RFC 7807
exception handling, route-aware `PrivacyBoundaryMiddleware`. Quality gates green.

## Milestone 2 — Database Models & Migrations ✅
All 19 SQLAlchemy 2.x async models and the Alembic `0001` migration (CHECK
constraints, partial unique indexes, approved-domain validation triggers,
published-template immutability, append-only audit/ledger triggers). Verified on
PostgreSQL 16; `upgrade head` / `downgrade base` cycle clean.

## Milestone 3 — Authentication & Session Security ✅

**Endpoints (`/api/v1/auth`)**

| Method | Path | Auth |
| :--- | :--- | :--- |
| POST | `/otp/request` | Public (5/hr per mobile & IP) |
| POST | `/otp/verify` | Public (5/min per IP) |
| POST | `/token/refresh` | Refresh token |
| POST | `/logout` | Bearer |
| GET | `/sessions` | Bearer |
| DELETE | `/sessions/{session_id}` | Bearer |
| DELETE | `/account` | Bearer + fresh OTP + confirmation |

**Security features**
- Six-digit `secrets` OTP; stored only as `HMAC-SHA256(otp_secret, "{challenge_id}:{code}")`.
- 5-minute expiry, 3-attempt lockout, 60-second resend cooldown, hourly request
  caps (DB-backed) and per-IP verify cap (in-process limiter).
- Keyed HMAC-SHA256 blind indexing for mobile/email; raw numbers never stored.
  Blind-index, OTP and JWT keys are kept distinct (enforced in production).
- Ed25519 (EdDSA) access tokens — claims limited to `sub`, `sid`, `role`, `iat`,
  `exp`, `iss`; 15-minute expiry; no PII in claims. Signing seed from env only.
- Opaque refresh tokens (`secrets.token_urlsafe(32)`), stored as SHA-256 hashes,
  rotated with `token_family_id` lineage. Reuse of a rotated token revokes the
  whole family and records `SECURITY_TOKEN_REUSE_DETECTED`. Rotation runs under
  `SELECT … FOR UPDATE`.
- Dependencies/RBAC (`get_current_user`, `get_current_session`,
  `require_authenticated_user`, `require_roles`) re-validate live account/session
  state — a suspended account or revoked session is rejected despite a valid JWT.
- Account deletion: fresh OTP + `DELETE_MY_ACCOUNT` confirmation → revokes all
  sessions, anonymises account metadata, writes a `deleted_account_quotas`
  tombstone (blind index + consumed quota) with configurable retention, and
  preserves the financial/entitlement ledger. Re-registration restores consumed
  free quota (anti-farming).
- Audit metadata is allowlisted; OTP codes, raw numbers and tokens are never logged.

**Commercial note (COM-01):** the 2-form lifetime free quota is **provisional**.
`DEFAULT_FREE_QUOTA_TOTAL` is configurable and defaults to **unset → 0 credits**;
tests configure it explicitly. No price or quota is hardcoded.

**Database changes:** none. No schema change was required; all auth behaviour uses
the existing M2 tables. Alembic `upgrade head` / `downgrade base` remain clean.

**New environment variables:** `JWT_ISSUER`, `ACCESS_JWT_ED25519_SEED_B64`,
`ACCESS_JWT_KID`, `OTP_REQUEST_MAX_PER_HOUR`, `OTP_VERIFY_MAX_PER_MINUTE_PER_IP`,
`OTP_DELIVERY_PROVIDER`, `SMS_PROVIDER_API_KEY/SECRET/SENDER_ID`. `DEFAULT_FREE_QUOTA_TOTAL`
is now optional (unset = 0 free credits). See `.env.example`.

**Quality gates (executed):** `ruff check .` ✅ · `ruff format --check .` ✅ ·
`mypy --strict app` ✅ (42 files) · `pytest` ✅ **68 passed** (unit + PostgreSQL
integration + security).

**Known limitations / pending production integrations**
- `ProductionSmsProvider.send_otp` is an interface stub — a concrete licensed
  Indian SMS gateway integration is pending.
- The per-IP verify limiter is in-process; a multi-instance deployment should
  back it with a shared store (e.g. Redis).
- Retention windows and the anti-abuse tombstone are technical privacy-by-design
  controls, **subject to formal legal review** (no DPDP compliance is claimed).

## Milestones 4–7 — Not started
Out of scope for this pass. eSign remains disabled (`503`) until M7 sign-off.
