# KMV 12 — Form of application for a conductor's licence (onboarding record)

Status: **technically ready for local admin onboarding; NOT cleared for non-local
publication** (reuse permission outstanding — DEC-10). Not published. No production
signature.

## 1. Official source verification

| Item | Value |
|---|---|
| Issuing authority | Transport Department, Government of Karnataka |
| Form / rule | Form KMV 12, "[See Rule 19(1)]", Karnataka Motor Vehicles Rules, 1989 (the form's own header) |
| Jurisdiction | `KARNATAKA_STATE` |
| Listing page | https://transport.karnataka.gov.in/58/driving-license-related-forms/en (row "FORM OF APPLICATION FOR A CONDUCTOR'S LICENCE … Form K.M.V 12", listed as English, 194 KB) |
| PDF URL | https://transport.karnataka.gov.in/storage/pdf-files/FORM%20KMV%2012.pdf |
| First retrieved | 2026-11-13 (downloaded with the project owner's approval) |
| Re-verified | 2026-11-14, in the browser from the official URL: HTTP 200, `application/pdf`, 198,865 bytes, SHA-256 identical |
| Revision evidence | Server `Last-Modified: Mon, 01 Jun 2020 09:35:57 GMT`; PDF CreationDate 2020-06-01. The form shows no edition or amendment date. |
| Currency | **Unverified.** No official Gazette text for Rule 19 / Form KMV 12 was located to compare against. |
| Reuse / publication terms | The site's footer **Copyright Policy** (pop-up, no own URL) allows republication free of charge **only with prior permission requested by email** from CeG or the owning department, reproduced accurately with the source acknowledged. The permission email on the page is an unfilled placeholder. **No permission has been requested or obtained.** |

## 2. PDF inspection (original, unmodified)

| Item | Result |
|---|---|
| SHA-256 | `3795a568273f3f95642ca2b8f4f1c9b3744e90c88334f7fe41abd26f887263ab` |
| Header / pages | `%PDF-1.5`, 1 page, US Letter 612 × 792 pt, CropBox = MediaBox, rotation 0 |
| Metadata | Title `kmv12.PDF`, Creator Microsoft Word 2016, Producer www.ilovepdf.com, created 2020-06-01; tagged (`/StructTreeRoot`, `/MarkInfo`, `/Lang`) |
| Revisions | one `%%EOF` (no incremental updates); not encrypted; 115 objects, none compressed |
| Text layer | yes (1,631 characters extracted) — not a scan; 0 images |
| Fonts | Times New Roman (regular, bold, italic) and Arial — TrueType/WinAnsi **not embedded**, plus embedded Times New Roman CID subsets (Identity-H). Viewers substitute the non-embedded faces, so the printed form's own text may render with slightly different metrics across viewers. Filled values use the standard Helvetica font. |
| AcroForm / XFA | none / none |
| Annotations | none |
| Actions | none — no JavaScript, Launch, SubmitForm, ImportData, URI, GoToR, GoToE, Named, RichMedia or other action; no `/OpenAction`, no `/AA` |
| Embedded files | none |
| Security validator (`validate_blank_pdf`, backend `e174b6e`) | **PASS** |
| Suitable for local filling | yes — coordinate (`text_at` / `mark_at`) mapping on page 0 |

## 3. Proposed manifest (`manifest.json`, contract v1)

- **Identity:** `ka-kmv-12-conductor-licence`, `KARNATAKA_STATE`, `TRANSPORT`, `KMV-12`, 1.0.0, 1 page.
- **Size:** 16 fields, 16 mappings.
- **Manifest SHA-256:** `b74350fe4661f283056d3807c33731de8942855281db9071dcd4c66382e79cf8`

| # on form | Field id | Type | Required | Mapping (page 0, pt) | Validation and why |
|---|---|---|---|---|---|
| 1 | `applicant_name` | text | yes | text_at x338 y680 w215 h14, 10 pt | max 40 (print space) |
| 2 | `father_name` | text | yes | x339 y651 w214 | max 40 (print space) |
| 3 | `date_of_birth` | date `DD/MM/YYYY` | yes | x339 y624 w120 | `not_future` (a birth date cannot be in the future) |
| 4 | `present_address` | multiline | yes | x343 y598 w232 h18, 8 pt, 2 lines × 9 pt | max 100 (print space) |
| 5 | `permanent_address` | multiline | yes | x344 y571 w231 h18, 8 pt, 2 lines | max 100 |
| 6 | `educational_qualification` | text | yes | x350 y542 w205 | max 40 |
| 7 | `convictions` | text | yes | x370 y500 w188, 9 pt | max 36 |
| 8 | `previous_licence` | radio `not_held` / `held` → prints label | yes | x355 y473 w200, 9 pt | — |
| 8 | `previous_licence_issuer` | text, shown only if `held` | yes when shown | x371 y458 w187, 9 pt | max 34 |
| 9 | `not_disqualified` | checkbox (declaration) | yes | mark_at check x372 y433 s10 | — |
| 10 | `age_and_truth_declaration` | checkbox (declaration) | yes | mark_at check x368 y378 s10 | — |
| 11 | `residence_type` | radio `reside` / `business` → prints label | yes | x125 y334 w95, 9 pt | — |
| 11 | `residence_place` | text | yes | x222 y334 w336, 9 pt | max 60 |
| 11 | `residence_district` | text | yes | x175 y320 w200, 9 pt | max 40 |
| 12 | `fee_rupees` | number | yes | x231 y293 w76 | 0–99999 (non-negative; 5-digit print space). The amount is entered by the applicant; nothing is hard-coded. |
| — | `application_date` ("Dated") | date `DD/MM/YYYY` | yes | x126 y247 w90 | none |

**Required-status rule** (the form marks nothing as mandatory): a numbered item printed
without "(if any)" is required. Item 8's issuer is required only when "previously held"
is chosen. Name patterns, address minimum lengths and a "not in the future" rule on
the application date were removed because the form does not state them.

**Validation results:**
- Backend `assert_valid_manifest` against the real PDF: **0 issues**.
- Frontend `parseFormManifest` and `fillPdf`: typical and maximum-length fictional sets fill with **0 problems**; rendered output inspected.

## 4. Unsupported fields and known limitations

- **Not mapped:** signature and duplicate signature (signed by hand; eSign stays disabled).
- **Attachments (physical, not mapped):** proof of date of birth, proof of qualification, two photographs.
- **Strike-out choices (items 7, 8, 11):** the chosen statement is printed next to the line; the form's text is not struck through.
- **Age ≥ 18:** not computable in contract v1, so it is covered only by the item 10 declaration.
- **Addresses:** free text, so no PIN code check. The single printed line holds at most 2 lines of 8 pt text.
- **Overflow:** text that does not fit fails generation with a message. It is never truncated.

## 5. Authenticity and permission evidence

- **Authenticity:** the file is served by the department's own `.gov.in` site from its forms listing, and the hash was re-verified there on 2026-11-14.
- **Permission:** none. Republishing requires prior permission under the site's policy (DEC-10).
