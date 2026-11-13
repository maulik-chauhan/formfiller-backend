# F4 Full-Stack Integration and Acceptance Report

Date of this report: 2026-11-13 (supersedes the 2026-11-12 version).

| Repository | Branch | Committed at | Uncommitted now |
|---|---|---|---|
| Backend `maulik-chauhan/formfiller-backend` | `feat/f3-f4-integration` | `94882e2` | `backend/tests/conftest.py`, this report |
| Frontend `KM-Inc/screenshot-to-sight-3914` | `feat/f4-forms-pdf` | `f4193b6` | `src/features/vault/VaultPanel.tsx`, `src/test/vault-panel.test.tsx` |

Correction to the brief: backend `main` is at `94882e2` (identical to
`feat/f3-f4-integration`), not `55083a7`.

## Verdict

**The prototype is functionally complete through F4 on a local development stack.**
Both repositories were run together as one application and every item of the
acceptance list has now been exercised in a real browser or over live HTTP, with
the exceptions listed under "Still not verified".

It is **not production-ready**: every external decision (publisher key, storage/CDN,
CORS origins, secrets, SMS provider) is open, no real government template exists,
and nothing has been verified outside one developer machine. eSign stays disabled.
No DPDP compliance is claimed.

## How to read the evidence

- **Unit** — automated test of one module.
- **Integration** — automated test through the real HTTP app on PostgreSQL 16.
- **Live HTTP** — scripted calls against the running backend.
- **Browser** — the real React build driving the real FastAPI backend.
- **Inspected** — I looked at the artefact itself (PDF, storage, logs, database).

## Sessions

| When | What |
|---|---|
| 2026-11-10/11 | First full browser run; four defects found and fixed. `docs/E2E_VERIFICATION.md`. |
| 2026-11-12 | Environment rebuilt from the guide; all gates re-run; PDFs inspected; admin workflow over live HTTP; token refresh, hostile-server cases, coordinate template, cross-field rule, real export and passphrase change in the browser. |
| 2026-11-13 | Environment rebuilt again; auto-lock (real 10 minutes), local deletion, vault deletion and account deletion in the browser; final gates. |

The local database and browser profile were wiped between sessions, so the guide
(`docs/LOCAL_DEVELOPMENT.md`) was followed from an empty state three times and
worked each time without edits.

## Defects found in this acceptance pass

| # | Defect | Root cause | Fix | Regression test |
|---|---|---|---|---|
| 1 | Passphrase change and vault deletion could not be done by a user | The vault engine implemented both, but no screen exposed them | Frontend: both added to the vault panel shown in Settings; deletion requires typing `DELETE` | 4 new tests (`vault-panel.test.tsx`): success, wrong/weak/mismatched/unchanged passphrase, atomic failure, confirmed deletion |
| 2 | Backend tests could change outcome depending on a developer's local `.env` | Test config pinned keys and origins but not token lifetime or OTP limits | Backend: test config pins every limit the tests assert on | The suite itself: it failed 2 tests with a tuned `.env` and passes with the fix |

Neither fix sends data anywhere new. Defect 2 was exposed by my own temporary
acceptance settings (1-minute access tokens), which have been removed.

One minor observation, not fixed: on the account-deletion form, submitting a
malformed mobile number does nothing and shows no message.

## 1. Integration environment

| Item | Result | Evidence |
|---|---|---|
| Fresh PostgreSQL 16.14, `gov_forms_dev` + `gov_forms_test` | Pass | Inspected |
| `make_dev_env` (stable dev keys, frontend pin) | Pass | Inspected |
| `alembic upgrade head` → `0003 (head)` on an empty database | Pass | Integration |
| `seed_dev` → 3 test templates through the real maker-checker chain | Pass | Integration |
| FastAPI `127.0.0.1:8000`, React `localhost:8080`, CDN stand-in `127.0.0.1:8001` | Pass | Browser |
| Explicit dev CORS origins, no credentials | Pass | Browser + Integration |
| Test-only signing key, fictional data | Pass | Inspected |

## 2. Authentication

