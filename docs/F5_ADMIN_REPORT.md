# F5 — Internal Admin Template Management: Report

Date: 2026-11-13. Baseline: backend `main` `947a800`, frontend `feat/f4-forms-pdf` `0c01223`.
Status: **implemented and verified on the local stack. Not committed.**

All templates used here are fictional, clearly labelled TEST FIXTURES. No government
form was invented or presented as official.

## 1. Existing APIs reused (unchanged contracts)

| Route | Use in F5 |
|---|---|
| `POST /admin/domains` (super admin) | Approve a source domain (Sources & domains page) |
| `POST /admin/sources` | Register a government source |
| `POST /admin/templates` | Create a template |
| `POST /admin/templates/{id}/versions` (multipart) | Upload a blank PDF + manifest as a DRAFT |
| `POST /admin/templates/{id}/versions/{vid}/transitions` | Submit, verify, approve, publish, reject (→ DRAFT), withdraw |
| `GET /templates/sources` (public) | Source picker |
| `GET /templates/{slug}/versions/{semver}/status` (public) | Confirms withdrawal for citizens |

The maker-checker rules, approved-domain DB triggers, published-immutability trigger,
append-only approval and audit logs, PDF validator and manifest contract were reused
as they were. Nothing was duplicated.

## 2. New APIs (all role-gated; citizens get 403, anonymous 401)

| Route | Roles | Purpose |
|---|---|---|
| `GET /admin/me` | any admin | Role and permissions; the console uses it as its access check |
| `GET /admin/domains` | any admin | List approved domains |
| `GET /admin/templates` | any admin | All templates in every state; filters `q`, `status`, `jurisdiction`, `category`, paging |
| `GET /admin/templates/{id}` | any admin | Detail, source, versions, `titles_editable`, `identity_editable` |
| `POST /admin/templates/{id}/metadata` | maker | Edit before publication (rules below) |
| `POST /admin/templates/{id}/versions/validate` (multipart) | maker | Dry run of every upload check; stores nothing; returns PDF inspection and all issues |
| `GET /admin/templates/{id}/versions/{vid}` | any admin | Version, manifest, signature check, allowed actions with reasons, approval history, audit events |
| `GET /admin/templates/{id}/versions/{vid}/blank-pdf` | any admin | Blank PDF for reviewer preview (`no-store`, checksum re-verified) |
| `POST /admin/templates/{id}/versions/{vid}/artifacts` (multipart) | the version's maker | Replace a DRAFT's PDF/manifest after rejection |

Additive changes to existing responses: `AuthTokensResponse.role` (display only);
rejected manifests return `errors: [{field, code, message}]` in the problem document.

Behaviour changes, each covered by tests:

- **Withdrawal requires `deprecation_reason`.** A named `replaced_by_template_slug`
  must exist and be a different template.
- **Publishing a version withdraws the template's previous published version**
  (reason "Superseded by version x.y.z"), so only one version is ever PUBLISHED and
  clients checking an older version learn it is no longer current. Before this, an older
  version stayed PUBLISHED but vanished from the catalogue.
- **`source_downloaded_at` must carry a timezone and not be in the future.**

