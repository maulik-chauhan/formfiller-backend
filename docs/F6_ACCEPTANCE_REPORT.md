# F6.3–F6.4 — Local government form onboarding and end-to-end acceptance

**Scope:** local development only, with fictional applicant data. Nothing was published
externally. No production keys, secrets, URLs or permissions were created. eSign stays
disabled, and no DPDP compliance is claimed.

Run on 2026-11-15.

## Decision

| Question | Recommendation |
|---|---|
| **Local F6 acceptance** of KMV 12 and CMV Form 1 | **GO** |
| **Wider (non-local) publication** | **NO-GO**: Karnataka Transport's republication permission has not been obtained (DEC-10), each form's currency against its notified rule text is unconfirmed, and production publishing infrastructure (DEC-1, DEC-3) does not exist |

Local publication acceptance is **not** external publication authorization. Approving
the domain `transport.karnataka.gov.in` records that the site is official; it is not
permission to reuse its documents.

## 1. Revisions tested

| Item | Value |
|---|---|
| Backend | branch `feat/f6-onboarding-prep` at `c5f7116`, plus this run's uncommitted changes (§10); backend `main` is `e174b6e` |
| Frontend | `main` at `c03f0df` (unchanged) |
| Database | local PostgreSQL 16 `gov_forms_dev`, migrations at head `0003` |
| Signing key | local development key `ed25519-template-dev-local`, pinned in the git-ignored frontend `.env.local` (development only) |
| Browser automation | Playwright 1.48.2 driving the installed Google Chrome 157 with a throwaway profile, through the real UI |

## 2. Official sources and verification evidence

| | KMV 12 | CMV Form 1 |
|---|---|---|
| Issuing authority | Transport Department, Government of Karnataka (Karnataka Motor Vehicles Rules 1989, Rule 19(1)) | Same department's copy of a Central Motor Vehicles Rules form (Rule 5(2)) |
| Official PDF | https://transport.karnataka.gov.in/storage/pdf-files/FORM%20KMV%2012.pdf | https://transport.karnataka.gov.in/storage/pdf-files/Forms%20related%20to%20driving%20license%20in%20English/form%201.pdf |
| SHA-256 | `3795a568…7263ab` | `c936da8e…0f1682c` |
| Revision evidence | server `Last-Modified: Mon, 01 Jun 2020 09:35:57 GMT`; no edition date on the form | `Last-Modified: Tue, 14 Jan 2020 09:15:32 GMT`; no edition date on the form |
| Retrieved / re-verified | 2026-11-13 20:32 IST / 2026-11-14 (live hash identical) | same |
| Currency against notified rule text | **unverified** | **unverified** |
| Reuse permission | **not granted**: the site's Copyright Policy requires prior permission by email (placeholder address); not requested | same |

The PDFs uploaded are the original files in `Claude outputs/f6/official-blanks/`,
unmodified. Their hashes match the values above. Per-form detail:
`docs/onboarding/<slug>/README.md`.

## 3. Source-domain configuration (through the admin console)

| Record | Value |
|---|---|
| Approved domain | `transport.karnataka.gov.in` · type Statutory body (exact host) · rationale "Official website of the Transport Department, Government of Karnataka (forms listing). Approval is not reuse permission (DEC-10)." |
| Government source | `KA_TRANSPORT` · Karnataka · "Transport Department, Government of Karnataka" · `https://transport.karnataka.gov.in/` · domain above |
| Source checks | Two per version: one recorded automatically at upload (`INITIAL_INGEST` + hash), and one entered manually with `ADMIN_MANUAL_UPLOAD`, HTTP 200, the server Last-Modified date, the observed hash, and a link to the version ID |

**Gaps in the existing interface**, reported rather than bypassed:
1. **"Allow subdomains" was saved as on.** The checkbox defaults to ticked, the script left it, and the console cannot edit a domain afterwards. The domain therefore also allows subdomains of `transport.karnataka.gov.in`. Correcting it needs a domain-edit capability or an approved database change; the database was not edited directly.
2. **No field for the form's revision.** Revision evidence is captured through the server Last-Modified date and the hash on source checks.
3. **No field for reuse-permission status.** It lives in documentation (DEC-10) and in each template's description.

## 4. Templates, versions and manifest validation

