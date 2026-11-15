# F6 hardening report

**Date:** 2026-11-15 · **Scope:** a focused hardening pass after F6.3–F6.4 local
acceptance (KMV 12, CMV Form 1), before F7.
**Status:** implemented on `feat/f6-hardening` in both repositories. Nothing is
committed, pushed or merged; the local dev database is unchanged.

**Recommendation:** **GO for starting F7 planning**, conditional on the review and merge
of these branches. **External publication stays NO-GO** (DEC-10, DEC-1, DEC-3).

## 1. Git reconciliation

| Repository | Branch at start | HEAD | origin/main | Working tree |
|---|---|---|---|---|
| Backend `maulik-chauhan/formfiller-backend` | `feat/f6-onboarding-prep` | `047eb2c` | `047eb2c` | no tracked changes; untracked user files only |
| Frontend `KM-Inc/screenshot-to-sight-3914` | `main` | `c03f0df` | `c03f0df` | clean |

- **Untracked user files in the backend, preserved and not touched:** `.DS_Store` files,
  `.claude/`, `Claude outputs/`, the three architecture spec files, `docs/user-manuals/`
  and `frontend/`.
- **The five F6.3–F6.4 changes the acceptance report listed as uncommitted are on
  backend `main`** (commit `047eb2c`, fast-forwarded with approval):
  1. CMV directory renamed;
  2. CMV slug updated;
  3. F6 prep document updated;
  4. manifest test updated;
  5. acceptance report added.
- **CMV manifest:** `docs/onboarding/ka-cmv-form-1-physical-fitness/manifest.json`,
  slug `ka-cmv-form-1-physical-fitness`, manifest SHA-256 `8b538e46…88a97`. The README,
  the prep doc and the test all match.
- **Acceptance report:** its "uncommitted" wording was corrected to cite `047eb2c`.
- **Leftover frontend worktree:** `scratchpad/fe-reconcile` (detached, prunable) is
  still there and was not deleted.
- **New branches:** `feat/f6-hardening` in both repositories, created from `main`.

## 2. Exact-host source-domain security

**How matching works today:**
- `approved_source_domains` stores `domain_pattern`, `domain_type` and
  `allow_subdomains` (database default TRUE).
- The SQL function `fn_validate_url_against_approved_domains` checks source and version
  URLs, both in triggers and in the admin validation step.

**Findings:**

1. **The exact-host flag is not enforced.** `domain_type` is never consulted. A
   `STATUTORY_EXACT_HOST` domain created with `allow_subdomains = true` matches
   subdomains, and the console checkbox was ticked by default. That is how
   `transport.karnataka.gov.in` ended up allowing subdomains. No console edit existed.
2. **A host-parsing bypass (new and more serious).** The function took the "host" as
   everything up to `/` or `:`. With subdomains on, a URL whose host is
   `evil.example` matched `transport.karnataka.gov.in`. Reproduced read-only on the
   dev database:
   - `https://evil.example#.transport.karnataka.gov.in` → accepted
   - `https://evil.example?.transport.karnataka.gov.in` → accepted
   - `https://transport.karnataka.gov.in:8443/x` → accepted (a port was allowed)

   **Impact:** a maker could record a non-government download URL as a version's
   official provenance. The PDF itself is uploaded and reviewed separately, and the
   bypass needs subdomains on. The version upload URL had no API-level pattern check;
   the portal and checked URLs did.

**Implemented:**

| Change | Where |
|---|---|
| Migration `0004`: the host must be `[A-Za-z0-9.-]+` followed by `/` or the end of the URL. User-info, ports, queries and fragments are refused. It refuses to run if any existing active source or version URL would stop validating. Downgrade restores the old function. | `backend/alembic/versions/0004_f6_strict_source_url_host.py` |
| Creating a domain refuses `STATUTORY_EXACT_HOST` with `allow_subdomains: true` (422) | `schemas.py` |
| `PATCH /api/v1/admin/domains/{domain_pattern}`, super admin only: changes `allow_subdomains` and `rationale`; the pattern, type and active flag cannot change. See the rules below. | `service.update_domain`, `router.py` |
| Admin console: choosing "exact host" unticks and disables "Allow subdomains"; a legacy exact host with subdomains on shows a warning; super admins get an "Edit domain scope" form | `SourcesPage.tsx`, `api.ts` |

