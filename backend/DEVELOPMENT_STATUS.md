# Development Status — Indian Government Forms Automation Platform (Backend)

Living milestone tracker. Spec baseline: **v2.1.0**.

| Milestone | Title | Status |
| :--- | :--- | :--- |
| M1 | Project Foundation | ✅ Complete |
| M2 | Database Models & Migrations | ✅ Complete |
| **M3** | **Authentication & Session Security** | ✅ **Complete** |
| **M4** | **Template Management & Maker-Checker** | ✅ **Complete** |
| **M5** | **Entitlements, Leases & Offline Lifecycle** | ✅ **Complete** |
| **M6** | **Payments, Webhooks & Refund Lifecycle** | ✅ **Complete** |
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

## Milestone 4 — Template Management & Maker-Checker ✅

**Endpoints**

Public / citizen read (PUBLISHED only):
`GET /api/v1/templates/sources`, `GET /api/v1/templates`, `GET /api/v1/templates/{slug}`,
`GET /api/v1/templates/{slug}/versions/{semver}/bundle` (Bearer).

Admin:
`POST /api/v1/admin/domains` (SUPER_ADMIN), `POST /api/v1/admin/sources`,
`POST /api/v1/admin/sources/{source_id}/checks`, `POST /api/v1/admin/templates`,
`POST /api/v1/admin/templates/{template_id}/versions` (multipart),
`POST /api/v1/admin/templates/{template_id}/versions/{version_id}/transitions`
(TEMPLATE_CURATOR unless noted; SUPER_ADMIN superset).

**Maker-Checker lifecycle**
`DRAFT → IN_REVIEW → VERIFIED → APPROVED → PUBLISHED → DEPRECATED`, plus rework
paths back to DRAFT. Permitted transitions and roles are a fixed table; the
creator cannot verify, and the approver must differ from both creator and
verifier (enforced in the service AND by the DB `chk_maker_checker_separation`
constraint). Each transition validates `expected_bundle_sha256`, records a
`template_approval_logs` row (append-only), and is audited. Publication signs
`bundle_sha256` with Ed25519 and sets the template's current-published pointer.
Published versions are immutable (DB trigger verified directly).

**Security validations**
- Admin blank-PDF upload: route-aware `PrivacyBoundaryMiddleware` (bearer +
  multipart + 10 MB) then `%PDF-1.` magic check, `pypdf` structural parse,
  rejection of `/JS`, `/JavaScript`, `/Launch`, `/EmbeddedFiles`, `/OpenAction`,
  `/AA`, and any populated AcroForm `/V` (no citizen data can enter).
- Government source/template URLs validated against `approved_source_domains`
  via DB triggers (unapproved hosts rejected 422).
- Citizens see only PUBLISHED versions; draft/in-review/verified/approved-but-
  unpublished remain private; bundle access requires auth and verifies the
  Ed25519 signature before returning.

**Database changes:** none. All M4 behaviour uses the existing M2 tables and
triggers. Migration unchanged.

**New environment variables:** `TEMPLATE_SIGNING_ED25519_SEED_B64`,
`TEMPLATE_SIGNING_KEY_ID`, `TEMPLATE_STORAGE_DIR`, `TEMPLATE_CDN_BASE_URL`.
New dependency: `python-multipart`.

**Quality gates (executed):** `ruff check .` ✅ · `ruff format --check .` ✅ ·
`mypy --strict app` ✅ (47 files) · `pytest` ✅ **102 passed** (M1–M4, incl.
PostgreSQL integration + security). Published-immutability trigger verified live.

**Known limitations / pending production integrations**
- Blank-PDF storage is a local filesystem adapter; production should swap the
  `ObjectStoragePort` for an S3-compatible / signed-CDN implementation.
- Automated source-checksum polling (`SCHEDULED_STATIC_PDF_CHECK`) is modelled
  but not scheduled; checks are admin-recorded for now.

## Milestone 5 — Entitlements, Leases & Offline Lifecycle ✅

