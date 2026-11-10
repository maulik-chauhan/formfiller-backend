# End-to-end browser verification (2026-11-10 / 11)

Real React dev build (`http://localhost:8080`) against the real FastAPI backend
(`127.0.0.1:8000`, PostgreSQL 16.14) and the development CDN stand-in
(`127.0.0.1:8001`), driven through an automated browser. Test OTPs came from the
development console provider. All data was fictional; the publisher key was a
development key generated on the test machine and pinned in `.env.local`.

| # | Scenario | Outcome |
|---|---|---|
| 1 | Sign in with OTP | Pass. Wrong code first: "incorrect or has expired. 2 attempts left" |
| 2 | Browse templates | Pass — 3 seeded test templates listed from the API over CORS |
| 3 | Open a template | Pass — detail incl. `template_id`, version id, PDF SHA-256 |
| 4–5 | Bundle metadata + publisher signature (pinned dev key, v2) | Pass |
| 6–7 | Download blank PDF from the approved (dev) host; checksum | Pass — `GET …/blank_pdfs/<sha>.pdf` 200, hash matched |
| 8 | Encrypt and cache the template | Pass — "saved encrypted on this device only" |
| 9 | Create vault | Pass |
| 10 | Create applicant profile (6 details) | Pass |
| 11–12 | Create draft, select profile | Pass — lands on the form, draft pinned to template/version ids |
| 13 | Fill answers; profile reuse | Pass — conflict dialog before replacing "Full name"; date converted; address filled |
| — | Conditional field | Pass — "Mobile number" appears only for "Phone"; "Dependants" only with fee waiver |
| — | Validation | Pass — "Enter a 10-digit mobile number.", "Enter 20 or less.", required-field gate on review |
| 14 | Save draft | Pass — autosave, "Saved hh:mm:ss" |
| 15–16 | Navigate away; lock vault | Pass — drafts hidden while locked |
| — | Browser storage inspection while locked | Pass — 6 IndexedDB records, all ciphertext; none of 9 plaintext probes found; `localStorage` empty; `sessionStorage` holds only router scroll state; no cookies |
| 17–18 | Reload; sign in; unlock | Pass — reload ends the memory-only session, redirect back after sign-in; wrong passphrase rejected first |
| 19 | Resume draft | Pass — all saved answers restored |
| 20–21 | Edit and validate | Pass |
| 22 | Generate completed PDF locally | Pass (after fix, see below) |
| 23 | Preview: page navigation and zoom | Pass — page 1 → 2 of 2, 100% → 125% |
| 24 | Export | Pass up to the browser download trigger: a `blob:` URL with a 15.7 KB `%PDF-` file named `test-fillable-form-1.0.0-E2E-test-draft.pdf`. The automated run intercepted the anchor click to inspect the bytes instead of writing into the Downloads folder. |
| 25 | Nothing sensitive reached the backend | Pass — during unlock, resume, edit, save, generate, preview and export the only requests were OTP request/verify and one public `…/status` GET. A full `pg_dump` of the dev database contains none of the entered values, nor the mobile number. |
| — | Template withdrawn server-side | Pass — Saved templates and My drafts show "Deprecated"; draft not migrated; PDF creation requires explicit acknowledgement |
| — | Session list and revocation | Pass — 3 sessions listed, one revoked (→ 2) |
| — | Sign-out | Pass — backend session revoked, token dropped, vault locked |

Live API checks (same backend, scripted): refresh-token rotation 200; reuse of a
rotated refresh token 401 and the whole session family revoked; missing/garbage bearer
401; bundle without auth 401; replayed OTP 409; rapid OTP requests 202, 429, 429.

## Defects found by this run and fixed

1. Multi-line PDF text boxes rendered one oversized, clipped line (the box declared a
   66 pt font). The fill engine now lays out multi-line boxes itself and fails with
   `text_overflow` if the text cannot fit. Regression test added.
2. Leaving the page while a preview was open crashed the app (wrong pdf.js cleanup
   call). Fixed.
3. After unlocking, drafts showed "Unconfirmed" until the page was revisited because
   the status check finished after the first read. Views now refresh when it completes.
4. The development OTP was never printed (application loggers had no handler).
   Development-only logging added.

## Not exercised in the browser

Account deletion, coordinate-mapped template (covered by automated tests only),
payments/entitlements UI (none exists yet), inactivity auto-lock timing (unit-tested),
a real HTTPS CDN, and any staging or production environment.