| | KMV 12 | CMV Form 1 |
|---|---|---|
| Slug | `ka-kmv-12-conductor-licence` | `ka-cmv-form-1-physical-fitness` (prefix set in F6.3, before the template existed) |
| Code · category · jurisdiction | `KMV-12` · Transport · Karnataka | `CMV-FORM-1` · Transport · Karnataka |
| Version · pages | 1.0.0 · 1 (US Letter) | 1.0.0 · 1 (A4) |
| Version ID | `a947bd5d-a882-456b-acaf-f0585b3adc2d` | `0c2b01bd-21e1-461d-98f2-a703fdc645dd` |
| Manifest SHA-256 | `b74350fe4661f283…` | `8b538e46a873dc0b…` |
| Bundle digest (signed, v2) | `a8871cc13ca9b75d…` | `eb6acbfaf0e0bb95…` |
| Backend validator (CLI, real PDF) | blank-PDF check PASS; manifest 0 issues | PASS; 0 issues |
| Console dry-run validation | "All checks passed"; 0 warnings | "All checks passed"; 0 warnings |
| Sample-fill preview | rendered | rendered |
| Status | **Published** 2026-11-15 10:47 IST; "Publisher signature verifies (ed25519-template-dev-local)" | **Published** 2026-11-15 10:47 IST; signature verifies |

Neither template nor slug existed before this run, so nothing was duplicated or renamed
after creation.

## 5. Admin workflow by role

| Step | Actor (role) | KMV 12 | CMV Form 1 |
|---|---|---|---|
| Domain, source, templates, upload, source checks | 50001 `615c7557` (SUPER_ADMIN, maker) | done | done |
| Draft → Submitted for review (with note) | 50001 | done | done |
| Maker tries to verify own version | 50001 | **blocked** (Verify disabled) | **blocked** |
| Submitted → Verified (with note) | 50002 `cfe26655` (TEMPLATE_VERIFIER) | done | done |
| Verified → Approved → Published (with notes) | 50003 `b52df35e` (TEMPLATE_APPROVER) | done | done |

The database approval log shows three distinct actors per version, so the maker-checker
separation held.

## 6. Citizen workflow (account 50004, fictional data)

| Check | KMV 12 | CMV Form 1 |
|---|---|---|
| Catalogue (anonymous); Karnataka filter shows both | listed | listed (2 forms under Karnataka) |
| Detail: version 1.0.0, department, official-source link | yes | yes |
| Prepare: bundle signature (pinned key) and blank-PDF checksum verified on device, stored encrypted | "Checks passed…" | "Checks passed…" |
| Status checks against the server | `…/status` called 3× | 3× |
| Field validation | future date of birth → "The date cannot be in the future."; fee 100000 → "Enter 99999 or less." | age 1000 → "Enter 999 or less." |
| Conditional field | issuer hidden → shown on "Previously held" → hidden on "Not previously held" | n/a (the form has no conditional fields) |
| Cross-field rules | none defined: the form states none | none defined |
| Profile reuse with confirmation | "Replace existing answers?" listed "1. Name: Current: Wrong Name → Profile: Zorawar Qentinel"; replaced only after ticking; empty DOB filled | profile chosen at draft creation; name and DOB filled |
| Draft save → reopen | values intact after reopening from My drafts | saved |
| Draft deletion | — | throwaway draft deleted, others kept |
| Completeness | 16 of 16 required | 14 of 14 required |
| PDF create, preview, navigation | created; "Page 1 of 1"; next page disabled (single page) | same |
| Export | `ka-kmv-12-conductor-licence-1.0.0-…pdf` | `ka-cmv-form-1-physical-fitness-1.0.0-…pdf` |
| Maximum-length variant (every field at its limit) | created, no overflow | 200-character address: 3 of 4 lines |

## 7. Independent PDF inspection (pypdf + rendering)

| Check | KMV 12 (typical) | KMV 12 (max length) | CMV Form 1 |
|---|---|---|---|
| Pages and size match blank | 1 · 612×792 | 1 · 612×792 | 1 · 596.04×842.04 |
| Original printed text preserved | all lines present | all present | all present |
| Every value inside its manifest box | 14/14 text boxes | 14/14 | 7 filled boxes (temporary address left blank, as entered) |
| Values and formats | exact; dates `DD/MM/YYYY` | exact, addresses wrap onto 2 lines in full | exact |
| Clipping or truncation | none | none | none |
| Ticks and crosses | items 9 and 10 ticked on their lines | same | each "no" crosses "Yes" and each "yes" crosses "No", (a)–(g) all correct |
| Fonts | Helvetica (standard) for values; the original text keeps its own fonts (some not embedded in the official file) | same | same; original fonts embedded |
| Interactive content | no annotations, actions, JavaScript or embedded files; structural forbidden-marker scan clean | same | same; the original's view-only `/OpenAction` is kept |
| Structure | readable by pypdf, not encrypted; an empty `/AcroForm` (`/Fields []`) is added by the fill library and holds no fields | same | same |