| Check | Actual result | Evidence |
|---|---|---|
| OTP request | `POST /auth/otp/request` → 202 | Browser |
| OTP verification, wrong code | 401; "That code is incorrect or has expired. 2 attempts left." | Browser |
| OTP verification, right code | 200; redirected to the page originally requested | Browser |
| Access token use | Bearer sent only on protected calls | Browser + Unit |
| Refresh in the browser | With 1-minute tokens the client called `POST /auth/token/refresh` → 200 and retried the request → 200 without signing the user out; the session then survived a further 10 minutes | Browser |
| Refresh token rotation | 200, new token differs | Live HTTP |
| Reuse of a rotated refresh token | 401; the whole session family revoked | Live HTTP |
| Session listing and revocation | 3 listed; 3 → 2 after revoking | Browser |
| Sign-out | Session revoked; token dropped; vault locked | Browser + Inspected |
| Protected route access | Redirect to login when signed out; no/garbage bearer → 401 | Browser + Live HTTP |
| Replayed OTP; rapid OTP requests | 409; 202, 429, 429 | Live HTTP |
| Account deletion | Fresh code required; `DELETE /auth/account` → 200; "Your account has been deleted."; database row `DELETED`, mobile index removed, 0 active sessions, audit event written | Browser + Inspected |

## 3. Template publication and verification

Admin workflow over live HTTP with four signed-in test accounts (curator, verifier,
approver, citizen); roles were assigned directly in the dev database because there
is no admin sign-in UI.

| Check | Actual result |
|---|---|
| Citizen / anonymous creates template | 403 / 401 |
| Citizen / anonymous uploads a version | 403 / 401 |
| Upload: pre-contract manifest | 422 `schema_version_unsupported` |
| Upload: identity mismatch (slug) | 422 `identity_mismatch` |
| Upload: manifest names a different PDF | 422 `identity_mismatch` |
| Upload: unknown field type | 422 `field_type_unsupported` |
| Upload: PDF with active content; not a PDF | 422 `PDF_VALIDATION_FAILED` (both) |
| Upload: valid PDF + manifest | 201, `DRAFT`, v2 digest, unsigned |
| Public status before publication | `NEVER_PUBLISHED`; not in catalogue |
| Creator verifies own version | 403 |
| Transition with wrong bundle hash | 409 |
| Verifier approves; publish skipping approval | 403; 409 |
| Curator → verifier → approver → publish | 200 at each step; signed with the dev key |
| After publication | `PUBLISHED`; listed in catalogue; bundle `signature_verified: true` |
| Duplicate semver | 409 |

That template then appeared in the browser catalogue and was used for the
hostile-server tests below.

Client-side verification in the browser, with the server's responses altered in
transit:

| Tampering | Browser result | Downloaded? | Cached? |
|---|---|---|---|
| Manifest label changed | "mapping does not match its published checksum…" | No | No |
| Unknown signing key id | "publisher signature could not be verified" | No | No |
| Unknown key id **and** server claims `signature_verified: true` | Same rejection — the server's flag is not trusted | No | No |
| Signature replaced | "publisher signature could not be verified" | No | No |
| Semver changed | "bundle is incomplete or does not match…" | No | No |
| Page count changed (digest no longer matches) | Same | No | No |
| Download host changed | "download address is not approved" | No | No |
| One bit flipped in the PDF | "does not match its published checksum" | Yes | No |
| Untampered | "Checks passed and the blank template is saved encrypted…" | Yes | Yes |

| Other checks | Result | Evidence |
|---|---|---|
| Publisher key discovery, disjoint from entitlement keys | Pass | Integration |
| v2 signature verified by the frontend with the pinned key | Pass | Browser |
| v1 signature | Pass | Integration (backend legacy row) + Unit (frontend, shared vectors) |
| Status endpoint, all five states | Pass | Integration |
| Withdrawal seen by the client; draft not migrated; acknowledgement required | Pass | Browser |

## 4. Vault and local data

