# CMV Form 1 — Application-cum-declaration as to physical fitness (onboarding record)

Status: **technically ready for local admin onboarding; NOT cleared for non-local
publication** (reuse permission outstanding — DEC-10). Not published. No production
signature.

## 1. Official source verification

| Item | Value |
|---|---|
| Rule authority | Central Motor Vehicles Rules, 1989 — form header "FORM 1 [See Rule 5(2)]" |
| Publishing authority of this file | Transport Department, Government of Karnataka (state-hosted copy of a Central-rule form) |
| Jurisdiction recorded | `KARNATAKA_STATE` (the source is the Karnataka department's site) |
| Listing page | https://transport.karnataka.gov.in/58/driving-license-related-forms/en (row "Application-cum-decalration as to physical fitness … C.M.V Form 1", listed as English, 23.5 KB) |
| PDF URL | https://transport.karnataka.gov.in/storage/pdf-files/Forms%20related%20to%20driving%20license%20in%20English/form%201.pdf |
| First retrieved | 2026-11-13 (downloaded with the project owner's approval) |
| Re-verified | 2026-11-14, in the browser from the official URL: HTTP 200, `application/pdf`, 24,522 bytes, SHA-256 identical |
| Revision evidence | Server `Last-Modified: Tue, 14 Jan 2020 09:15:32 GMT`; PDF CreationDate 2018-01-30 (Online2PDF). No edition date on the form. |
| Currency | **Unverified.** No Central Gazette / MoRTH / Parivahan copy of Form 1 was located to compare against; other state transport sites host their own copies, which are not authoritative for this check. |
| Reuse / publication terms | Same site policy as KMV 12: republication **only with prior permission** by email (placeholder address). **No permission has been requested or obtained.** |

## 2. PDF inspection (original, unmodified)

| Item | Result |
|---|---|
| SHA-256 | `c936da8e47f9e913961ca3566ced4ec24713d8c64cd553f6cfb26df380f1682c` |
| Header / pages | `%PDF-1.4`, 1 page, A4 596.04 × 842.04 pt, CropBox = MediaBox, rotation 0 |
| Metadata | Creator/Producer Online2PDF.com, created 2018-01-30; no XMP |
| Revisions | one `%%EOF`; not encrypted; 23 objects, none compressed |
| Text layer | yes (3,338 characters) — not a scan; 0 images |
| Fonts | embedded subsets: Times-Roman and Times-Bold (Type1, WinAnsi), TimesNewRoman CID (Identity-H) |
| AcroForm / XFA | none / none |
| Annotations | none |
| Actions | only catalog `/OpenAction [page 0 /FitH null]` — a view destination ("open at fit width"), accepted under DEC-9. No JavaScript, Launch, SubmitForm, ImportData, URI, GoToR, GoToE, Named, RichMedia or other action; no `/AA` |
| Embedded files | none |
| Security validator (`validate_blank_pdf`, backend `e174b6e`) | **PASS** (would have been rejected before DEC-9) |
| Suitable for local filling | yes — coordinate mapping on page 0 |

## 3. Proposed manifest (`manifest.json`, contract v1)

- **Identity:** `cmv-form-1-physical-fitness`, `KARNATAKA_STATE`, `TRANSPORT`, `CMV-FORM-1`, 1.0.0, 1 page.
- **Size:** 15 fields, 22 mappings.
- **Manifest SHA-256:** `1d276c32b4bbbf369b86cd49e423272fa2b1c5aef545d8fc863ab10d46830735`

| # on form | Field id | Type | Required | Mapping (page 0, pt) | Validation and why |
|---|---|---|---|---|---|
| 1 | `applicant_name` | text | yes | text_at x234 y715 w316, 10 pt | max 55 (print space) |
| 2 | `relation_name` (Son/Wife/Daughter of) | text | yes | x234 y694 w316 | max 55 |
| 3 | `permanent_address` | multiline | yes | x234 y608 w316 h76, 9 pt, line height 19 = the form's four dotted lines | max 200 (print space) |
| 4 | `temporary_address` ("Temporary address / Official address (if any)") | multiline | **no** ("if any") | x234 y534 w316 h76, four lines | max 200 |
| 5(a) | `date_of_birth` | date `DD/MM/YYYY` | yes | x234 y525 w110 | `not_future` (a birth date cannot be in the future) |
| 5(b) | `age_on_application` | number | yes | x234 y512 w60 | 0–999 (non-negative; 3-digit print space). Entered by the applicant, not computed. |
| 6(1) | `identification_mark_1` | text | yes | x234 y490 w310, 9 pt | max 60 |
| 6(2) | `identification_mark_2` | text | yes | x234 y454 w310, 9 pt | max 60 |
| (a)–(g) | `q_a_epilepsy`, `q_b_eyesight`, `q_c_limbs`, `q_d_colours`, `q_e_night_blindness`, `q_f_hearing`, `q_g_other` | radio `yes` / `no` | yes | two `mark_at` crosses (size 12) per question: "yes" strikes out "No", "no" strikes out "Yes" | — |

Strike-out mark positions (page 0, mark_at x/y):

| Question | "yes" mark (strikes "No") | "no" mark (strikes "Yes") |
|---|---|---|
| (a) | 544 / 388 | 528 / 388 |
| (b) | 543 / 324 | 526 / 324 |
| (c) | 547 / 294 | 530 / 294 |
| (d) | 547 / 280 | 530 / 280 |
| (e) | 547 / 267 | 530 / 267 |
| (f) | 544 / 240 | 527 / 240 |
| (g) | 544 / 213 | 527 / 213 |

- **Question labels** use the form's own wording. Question (b) exceeds the 200-character label limit, so it continues in its help text.
- **Required-status rule:** an item printed without "(if any)" is required, and the form's "Yes/No" questions must each be answered.
- **Removed as not stated on the form:** name patterns, the earlier age range of 1–120, and a structured address with a compulsory PIN code.

**Validation results:**
- Backend `assert_valid_manifest` against the real PDF: **0 issues**.
- Frontend `parseFormManifest` and `fillPdf`: the all-No set and the all-Yes / maximum-length set (200-character address) fill with **0 problems**; rendered output inspected; addresses sit on the dotted lines.

## 4. Unsupported fields and known limitations

- **Not mapped:** signature or thumb impression of the applicant.
- **No space on the form:**
  - details for a "Yes" to (g);
  - the further particulars Note (1) asks for after certain answers.

  The help text on (g) says to give these separately.
- **Closing declaration:** it has no box, so it is affirmed by signature and not mapped.
- **Form 1A:** the medical certificate in Form 1A (Note 2) is a separate form and is not covered.
- **Age:** not cross-checked against the date of birth (contract v1 has no computed rules).
- **Identification marks:** the form prints "(1)" on a row without dots and "(2)" beside a dotted line. Mark (1) is placed on the "(1)" row and mark (2) on the line beside "(2)". This layout is ambiguous and should be reviewed by the checker.
- **Slug:** `cmv-form-1-physical-fitness` has no state prefix. Consider `ka-cmv-form-1-physical-fitness` before the template is created; a slug cannot change afterwards.

## 5. Authenticity and permission evidence

- **Authenticity:** the file is served by the Karnataka Transport Department's own `.gov.in` site from its forms listing, and the hash was re-verified there on 2026-11-14. Whether this copy matches the current Central rule text is unverified.
- **Permission:** none. Republishing requires prior permission (DEC-10).