**Signature:** both forms need a hand signature (and KMV 12 a duplicate signature); none
is printed. **Cosmetic:** at maximum length, KMV 12 single-line values run past the printed
underline while staying within the page margin.

## 8. Privacy and security acceptance

Searched for the fictional values: "Qentinel", "Zorawar", "Bhimsen", "1999-07-21", "4321",
"Jayanagar" and the vault passphrase.

| Check | Evidence | Result |
|---|---|---|
| Applicant data never sent to the backend | All 27 citizen requests to the API and CDN captured with bodies: sign-in (2 POSTs), and GETs of templates, sources, template detail, bundle, status and blank PDFs | **0** fictional values in any URL or body |
| Completed PDFs never uploaded | No citizen POST, PUT or PATCH with PDF or multipart content | none |
| Only approved blank PDFs downloaded | `GET 127.0.0.1:8001/blank_pdfs/{sha256}.pdf` ×2, each verified by hash before use | yes |
| Drafts encrypted on the device | IndexedDB `govforms-local`: vault 9 records (encrypted); drafts and profiles stores hold no plaintext; localStorage empty | **0** fictional values in plaintext on the device |
| No applicant data in logs | Full API log for the run | **0** matches |
| No applicant data in the database | Full `pg_dump` of `gov_forms_dev` (279 KB) | **0** matches |
| Admin uploads | Only blank PDFs and manifests to `/admin/.../versions/validate` and `/versions` (multipart; sizes not captured by the tool) | stored hashes equal the official files |

**Note:** the frontend loads its typefaces from Google Fonts (`fonts.googleapis.com`,
`fonts.gstatic.com`). These requests carry no form data, but they expose the visitor's IP
address to a third party. Self-hosting the fonts is recommended before production.

## 9. Known limitations and deferred items

- **Not on the forms:** signatures, photographs and attachments are completed by hand; eSign stays disabled.
- **KMV 12:** strike-out items 7, 8 and 11 print the chosen statement rather than striking text; the age ≥ 18 rule is covered only by the declaration; addresses are free text.
- **CMV Form 1:**
  - there is no space for details after a "Yes" to (g), or the further particulars Note (1) asks for;
  - age is not cross-checked against the date of birth;
  - the placement of identification marks (1) and (2) follows an interpretation of an ambiguous layout.
- **Form 121:** deferred; it needs a contract v2 decision.
- **Out of scope:** DEC-1, DEC-2, DEC-3, DEC-8 and DEC-10 remain open; DEC-9 follow-ups (submit-form and similar actions, XFA, encrypted PDFs) remain open.
- **Domain configuration:** the subdomain flag (§3) needs a console edit capability or an approved database correction.

## 10. Changes made in this run (uncommitted, branch `feat/f6-onboarding-prep`)

- `docs/onboarding/cmv-form-1-physical-fitness/` renamed to `docs/onboarding/ka-cmv-form-1-physical-fitness/`; manifest identity slug updated; README and manifest hash updated.
- `docs/F6_ONBOARDING_PREP.md`: CMV slug and hash updated.
- `backend/tests/unit/test_onboarding_manifests.py`: expected folder name updated.
- `docs/F6_ACCEPTANCE_REPORT.md`: this report.

There are no frontend changes and no application-code changes.

**Gates:**
- pytest: 311 passed, 4 skipped.
- ruff check and format: clean.
- mypy (`app`, and the changed test): clean.
- Contract fixtures and OpenAPI: up to date.

**Evidence** is in `Claude outputs/f6/acceptance/` (not committed):
- screenshots for each admin and citizen step;
- the network logs (`admin-*-network.json`, `citizen-network.json`);
- `citizen-results.json` and `admin-*-results.json`;
- the three exported PDFs and their renders.

## 11. Reproduce

1. Start PostgreSQL (`~/.govforms-pg16`, port 55432), the API, the dev CDN and the frontend, as in `docs/LOCAL_DEVELOPMENT.md`.
2. Sign in as 50001 → Admin → Sources & domains: approve the domain and register the `KA_TRANSPORT` source. Create each template with the slugs above.
3. Upload the blank PDF from `Claude outputs/f6/official-blanks/`, version 1.0.0, downloaded 2026-11-13 20:32, the official URL, and the manifest from `docs/onboarding/<slug>/manifest.json`. Validate, upload, submit.
4. As 50002, verify. As 50003, approve and publish.
5. As 50004, run the citizen steps in §6 with fictional data.