| Check | Result | Evidence |
|---|---|---|
| Vault creation (Argon2id in the real browser) | Pass | Browser |
| Unlock; wrong passphrase rejected; lock | Pass | Browser |
| Passphrase change | Pass after fix 1 — "Everything on this device was re-encrypted"; old passphrase rejected, new one opens all 8 records | Browser + Unit |
| Passphrase change is atomic | Pass | Unit |
| Automatic lock on page hide | Pass — form and answers removed from the page | Browser |
| Automatic lock on inactivity | Pass — locked at **600 s** of real inactivity; form and answers removed | Browser |
| Encrypted profile and draft persistence | Pass | Browser + Inspected |
| Reload and resume | Pass | Browser |
| Failed-save recovery | Pass | Unit (form page with a real vault) |
| Delete draft / profile / saved template | Pass — each record gone from IndexedDB | Browser + Inspected |
| Delete vault | Pass after fix 1 — disabled until `DELETE` typed; store emptied; account unaffected | Browser + Inspected |
| Storage inspection | All records ciphertext; plaintext probes not found; `localStorage` empty; no cookies | Inspected |

## 5. Complete F4 workflow

Run in the browser on the fillable template (11-10/11) and on the
coordinate-mapped template (11-12).

| # | Step | Result |
|---|---|---|
| 1–3 | Sign in, browse, open a published test template | Pass |
| 4–7 | Verify bundle, download via CDN stand-in, checksum, save encrypted | Pass |
| 8–10 | Profile, new draft, select profile | Pass |
| 11 | No overwrite without confirmation | Pass |
| 12 | All field types: text, multiline, number, date, select, radio, checkbox, address | Pass |
| 13 | Required fields block the review step | Pass |
| 14 | Conditional visibility | Pass |
| 15 | Cross-field validation | Pass — "The declaration date cannot be before the date of birth."; also "The date cannot be in the future." |
| 16–18 | Section navigation, progress, autosave | Pass |
| 19–24 | Navigate away, lock, reload, unlock, resume, answers intact | Pass |
| 25–26 | Review, generate PDF | Pass |
| 27 | Preview every page | Pass — both pages of both templates, plus zoom |
| 28 | Mapped values and formatting | Pass — section 6 |
| 29 | Export | Pass — a real file was written to the Downloads folder |
| 30 | Open the exported PDF and verify contents | Pass — that file was read with an independent PDF library |

## 6. PDF correctness

Independent reader: `pypdf` in strict mode. Visual check: rendered pages and the
in-app preview.

Exported file from the browser
(`test-coordinate-form-1.0.0-Coordinate-acceptance-draft.pdf`, 2,773 bytes, 2 pages):
name, date `05.01.2026`, count, the four address lines, PIN code on page 1; the
two-line note on page 2. All match what was typed.

Fillable template, values read back from the PDF's form fields:

| PDF field | Value |
|---|---|
| `applicant.full_name` | `ASHA TEST` (uppercase transform) |
| `applicant.dob` | `28/02/1990` |
| `applicant.contact_method` | option "phone" |
| `applicant.mobile` | `9876543210` |
| `address.full` | three lines |
| `address.pincode` | `560001` |
| `details.service_type` | `PRIORITY` |
| `details.fee_waiver`, `declaration.agree` | checked |
| `details.dependants` | `2` |
| `details.remarks` | two lines |
| `declaration.date` | `10-11-2026` |

| Check | Result | Evidence |
|---|---|---|
| All mapped fields populate | Pass | Inspected + Unit |
| No unexpected truncation; overflow fails generation | Pass | Inspected + Unit |
| Multiline values | Pass | Inspected + Browser |
| Date formatting (three formats) | Pass | Inspected |
| Checkboxes, radio, coordinate marks | Pass | Inspected + Browser |
| Text positioning inside each printed box; right-aligned number | Pass | Inspected |
| Original blank PDF unchanged (SHA-256 before = after) | Pass | Unit + Inspected |
| Unsupported mappings fail clearly | Pass | Unit |
| Output valid and readable, page sizes preserved | Pass | Inspected |

Limitation: text outside the standard PDF fonts (for example Devanagari) is refused
with a clear message rather than printed. A real gap for Indian-language forms.

## 7. Privacy

| Check | Result | Evidence |
|---|---|---|
| No profiles, answers, passphrases, keys or completed PDFs reach the backend | Pass | Inspected (network log, DB dump, route inventory) |
| Requests during unlock → edit → save → generate → preview → export | Only auth calls and one public status `GET` | Inspected |
| Blank-PDF request | `credentials: "omit"`, no headers at all (so no `Authorization`), `redirect: "error"`, `no-referrer`, no body; the page has no cookies | Browser (request captured) |
| Database after the runs | None of the entered values; no mobile number | Inspected |
| Backend logs | Paths and status codes; the development OTP line is the one deliberate exception and is inert in production | Inspected |
| No plaintext in IndexedDB | Pass | Inspected |
| Citizen routes reject multipart, binaries, data URIs, oversized bodies | Pass | Integration |