**Schema reconciliation (migration `0002`):** the M2 `0001` migration used
provisional entitlement enum literals (`FREE_UPGRADED_SIGNATURE`, `STANDARD_BASE`,
…) and the column `is_upgraded_with_sig`, which the authoritative v2.1.0 spec
supersedes with `FREE_UPGRADED_SIG_50` / `PAID_UPGRADED_SIG_50`, the
`FREE_LIFETIME` / `STANDARD_20` / `SIGNATURE_50` / `SIGNATURE_UPGRADE_30` / `ESIGN`
buckets, and `is_upgraded_with_sig_30`. The old CHECK constraints would reject the
required values, so M5 added the **smallest** additive migration: rename one column
and rewrite five CHECK constraints. The 19-table inventory is unchanged; `upgrade`
/`downgrade`/`upgrade` verified clean on PostgreSQL 16.

**Endpoints (`/api/v1/entitlements`)**
`GET /public-keys` (public), `GET /wallet` (Bearer), `POST /leases`,
`POST /leases/{id}/upgrade`, `POST /leases/{id}/commit`, `POST /leases/{id}/release`
(all Bearer + `Idempotency-Key: <UUIDv4>`).

**Entitlement Lease Token (ELT)** — EdDSA/Ed25519 compact JWS, spec §8.2 claims
(`iss`, `sub`, `jti`=lease id, `tid`, `tier`, `st`, `dfd`, `did`, `cap`, `cwe_hours`,
`iat`, `exp`), header `typ=ELT+jwt`. Verifiable fully offline via `/public-keys`;
active + previous keys support rotation without invalidating in-flight tokens.
`exp` = reservation expiry (ISSUED) or correction-window expiry (COMMITTED).

**Lifecycle & locking** — every wallet+lease operation locks
`entitlement_wallets` `FOR UPDATE` then `entitlement_leases` `FOR UPDATE`, mutates
balances, appends a hash-chained ledger entry, writes an allowlisted audit event,
and commits atomically. Implements ISSUED→COMMITTED/RELEASED/EXPIRED_RELEASED,
EXPIRED_RELEASED→COMMITTED (delayed recovery), and COMMITTED→COMMITTED
(same-draft re-commit, 24h window, `410` when expired). 48h reservation / 24h
correction windows are configurable.

**Delayed recovery (§3.3)** — `completed_at_client_iso` is untrusted diagnostic
metadata only: credit is granted strictly against the authoritative wallet
balance (`409 LEASE_EXPIRED_CREDIT_CONSUMED` if spent, never negative), and the
client timestamp is capped at the server reservation window before adding 24h, so
a tampered clock cannot extend a window.

**Reaper** — `app/workers/reaper.py`: idempotent, multi-instance-safe
(`FOR UPDATE SKIP LOCKED`, wallet→lease lock order, deterministic
`reaper:<lease_id>` ledger key). See README for run instructions.

**Commercial safeguards** — free quota from `DEFAULT_FREE_QUOTA_TOTAL` (unset ⇒ 0);
no price hardcoded (upgrade consumes a `SIGNATURE_UPGRADE_30` wallet credit; rupee
values stay in `pricing_catalog`, pending product-owner approval). eSign tier
rejected (M7-gated).

**New environment variables:** `LEASE_SIGNING_SEED_B64`, `LEASE_SIGNING_KEY_ID`,
`LEASE_SIGNING_PREV_SEED_B64`, `LEASE_SIGNING_PREV_KEY_ID`.

**Quality gates (executed):** `ruff check .` ✅ · `ruff format --check .` ✅ ·
`mypy --strict app` ✅ (51 files) · `pytest` ✅ **128 passed** (M1–M5, incl.
PostgreSQL concurrency with separate sessions, reaper, and ledger-chain
verification). Acceptance A–G all covered.

**Known limitations / M6 prep**
- `REVOKED_REFUND` lease state and the refund ledger entry types exist and are
  reachable, but Razorpay refund processing is intentionally deferred to M6.
- Rupee pricing rows are not seeded (payment is M6); upgrade reads the wallet
  bucket, not a price.

