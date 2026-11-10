# Decision Register

Open decisions that code cannot settle. Nothing below has been approved by anyone;
"Recommended" is an engineering recommendation only. Code that depends on an open
decision fails closed until the decision is recorded here with an approver and date.

| ID | Decision | Status | Approver needed |
|---|---|---|---|
| DEC-1 | Publisher trust anchor | **Open** | Product + Application Security |
| DEC-2 | Signing-key compromise recovery | **Open** (draft runbook written) | Application Security + Engineering lead |
| DEC-3 | Production template storage / CDN | **Open** | Infrastructure + Application Security |
| DEC-4 | Production CORS origins | **Open** | Infrastructure |
| DEC-5 | Stable shared-environment secrets | **Open** | Infrastructure / Security |
| DEC-6 | SMS (OTP) provider | **Open** | Product + Legal (DLT registration) |
| DEC-7 | Refresh-token storage (C-2) | **Open** | Application Security |
| COM-01 | Free quota and pricing | **Open** | Product owner |
| M7 | eSign | **Disabled** | Legal + Product (separate milestone) |
| LEG-1 | DPDP / retention review | **Open** | Legal counsel |

## DEC-1 — Publisher trust anchor

- **Dependency:** the client may only call a template authentic if its signature
  verifies under a key the client already trusts. Keys fetched from
  `/templates/publisher-keys` are distribution hints and are never trusted.
- **Recommended:** build-time pinned Ed25519 public keys
  (`VITE_TEMPLATE_PUBLISHER_KEYS`, a JSON map `kid → key`), with the private seed held
  in a KMS/HSM-backed secret, generated in a witnessed ceremony.
- **Implemented now:** multiple pinned kids; previous-key support on the backend;
  unknown kid rejected; no pins ⇒ verification and download disabled with a clear
  message; the published test key is refused in staging/production builds.
- **Needs:** the approved production key (public half only in the repo/build config),
  its `kid`, and sign-off. Until then production authenticity and download stay disabled.

## DEC-2 — Signing-key compromise recovery

- **Dependency:** a leaked publisher seed lets an attacker sign forged templates.
- **Recommended:** `docs/KEY_COMPROMISE_RUNBOOK.md` (draft, untested as a drill).
- **Implemented now:** the mechanisms the runbook relies on — retire a key by removing
  it from config (its signatures stop verifying server-side), per-kid client pins,
  re-verification of every cached template on each load (a template signed by a
  removed kid is purged), status endpoint for withdrawals.
- **Needs:** approval of the policy, named owners, and one rehearsal. Do not claim
  incident readiness before that.

## DEC-3 — Production storage / CDN

- **Dependency:** blank PDFs must be served over HTTPS from a host the client pins.
- **Recommended:** private object-store bucket + CDN, immutable content-addressed
  objects, short-lived signed URLs optional.
- **Implemented now:** provider-neutral port (`ObjectStoragePort`), a filesystem adapter
  and a test-only signed-URL adapter; production refuses the test adapter.
- **Needs (exact configuration):** provider; bucket; write credentials for the admin
  upload path only; CDN hostname (→ `TEMPLATE_CDN_BASE_URL` and the frontend's
  `VITE_TEMPLATE_CDN_HOST`); whether URLs expire and the TTL
  (`TEMPLATE_DOWNLOAD_URL_TTL_SECONDS`); CDN CORS allowing the frontend origins for
  `GET`; no redirects on object URLs; path must stay `/blank_pdfs/<sha256>.pdf`.
  Then implement one adapter class and select it via `TEMPLATE_STORAGE_PROVIDER`.

## DEC-4 / DEC-5 — CORS origins and stable secrets

- `CORS_ALLOWED_ORIGINS` must list the real frontend origins (exact, https, no wildcard
  in production — enforced at startup). Values are unknown today.
- Staging and production refuse to start without three distinct, 32-byte Ed25519 seeds
  (access token, entitlement lease, template publisher) and three distinct HMAC keys.
  They must come from a secret manager and be identical across instances.

## DEC-6 — SMS provider

`ProductionSmsProvider` is an interface only; `send_otp` raises. A licensed Indian SMS
gateway (with DLT template registration) must be selected and integrated.

## DEC-7 — Refresh-token storage

Tokens are memory-only in the browser, so a page reload signs the user out. Moving the
refresh token to an HttpOnly cookie would change the backend auth model and CORS
(credentials). Left unchanged deliberately; needs a security decision.

## COM-01, M7, LEG-1

Unchanged: quota/pricing stay database- and env-driven with a zero default; eSign code
paths remain disabled (`ESIGN_MODULE_ENABLED=false`); no DPDP compliance is claimed.
