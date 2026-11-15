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

## 6. Revision 1.0.1 (`manifest-1.0.1.json`, F6 hardening) — not yet onboarded

1.0.0 was onboarded and published locally on 2026-11-15. At maximum length its values
ran past the printed rules (by up to 98 pt) and, for items 1, 2, 4, 5 and 6, past the
form's own right text margin (x 528.7). 1.0.1 fixes the geometry only; the questions,
identity and blank PDF are unchanged except for item 8's issuer limit.

- **Manifest SHA-256:** `e4e80091d3f42e3c31f50eabdb7cc96971a06242b36bf4726bcd1694a035332b`
- **Blank PDF SHA-256:** `3795a568273f3f95642ca2b8f4f1c9b3744e90c88334f7fe41abd26f887263ab` (unchanged)
- **Needs:** contract support for `valign` (F6 hardening); maker → checker → approver onboarding as version 1.0.1.

Printed rules were measured with pdf.js, which applies the form's negative character
spacing (the earlier estimate from the font's width table alone was ~28 pt too long).

| Item | Rule (x, baseline y) | 1.0.1 mapping | Policy |
|---|---|---|---|
| 1 Name | 338.0–466.2, 681.6 | x338 y682 w128 h20, 9 pt, 2 lines × 10, `valign: bottom` | on the rule; wraps upward |
| 2 Father | 339.2–467.4, 653.5 | x339 y654 w128 h20, 9 pt, 2 × 10, bottom | on the rule; wraps upward |
| 3 Date of birth | 339.5–467.4, 626.1 | x339 y624 w128, 10 pt, single | fixed length |
| 4 Present address | 342.8–470.8, 598.3 | x343 y599 w127 h24, 7 pt, 3 × 8, bottom | 3 lines above the rule |
| 5 Permanent address | 344.0–472.2, 570.6 | x344 y571 w128 h24, 7 pt, 3 × 8, bottom | 3 lines above the rule |
| 6 Qualification | 350.3–473.4, 543.5 | x350 y544 w123 h20, 9 pt, 2 × 10, bottom | on the rule; wraps upward |
| 7 Convictions | 369.7–473.7, 501.5 | x370 y502 w103 h18, 8 pt, 2 × 9, bottom | on the rule; wraps upward |
| 8 Held / not held | no rule | x355 y473 w120, 9 pt | ends before the issuer column edge |
| 8 Issuer | 371.4–475.1, 460.2 | x372 y458 w103, 8 pt, single; **max 24** (was 34) | no room to wrap (item 8's answer is printed above); help asks to abbreviate |
| 11 Reside/business | dotted 125.3–443.3 | x125 w83, 9 pt | — |
| 11 Place | dotted to 443.3 | x209 y334 w234, 8 pt, single | ends on the dotted line |
| 11 District | dotted to 445.1 | x175 y320 w268, 9 pt | ends on the dotted line |
| 12 Fee | dots from 230.7 | x231 w30, 10 pt | 5 digits |
| Dated | rule to 212.7 | x126 w86, 10 pt | — |

**Overflow policy:** a value is printed on its rule and never past the rule's end. It
wraps only upward, into empty space reserved for it, where the row spacing allows
(items 1, 2, 4–7). Text is never shrunk below the manifest size and never truncated: a
value that still does not fit fails PDF generation with a message naming the field.
Readable floor for any printed value: 7 pt (addresses only).

**Evidence:** `src/test/forms/kmv12-real-pdf.test.ts` (frontend) fills the real blank with
typical and maximum-length fictional answers and checks rule extents, rule and label
collisions, the text column edge and non-truncation, and that over-wide answers are
refused. Renders: `Claude outputs/f6/hardening/kmv12-1.0.1-{typ,max}.pdf(.png)`.