## Milestone 6 — Payments, Webhooks & Refund Lifecycle ✅

No schema change was required: `pricing_catalog`, `payment_orders`,
`payment_refunds`, `payment_webhook_events` and the partial unique index
`uq_ledger_paid_credit_per_order` already exist from M1/M2.

**Endpoints (`/api/v1/payments`)**
`GET /pricing` (public), `POST /orders` (Bearer + `Idempotency-Key`),
`POST /orders/{order_id}/verify` (Bearer + `Idempotency-Key`),
`POST /webhooks/razorpay` (`X-Razorpay-Signature`, raw body, 64 KB cap).

**Pricing** is database-driven: only active, in-effect SKUs are returned, amounts
in integer paise, `tax = total_payable − base − surcharge`, eSign SKUs excluded.
Provisional ₹20/₹50/₹30 values are **not** seeded in production — the catalog is
configured explicitly (tests seed their own rows). The client never supplies an
amount (`extra="forbid"`).

**Orders** are priced and created server-side; the gateway `create_order` runs
before any row is persisted (a timeout leaves no order), and idempotency is
enforced by `uq_payment_orders_user_idempotency`.

**Signatures** — payment (`HMAC-SHA256(secret,'<order>|<payment>')`) and webhook
(`HMAC-SHA256(webhook_secret, raw_body)`) verified with constant-time compare; a
forged signature never credits a wallet; the frontend callback is never trusted.

**Exactly-once fulfilment** — `credit_order_idempotently` locks `payment_orders`
`FOR UPDATE` (`populate_existing` to refresh under the lock), returns without
mutation if already `CAPTURED`, else locks the wallet, credits the paid bucket,
appends one `PAID_CREDIT` ledger entry (backed by `uq_ledger_paid_credit_per_order`),
and is shared by sync verify and the webhook. A concurrent verify + `payment.captured`
race credits the wallet exactly once (tested with separate PostgreSQL sessions).

**Webhooks** — raw-body HMAC first; the `X-Razorpay-Event-Id` dedup row is inserted
in the *same* transaction as processing, so a genuine duplicate returns
`ALREADY_PROCESSED` and a processing failure rolls back both (never a false
"processed"). Unknown events are `IGNORED`.

**Refunds (all 7 scenarios)** — available-credit debit, reserved-lease revocation
(`REVOKED_REFUND`), post-consumption flag (`REFUNDED_POST_CONSUMPTION`, never
negative), partial refunds with the cumulative `base_amount_paise` revocation
threshold counted once, failed refunds (wallet/order untouched), duplicate-webhook
and concurrent-same-refund dedup (via `gateway_refund_id` uniqueness within a
SAVEPOINT). Each appends the correct hash-chained ledger entry.

**New environment variables:** `RAZORPAY_ENVIRONMENT`, `RAZORPAY_API_BASE`,
`RAZORPAY_GATEWAY_MODE`, `RAZORPAY_HTTP_TIMEOUT_SECONDS`, `PAYMENT_MAX_QUANTITY`
(plus the existing `RAZORPAY_KEY_ID/KEY_SECRET/WEBHOOK_SECRET`). Fake gateway is
the local/test default; secrets are never logged or returned.

**Quality gates (executed):** `ruff check .` ✅ · `ruff format --check .` ✅ ·
`mypy --strict app` ✅ (54 files) · `pytest` ✅ **162 passed** (M1–M6, incl.
PostgreSQL concurrency with separate sessions and ledger-chain verification).

**Known limitations / production integration**
- Real `HttpRazorpayGateway` HTTP calls are implemented but exercised only in
  live mode; automated tests use the deterministic fake gateway.
- Refunds are reconciled from the authoritative `refund.processed`/`refund.failed`
  webhooks; a separate admin-initiated refund-initiation endpoint is out of M6
  scope (the `initiate_refund` provider method is ready for it).

## Milestone 7 — Not started
Out of scope. eSign remains disabled (`503`) and eSign SKUs stay gated until M7 sign-off.