Metadata rules: titles and descriptions can change until the first publication; form
code, category and source only while no version exists (they are bound into every
version's signed digest and manifest); slug and jurisdiction never.

Rework rules: only a DRAFT, only by the admin who created the version (so maker-checker
separation still describes who authored the content), version number fixed, every
upload check re-run, a new digest computed (a reviewer screen holding the old digest
can no longer advance it).

## 3. Quality checks (actionable messages; dry run and upload agree)

| Check | Code(s) |
|---|---|
| Manifest schema and supported field/mapping types | contract codes, e.g. `schema_version_unsupported`, `field_type_unsupported` |
| Template identity (slug, code, category, semver, page count, PDF hash) | `identity_mismatch` |
| PDF safety, structure, page count | `pdf_invalid` |
| PDF field names, kinds, options | `pdf_field_missing`, `pdf_field_type_mismatch`, `pdf_option_missing`, `pdf_field_reused` |
| Coordinate bounds and rotated pages | `pdf_rect_out_of_bounds`, `pdf_page_rotated`, `pdf_page_out_of_range` |
| Required/conditional field definitions, rules | `condition_invalid`, `rule_invalid`, … |
| Official source URL on an approved domain | `source_url_not_approved`; warning `source_domain_differs` |
| Download time | `source_downloaded_at_invalid` |
| Duplicate versions | `semver_exists`, `semver_invalid`, `semver_fixed`; warnings `semver_not_increasing`, `duplicate_content` |
| Signature generation | re-derived digest before signing; `signature_verified` on the version |
| Publication immutability | DB trigger; rework and title edits refused after publication |

Each issue carries a path (e.g. `manifest.sections[0].fields[0].pdf[0].field_name`) and a
plain message (e.g. "PDF has no field 'applicant.fullname'"). The validation report also
lists every fillable field found in the PDF, to help authors map fields.

## 4. Frontend admin console (`/admin`)

Separate route tree (`src/routes/_authenticated/admin`) and feature folder
(`src/features/admin`); it never imports the vault or local-data modules. An "Admin"
header link appears only for admin roles; access is decided by `GET /admin/me`.

- **Overview:** counts per lifecycle stage, with the signed-in role's queue highlighted.
- **Templates:** list with search and status/jurisdiction/category filters.
- **New template:** source, slug (validated), form code, category, titles, description.
- **Template detail:** metadata editing within the rules above; version history.
- **Upload / rework:** PDF, provenance (official URL, approved domain, download time),
  manifest editor with file load, live in-browser contract check, "Insert identity
  block" from the server's report, server validation report, upload enabled only for
  the exact inputs that last validated cleanly.
- **Version review:** preview of the blank PDF and of the PDF filled with fictional
  sample values in the browser (fill problems listed instead of a misleading PDF);
  field/mapping table; raw manifest; action buttons with the reason when not allowed;
  confirmation dialogs requiring notes (and a citizen-facing reason for withdrawal);
  integrity panel; approval history and audit log.
- **Sources & domains:** list and register sources (maker), approve domains (super admin).

## 5. Tests

Backend, from `backend/`, PostgreSQL 16.14:

| Command | Result |
|---|---|
| `TEST_DATABASE_URL=postgresql+asyncpg://postgres@127.0.0.1:55432/gov_forms_test python -m pytest -q` | **277 passed, 4 skipped** (was 258 + 2) |
| `python -m pytest -q tests/integration/test_admin_console.py` | 18 passed |
| `ruff check .` / `ruff format --check .` / `mypy app scripts` | clean |
| `alembic downgrade base` → `upgrade head` | 3 down, 3 up |
| `build_contract_fixtures --check`, `export_openapi --check`, `sync_contracts --check` | up to date |

The 2 new skips are the pinned privacy test's parametrised cases for the two new upload
routes, which are covered by their own profile. New tests cover admin access control,
maker-checker separation and explained actions, invalid uploads and manifests, wrong
PDF mappings, duplicate versions, rejection and rework, publication and supersession,
withdrawal rules, immutability, append-only history and audit metadata (field names
only, never values).

Existing tests changed, deliberately:
- `test_f3_templates.py`: registers the replacement template before naming it in a
  withdrawal (new rule); assertions unchanged.
- `test_privacy_surface.py`: the pinned route inventory now lists the nine reviewed
  admin routes and the three upload routes. This file exists to force that review.

Frontend, from `frontend/screenshot-to-sight-3914/`:

| Command | Result |
|---|---|
| `npx vitest run` | **426 passed** (was 369) |
| `npx tsc --noEmit` | exit 0 |
| `npx eslint .` | 0 errors, 9 warnings (unchanged from baseline) |
| `npx prettier --check src` | clean |
| `npx vite build` | exit 0 |

New tests: sample preview for every fixture, manifest editor helpers, the admin upload
transport (admin-only paths, bearer, no cookies, single refresh, size cap, structured
errors), access screen, header link by role, review actions and dialogs, withdrawal,
validated-only upload and rework. The OpenAPI contract test now checks all 15 admin
calls and 11 admin response models, and asserts every `/admin` route requires a bearer
token. Its "no route may carry applicant data" check now exempts `/admin` routes (they
carry blank templates) instead of one hard-coded path.

## 6. Browser verification (real React app + real FastAPI + PostgreSQL 16)

Four fictional accounts; roles granted with the dev-only `scripts/grant_role.py`.

| Step | Account | Result |
|---|---|---|
| Open `/admin` | citizen | "Admin access required"; no Admin link; `GET /admin/me` → 403 |
| Overview | maker (super admin) | counts per stage |
| Create template, invalid slug first | maker | slug error, then created |
| Edit form code and title (no versions yet) | maker | saved |
| Upload: validate fixture manifest | maker | 2 errors `identity_mismatch`; 12 PDF fields listed; upload disabled |
| Insert identity block; break one field name | maker | `pdf_field_missing` with path and message; stale-inputs note shown |
| Fix and validate | maker | "All checks passed"; upload as draft |
| Review page | maker | sample-filled preview renders with values in their boxes |
| Submit for review | maker | notes required; then Verify disabled: "You created this version, so someone else must verify it" |
| Direct API call to verify own version | maker | 403 "Creator cannot verify their own template version" |
| Reject with reason | checker | back to Draft; history records the reason |
| Rework | maker | **defect found** (below); after fix: validate clean, files replaced, resubmitted |
| Verify | checker | Approve/Reject then disabled for the checker's role, with reason |
| Approve, publish | approver | publish dialog explains signing and supersession; "Publisher signature verifies" |
| Citizen catalogue + client verification | — | listed with the edited title; frontend digest, pinned-key signature and manifest checks pass |
| Withdraw | approver | blocked until a reason is entered; then status DEPRECATED with reason and replacement; removed from catalogue; "Withdrawn versions are final" |
| History | any admin | 9 approval entries, 9 audit events, roles and notes |

No server errors were logged. The admin console made no request that carries applicant
data; it has no access to the vault.

Not exercised in the browser: post-publication title edit and rework by the maker
(the approver's attempts were refused on role, the immutability rules are covered by
integration tests), supersession by publishing a second version, the Sources & domains
forms, and staging or production.

## 7. Defect found and fixed during the browser run

**Rework could never proceed.** The rework screen ran the dry-run validation as if for a
new version, so the draft's own version number was reported as `semver_exists` and the
replace button stayed disabled. Fix: the validate endpoint accepts
`replace_version_id` (DRAFT only, version number fixed); the frontend sends it in rework
mode. Regression tests in both repositories; re-verified in the browser.

## 8. Security findings

- **Fixed:** the rework defect above, and the lifecycle gap where an older version stayed
  PUBLISHED after a newer one was published.
- **Added controls:** pinned route inventory updated deliberately; only three routes accept
  file bytes; admin uploads capped at 10 MB client-side as well as server-side; reviewer
  PDF downloads are `no-store` and checksum-verified before serving; audit metadata for
  metadata edits records field names, never values.
- **Residual risks:**
  - Admin roles have no provisioning workflow or periodic review (DEC-8).
  - Admins authenticate with the same OTP flow as citizens; there is no second factor
    and no separate admin session policy.
  - The in-process rate limiter is per instance, and the 5/hour/network OTP limit also
    throttles admins on a shared office network.
  - The admin console runs in the same origin as the citizen app.
  - Server `detail` text is shown in the admin console only.

## 9. Remaining production decisions

Unchanged: DEC-1 publisher key, DEC-2 compromise procedure, DEC-3 storage/CDN, CORS
origins, stable secrets, SMS provider, refresh-token storage, pricing, eSign (disabled),
legal review. New: **DEC-8 admin role provisioning and review**. Also open: which real
government forms to onboard first, and who authors and reviews their manifests.

## 10. Files changed

Backend (parent repo): `app/core/errors.py`, `app/middleware/privacy_boundary.py`,
`app/modules/audit/schemas.py`, `app/modules/auth/{router,schemas}.py`,
`app/modules/templates/{manifest_contract,router,schemas,service}.py`, new
`app/modules/templates/admin_service.py`, new `scripts/grant_role.py`, new
`tests/integration/test_admin_console.py`, `tests/integration/test_f3_templates.py`,
`tests/security/test_privacy_surface.py`; docs `DECISION_REGISTER.md`,
`LOCAL_DEVELOPMENT.md`, `contracts/openapi.json`, this report.

Frontend: new `src/features/admin/*`, `src/routes/_authenticated/admin/*`,
`src/test/admin/*`; changed `AppShell.tsx`, `features/auth/{api,session}.ts`,
`features/forms/PdfPreview.tsx`, `services/api/{client,errors}.ts`, `routeTree.gen.ts`
(generated), `test/openapi-contract.test.ts`, `contracts/openapi.json`,
`docs/backend/openapi.json`, `AGENTS.md`, `README.md`.

No migration was needed. No dependency was added.