Rules for the `PATCH` endpoint:
- `expected_allow_subdomains` is required: a stale edit is refused with 409.
- An exact host cannot be widened (422).
- Narrowing is refused (409) while any source or version under that pattern uses a
  subdomain. The URL triggers re-check rows on every update, so narrowing would
  otherwise block later status changes.
- Each change writes an `ADMIN_APPROVED_DOMAIN_UPDATED` audit row. It records the
  changed field names and the before and after values of `allow_subdomains`
  (allowlisted booleans), never the rationale text.
- A request that changes nothing does nothing and writes no audit row.

**Acceptance criteria (tests in `backend/tests/integration/test_domain_scope.py`):**

| Criterion | Result |
|---|---|
| `transport.karnataka.gov.in` approved (exact, mixed-case host, with and without a path) | pass |
| `transport.karnataka.gov.in.evil.example` rejected | pass |
| Unapproved subdomain rejected in exact-host mode | pass |
| `#`, `?`, `\`, user-info, port and empty-host tricks rejected even with subdomains on | pass (7 cases) |
| Existing approvals not silently altered | the migration changes no row; a failed edit changes nothing (asserted); other domains are untouched |
| Audit history records the change | pass (actor, resource and metadata asserted) |

**Dev data:** a read-only check found 0 source or version URLs that would fail the strict
rule, and none under `transport.karnataka.gov.in` using a subdomain. Migration `0004` is
**not applied** to `gov_forms_dev`; it is still at `0003`. The Karnataka domain still
allows subdomains. **Next, with your approval:**
1. run `alembic upgrade head` on the dev database;
2. as the super admin, open Sources & domains → `transport.karnataka.gov.in` → Edit
   domain scope, and save it with a rationale.

The editor was checked in the running app: the warning shows and the checkbox is locked
off. Nothing was saved.

## 3. KMV 12 print overflow

**Root cause: the manifest geometry, not the fill engine.**
- The engine already refuses text wider than its box and never truncates.
- The 1.0.0 boxes were 188–336 pt wide, but the printed rules end at x 466–476. Values
  ran up to 98 pt past their rules, and items 1, 2, 4, 5 and 6 ran past the form's own
  right text margin (x 528.7).
- The rules use negative character spacing. pdf.js applies it; my first estimate from
  the font width table did not and was about 28 pt too long. The test re-measures the
  rules from the PDF.

**Policy, per field (`docs/onboarding/ka-kmv-12-conductor-licence/README.md` §6):**
- A value sits on its rule and never extends past the rule's end.
- It wraps only upward, into empty space the manifest reserves (items 1, 2, 4–7).
- Below the rule is not usable: a first attempt that wrapped downward was struck through
  by the rule, as the render showed.
- Fixed-length values (dates, the fee) stay on one line.
- Item 8's issuer cannot wrap, because item 8's answer prints just above it. It is
  8 pt on one line, with its limit reduced from 34 to 24 and a help text asking the
  applicant to abbreviate.
- Never shrunk below the manifest size (readable floor 7 pt, used only for the
  3-line addresses) and never truncated. An answer that still does not fit fails PDF
  generation, naming the field.

**Contract change (DEC-11, proposed):**
- Contract v1 lays multi-line boxes out from the top, so a short answer would float
  above its rule. An optional `valign: "bottom"` for multi-line `text_at` was added in
  step to both validators, the JSON Schema, the contract document and fixtures (new
  invalid case `valign-without-multiline`), and the fill engine.
- The default `top` keeps every existing output identical (unit-tested).
- Shrink-to-fit was **not** added: the contract's "never shrunk" rule is unchanged.

**Corrected manifest:** `manifest-1.0.1.json` (SHA-256 `e4e80091…5332b`), with the same
identity and blank PDF. It is not onboarded; it needs maker → checker → approver. 1.0.0
stays as the published local record.

**Regression tests:** `frontend/src/test/forms/kmv12-real-pdf.test.ts` uses the **real
official PDF** after checking its SHA-256. It skips when the uncommitted PDF is absent.
It checks:
- the rules are re-measured from the blank;
- 1.0.1 with typical answers: every answer prints on one line on its rule;
- 1.0.1 with maximum-length answers: every line stays inside its rule, the lowest line
  sits on the rule, no value line touches a rule, a label or another value, everything
  stays within the text column, every word is printed, and the blank is unchanged;
- over-wide answers are refused with `text_overflow`;
- 1.0.0 is shown to overflow, which proves the test detects the defect.

**Renders:** `Claude outputs/f6/hardening/kmv12-1.0.1-{typ,max}.pdf(.png)`. The original
PDF is unmodified.

**Residual:** at maximum length, the top line of item 4 sits about 2.7 pt below item 3's
rule. It is legible and nothing touches, but the checker should confirm it on screen.

## 4. CMV Form 1 supplementary particulars

**The form's own wording:**
- Item (g): "if so, give details" — and the line holds only "Yes/No".
- Note (1): applicants answering "Yes" to (a), (c), (e), (f) or (g), or "No" to (b) or
  (d), "should amplify his answers with full particulars".

There is no space for either on the page.

**Not implemented.** No safe, consistent design fits the current architecture:
- Contract v1 maps answers only onto the official page. A filled PDF must keep the
  blank's page count, and nothing can be printed that is not a mapped field.
- Adding a page or free text would be a new contract mapping, and its legal
  acceptability is unknown.

**Recommended design (for DEC-12):**
- **Contract v2 annex:** a `supplementary` section type whose fields render onto one or
  more pages generated locally by pdf-lib and appended after the official page.
  - **Annex page content:** a clearly different heading, "Annexure to Form 1 —
    particulars under Note (1) — prepared by the applicant", plus the applicant's
    name, a reference to each question answered, and a signature line.
  - **Shown only when required:** the conditions are the Note (1) triggers, expressed
    with the existing `visible_when`.
  - **The official page stays byte-identical** apart from its usual filled values.
- **Privacy:**
  - Health particulars are sensitive. They stay in the encrypted vault like every other
    answer and are never uploaded, logged or sent to analytics.
  - The exported PDF will contain them, so the export screen should say so.
  - The privacy acceptance test must add annex sentinels.
- **User-facing limitations:**
  - Whether an RTO accepts a separate annex must be confirmed (Legal / the
    department) before release.
  - Until then, the applicant writes the particulars on a separate sheet by hand.
- **Smaller interim step (not done):** CMV 1.0.0's help text mentions Note (1) only on
  (g). A 1.0.1 manifest could add the same note to (a)–(f) under their trigger
  answers. It needs a new onboarding round.

## 5. CMV Form 1 age and date-of-birth check

**Conclusion: no validation added.** The legal and reference-date basis could not be
established, and contract v1 cannot express the rule. The limitation stays documented.

- **The form:**
  - Item 5(a) is the date of birth and 5(b) the "Age on date of application".
  - The form has **no date-of-application field**: only a signature line.
- **The rule:** CMVR Rule 5(2) only requires the Form 1 declaration with an application
  for a medical certificate; it sets no age test ([Rule 5 text](https://future.indiankanoon.org/doc/36229196/)).
  Licensing ages are set elsewhere (Motor Vehicles Act s.4) and depend on the licence
  class, which this form does not record.
- **Why "today" is not used:** checking against today's date would assume the PDF is
  made on the day of submission. That assumption is not established, and an applicant
  who prepares the form on one day and submits it on another would get a false error.
- **Contract v1:** has no computed cross-field rule. The help text already says the age
  "is not calculated from the date of birth".
- **For F7, if wanted:** a non-blocking consistency hint that needs an explicit
  reference date entered by the applicant ("date you will submit"), never a blocking
  legal rule.

## 6. Self-hosted fonts

**Result:**
- Google Fonts is removed: the preconnect links and the stylesheet in
  `src/routes/__root.tsx`.
- `src/fonts.css` declares the same families, weights and axes:
  - IBM Plex Sans 400/500/600;
  - Fraunces, variable, with the optical-size axis (used at 500/600).
- The files come from `@fontsource/ibm-plex-sans@5.3.0` and
  `@fontsource-variable/fraunces@5.3.0` (SIL OFL-1.1), installed with your approval and
  pinned exactly in `package.json` and `bun.lock`.
- Only the Latin and Latin Extended subsets are declared (Latin Extended carries ₹),
  with the same `unicode-range` split Google serves.
- The `--font-*` tokens and the layout are unchanged.
- The `styles.css` header now forbids third-party font or CSS origins.

**Verified:**
- **Production build:** OK; 8 `.woff2` files emitted, 0 references to
  `fonts.googleapis.com` or `fonts.gstatic.com` in `.output`.
- **Running app:** fonts load from the local server; 0 third-party requests; computed
  `body` is IBM Plex Sans and `h1` is Fraunces.
- **Not covered:** other scripts (Greek, Cyrillic, Vietnamese) now fall back to system
  fonts. The UI is English-only today.

## 7. Signature-image acceptance gap

**What the F5 report left open:** signature-image privacy was not verified, because
contract v1 has no signature or image input.

**Current state:**
- No capture, storage, embedding or test exists in the frontend.
- The backend has entitlement tiers (`SIGNATURE_50`, `SIGNATURE_UPGRADE_30`) but no
  image handling.
- eSign stays disabled (`ESIGN_MODULE_ENABLED=false`).

**Untested or incomplete:**
- capture (draw or upload), size and format limits, EXIF removal;
- encryption of the image bytes in the vault and AAD binding;
- persistence across lock and unlock;
- deletion;
- embedding into the PDF (`image_at` mapping, box fit, aspect ratio, no upscaling
  blur);
- export;
- network silence;
- absence from logs, IndexedDB plaintext, error reports and the service-worker cache.

**Changes needed in both repositories:**

| Area | Backend | Frontend |
|---|---|---|
| Contract v2 | `signature` field type, `image_at` mapping, validator, schema, fixtures | mirror validator; renderer |
| Capture | — | canvas pad and file upload; size, type and dimension limits; strip metadata (re-encode to PNG) |
| Storage | none (must never receive images) | vault record type, encrypted, with record-id AAD; delete with the draft |
| PDF | publication check that `image_at` boxes are on the page | `embedPng` into the box, keeping the aspect ratio; refuse if too small |
| Entitlements | lease semantics for the `SIGNATURE_*` tiers (product decision; no pricing invented) | gate before embedding |
| Privacy tests | route-inventory and privacy-surface tests stay unchanged (no new upload route) | sentinel image bytes; network capture; IndexedDB scan; `pg_dump` and API-log scan |

**Test plan (separate acceptance):**
1. draw and upload signatures; reject oversized or non-image files;
2. lock and unlock the vault, reload, and confirm the signature persists encrypted;
3. place it on a fixture form and the real KMV 12, and confirm the box fit by pdf.js
   image-operator inspection;
4. export and confirm byte presence only in the exported file;
5. network: no request body contains the sentinel image hash or base64 prefix;
6. IndexedDB: no PNG magic bytes in plaintext;
7. delete the draft and confirm the record is gone;
8. confirm the backend rejects any `image_at` box off the page.

Decision needed: DEC-12.

## 8. Changed files and test results

**Backend (`feat/f6-hardening`, uncommitted):**
- **Domain security:**
  - new `backend/alembic/versions/0004_f6_strict_source_url_host.py`;
  - `app/modules/templates/{schemas,service,router}.py` (create rule, `update_domain`,
    `PATCH` route);
  - `app/modules/audit/schemas.py` (two boolean audit keys);
  - new `tests/integration/test_domain_scope.py` (12 tests);
  - `tests/security/test_privacy_surface.py` (reviewed route inventory +1).
- **Contract `valign`:**
  - `app/modules/templates/manifest_contract.py`;
  - `docs/contracts/form-manifest-v1.{md,schema.json}`;
  - `scripts/build_contract_fixtures.py`, new
    `docs/contracts/fixtures/manifests/invalid/valign-without-multiline.json`,
    `docs/contracts/fixtures/index.json`;
  - `tests/unit/test_manifest_contract.py` (+4).
- **OpenAPI:** `docs/contracts/openapi.json` (new `PATCH` route).
- **KMV 12:**
  - new `docs/onboarding/ka-kmv-12-conductor-licence/manifest-1.0.1.json`;
  - its `README.md` §6;
  - `tests/unit/test_onboarding_manifests.py` (versioned manifests; identity unchanged
    except the version number).
- **Docs:** this report; `docs/DECISION_REGISTER.md` (DEC-11, DEC-12);
  `docs/F6_ACCEPTANCE_REPORT.md` (Git status correction).

**Frontend (`feat/f6-hardening`, uncommitted):**
- **Admin:** `src/features/admin/{SourcesPage.tsx,api.ts}`; new
  `src/test/admin/admin-domains.test.tsx` (4 tests).
- **Engine:** `src/features/forms/{manifest.ts,pdf-fill.ts}`; new
  `src/test/forms/valign.test.ts` (4) and `src/test/forms/kmv12-real-pdf.test.ts` (5).
- **Contracts:** `contracts/**` (synced, byte-identical); `docs/backend/openapi.json`;
  `src/test/openapi-contract.test.ts` (+1).
- **Fonts:** new `src/fonts.css`; `src/styles.css`; `src/routes/__root.tsx`;
  `package.json`; `bun.lock`.

**Gates:**

| Gate | Result |
|---|---|
| Backend pytest | **332 passed, 4 skipped** (was 311 + 4) |
| Backend ruff check / format / `mypy app` | clean / 108 files formatted / no issues |
| Contract fixtures and frontend copy | regenerated; byte-identical (test passes) |
| Migration 0004 downgrade → upgrade (test database) | OK |
| Frontend vitest | **451 passed, 30 files** (includes the 5 real-PDF tests, which ran) |
| Frontend eslint / `tsc --noEmit` | 0 errors (the 9 earlier fast-refresh warnings remain) / clean |
| Frontend production build | OK; 0 Google Fonts references |

## 9. Blockers and open items

- **Your approval to:**
  1. commit both branches;
  2. push them and merge them (frontend `main` syncs to Lovable);
  3. apply migration 0004 to the dev database and narrow the Karnataka domain through
     the console;
  4. onboard KMV 12 version 1.0.1.
- **DEC-11:** approve `valign` (contract addition).
- **DEC-12:** the annex for particulars and signature images (product and legal).
- **Unchanged:** DEC-1, DEC-2, DEC-3, DEC-8 and DEC-10 remain open; no external
  publication; eSign stays disabled; no DPDP compliance is claimed.

## 10. GO / NO-GO for F7

**GO to start F7 planning and implementation on local development**, once these
branches are reviewed and merged. The F6 hardening items are either fixed (domain
security, the overflow policy, fonts) or documented with decisions recorded (particulars,
age check, signatures). None of them blocks F7.

**NO-GO for any external publication** until DEC-10 permission is obtained and DEC-1
and DEC-3 are decided.
