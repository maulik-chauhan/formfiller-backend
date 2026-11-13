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
| DEC-8 | Admin role provisioning and review | **Open** | Security + Operations |
| DEC-9 | Blank PDFs with a view-only `/OpenAction` | **Decided** (option b, 2026-11-13) | Application Security |
| DEC-10 | Reuse terms and currency of official form sources | **Open** | Legal + Product |
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

## DEC-8 — Admin role provisioning

- **Dependency:** the admin console and every `/admin` API trust `users.role`. There is
  no API or screen that grants a role, by design.
- **Implemented now:** `python -m scripts.grant_role` for local development only (it
  refuses to run in staging/production and writes an `ADMIN_ROLE_ASSIGNED` audit event).
- **Needs:** who may grant curator/verifier/approver/super-admin roles in production,
  how a grant is approved and recorded, periodic access review, and how many people
  must hold each role so maker-checker separation is always possible.

## DEC-9 — Blank PDFs with a view-only `/OpenAction`

- **Found in F6:** the official Karnataka Transport copies of CMV Form 1 and CMV Form 9
  contain `/OpenAction [<page> /FitH null]`: an instruction to open the first page at
  fit-width. It is a view destination, not an action dictionary, and runs nothing.
- **Current behaviour:** `validate_blank_pdf` rejects any raw `/OpenAction` token, so
  these official files cannot be uploaded. Stripping it would change the file and its
  SHA-256, so the published blank would no longer be byte-identical to the official one.
- **Options:** (a) keep rejecting and skip such forms; (b) allow `/OpenAction` only when
  its value is a destination array and the document has no `/JavaScript`, `/JS`,
  `/Launch`, `/AA` or embedded files (needs a structural check, not a byte scan, plus
  tests); (c) publish a sanitised copy and record the official hash next to it.
- **Decision (2026-11-13, project owner): option (b).** `pdf_validator._check_open_action`
  parses the catalog and accepts `/OpenAction` only when it is an array
  `[<page of this document> <fit> <params>]` with a standard fit type and exactly the
  number of numeric/null parameters that type requires. It rejects:
  - any action dictionary;
  - a remote or numeric page target, or a named destination;
  - a raw `/OpenAction` anywhere except that single catalog entry.

  All other forbidden tokens are unchanged, and the official files stay byte-identical.
  Tests: `tests/unit/test_pdf_validator.py` (5 accepted shapes, 12 rejected).
- **Compressed-object gap closed (2026-11-14):** the raw-byte scan cannot see inside
  compressed object streams (PDF 1.5+). `_reject_forbidden_objects` now walks every
  object the file defines, compressed ones included. A forbidden name rejects the file
  whether it appears as a key or as a name value, e.g. `/S /JavaScript` or
  `/Type /EmbeddedFile`. An `/OpenAction` key outside the catalog is also rejected.
  The walk is bounded (200,000 objects, 2,000,000 nested items). Tests build PDFs whose
  markers exist only inside an object stream; with the walk disabled, 5 of those 8
  tests fail.
- **Not covered (policy unchanged):**
  - action types other than JavaScript and Launch on links, buttons or annotations:
    `/SubmitForm`, `/ImportData`, `/URI`, `/GoToR`, `/GoToE`, `/Named`, `/RichMedia`,
    `/Rendition` and others;
  - XFA forms (the `/XFA` key and its XML packets, whose stream content is not parsed);
  - encrypted PDFs that open with an empty user password (accepted, as before). A blank government PDF with a
  submit button could send filled values to a third-party URL when a viewer clicks it.
  Rejecting these is a separate policy decision.

## DEC-10 — Reuse terms and currency of official form sources

- incometaxindia.gov.in states that its material may be reproduced free of charge with
  accurate reproduction and prominent source acknowledgement.
- transport.karnataka.gov.in has a **Copyright Policy** in its footer ("Website
  Policies"). It is a pop-up with no URL of its own; the `/info-4/Copyright+Policy`
  path redirects to the home page. It states:
  - information may be republished free of charge only with **prior permission**,
    requested by email from CeG or the owning department;
  - it must be reproduced as is, without distortion, with the source acknowledged.

  The permission email address on the page is an unfilled placeholder. An earlier F6
  note recorded that no policy existed; that was wrong and is corrected here
  (2026-11-14). **Republishing these PDFs needs written permission, which has not been
  requested or obtained.**
- The Transport copies are retyped files (Online2PDF 2017–2018; Word/iLovePDF 2020).
  Whether they match the current text of the Central Motor Vehicles Rules and the
  Karnataka Motor Vehicles Rules, 1989, has not been confirmed against the Gazette.
- **Needs:**
  - written permission from the Transport Department / CeG for the Karnataka files, or a
    documented legal view that statutory forms may be redistributed regardless;
  - a check of each form against its current notified text;
  - both before any real (non-local) publication.

  Local development publication with fictional data is unaffected.

## COM-01, M7, LEG-1

Unchanged: quota/pricing stay database- and env-driven with a zero default; eSign code
paths remain disabled (`ESIGN_MODULE_ENABLED=false`); no DPDP compliance is claimed.