## 8. Regression results (final run, 2026-11-13)

Backend, from `backend/`:

| Command | Result |
|---|---|
| `TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/gov_forms_test python -m pytest -q` | **258 passed, 2 skipped, 0 failed** |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 102 files already formatted |
| `mypy app scripts` | no issues in 62 source files |
| `alembic downgrade base` then `upgrade head` (11-12) | 3 down, 3 up, `0003 (head)` |
| `build_contract_fixtures --check`, `export_openapi --check`, `sync_contracts --check` (11-12) | all up to date |

The 2 skips are parametrised cases for two routes tested under their own profile.

Frontend, from `frontend/screenshot-to-sight-3914/`:

| Command | Result |
|---|---|
| `npx vitest run` | **369 passed, 0 failed** (24 files; 365 before, +4 for fix 1) |
| `npx tsc --noEmit` | exit 0 |
| `npx eslint .` | 0 errors, 9 warnings (7 pre-existing) |
| `npx vite build` | exit 0 |
| Browser tests | The runs described above; there is no scripted browser suite in the repository |

No existing test was removed, weakened or skipped.

## 9. API contract mismatches

None found. The frontend checks its response validators, request bodies, paths and
auth requirements against the backend's exported OpenAPI document, and the backend
pins that document to the live app. Every live call in these runs returned the
status and shape the other side expected.

## Files changed and commits

Uncommitted, from this pass:

- Frontend: `src/features/vault/VaultPanel.tsx` (passphrase change, vault deletion),
  `src/test/vault-panel.test.tsx` (new).
- Backend: `backend/tests/conftest.py` (pins limits and lifetimes).
- Docs: `docs/F4_ACCEPTANCE_REPORT.md` (this file).

No commits were created in this pass. Existing: backend `55083a7`, `94882e2`;
frontend `f4193b6`.

Left on this machine by the run: one exported test PDF in the Downloads folder
(`test-coordinate-form-1.0.0-Coordinate-acceptance-draft.pdf`, fictional data).
The temporary settings added to the git-ignored `backend/.env` were removed.

## Still not verified

- **v1 signature in the browser.** No v1 template can be published by the current
  backend (new publications are always v2), so this path is covered by shared test
  vectors and a backend legacy-row test only.
- **Failed-save recovery in the browser.** Needs a storage failure; unit-tested with
  a real vault.
- **Admin screens.** None exist; the admin workflow was driven over HTTP.
- **Repeatability.** The browser runs were driven interactively. A scripted browser
  suite would make them repeatable by command.
- **Anything beyond one machine**: real HTTPS CDN, staging, multiple instances,
  other browsers, mobile.

## Outstanding blockers (need a decision, not code)

See `docs/DECISION_REGISTER.md`:

- DEC-1 production publisher key — production authenticity and download stay disabled.
- DEC-2 key-compromise procedure — draft runbook only, never rehearsed.
- DEC-3 storage/CDN provider — production adapter not written.
- Production CORS origins, stable shared secrets, SMS provider.
- Refresh-token storage: a page reload signs the user out.

Product gaps: no real government template, no Unicode font support for coordinate
printing, no admin UI, no wallet or payment screens.

## Status by acceptance area

| Area | Unit | Integration / Live HTTP | Browser | Inspected |
|---|---|---|---|---|
| Authentication | Yes | Yes | Yes, including refresh and account deletion | Logs, database |
| Template publication | Yes | Yes, full workflow over live HTTP | Result visible in catalogue | — |
| Template verification | Yes | Yes | Yes, including 8 hostile-server cases | — |
| Vault | Yes | n/a | Yes, including passphrase change, both auto-locks, deletion | Storage |
| Profiles and drafts | Yes | n/a | Yes, including deletion | Storage |
| Form engine | Yes | n/a | Yes, including cross-field rule | — |
| PDF generation | Yes | n/a | Yes, both templates | Both templates |
| Preview and export | Partly | n/a | Yes, real file exported | Exported file |
| Privacy | Yes | Yes | Yes | Network, database, storage |
