# F6 — Real government template onboarding: preparation (Phases 1–3)

Status: **preparation only.** Nothing has been published. The F5 admin console and the
citizen flow are not used yet: Phases 4–7 run after the frontend update from Lovable.
No form here is production-ready, and no generated document is officially accepted by
any authority.

Prepared on 2026-11-13; F6.1/F6.2 update on 2026-11-14 (sources re-verified, manifests
corrected, per-form records in `docs/onboarding/<slug>/README.md`).

**Baseline correction:** backend `main` is now `03c2bbd` (F5), not `947a800`. The
frontend is on `feat/f4-forms-pdf` at `5d876f1`. The only code change made during this
preparation is the blank-PDF validator (DEC-9: view-only `/OpenAction`, plus a structural
scan of compressed object streams). The manifest contract and frontend are unchanged.

## 1. Official source verification (Phase 1)

| | KMV 12 | CMV Form 1 | Form No. 121 | CMV Form 9 (backup) |
|---|---|---|---|---|
| Title | Form of application for a conductor's licence | Application-cum-declaration as to physical fitness | Declaration u/s 393(6) for receipt of certain incomes without deduction of tax (replaces 15G/15H) | Application for renewal of driving licence |
| Issuing department | Transport Department, Govt of Karnataka | Transport Department, Govt of Karnataka (form under Central Motor Vehicles Rules, Rule 5(2)) | Income Tax Department (CBDT), Ministry of Finance | Transport Department, Govt of Karnataka (CMVR Rule 18(1)) |
| Jurisdiction | Karnataka (Karnataka Motor Vehicles Rules, 1989, Rule 19(1)) | Central rule, Karnataka-hosted copy | Central (Income-tax Act 2025, Rules 2026, rule 211) | Central rule, Karnataka-hosted copy |
| Listing page | transport.karnataka.gov.in/58/driving-license-related-forms/en | same | incometaxindia.gov.in/w/form-no.-121 | same as KMV 12 |
| PDF URL | …/storage/pdf-files/FORM%20KMV%2012.pdf | …/storage/pdf-files/Forms%20related%20to%20driving%20license%20in%20English/form%201.pdf | …/documents/20117/16508534/form-no-121.pdf/6653e7c9-ddfe-e2ef-1909-4db87f103ee7 | …/storage/pdf-files/form%209.pdf |
| Version / date evidence | Server Last-Modified 01 Jun 2020; PDF created 2020-06-01 (Word 2016 → iLovePDF) | Last-Modified 14 Jan 2020; PDF created 2018-01-30 (Online2PDF) | Portal "Upload Date 24/03/2026"; PDF created 2026-03-20 (iLovePDF) | Last-Modified 07 Feb 2022; PDF 2017 (Online2PDF) |
| Pages / size | 1 page, US Letter, 198,865 bytes | 1 page, A4, 24,522 bytes | 4 pages, A4, 464,820 bytes | 1 page, Letter, 81,710 bytes |
| SHA-256 | `3795a568…7263ab` | `c936da8e…0f1682c` | `d6b87a64…12db74` | `b55c959e…fb117b00` |
| Fillable fields | none (coordinate mapping) | none | none (an empty `/AcroForm` key only) | none |
| Backend blank-PDF check | **passes** | **passes** since DEC-9 (view-only `/OpenAction [page /FitH null]`) | **passes** | **passes** since DEC-9 |
| Reuse terms | republishing needs **prior permission** by email (footer Copyright Policy; DEC-10) | same | site permits free reproduction, accurate and with source acknowledged | same as KMV 12 |

Full hashes are in §5. How each file was obtained:

- **Karnataka Transport files:** downloaded directly, with the project owner's approval.
- **Form 121:** the site blocks scripted downloads, so the project owner saved it from the browser (the block was not bypassed). Its hash equals the one computed in the browser from the official URL.
- **FN-121.pdf** (377,472 bytes) on the same site is the department's explanatory *form note*, not the blank form. It is not used.

Sources I rejected or deferred:

- **EPFO Form 11:** the old `epfindia.gov.in/site_docs/.../Form11Revised.pdf` now redirects to a 404. The redesigned epfo.gov.in lists no Form 11 PDF, so the source is not stable.
- **Form 15G/15H:** superseded by Form 121 for tax years from 1 April 2026.
- **KMV 15 and KMV 17:** official and pass the PDF check, but their date lines carry a pre-printed "19__" year, so a 20xx date cannot be printed faithfully. KMV 17 also pre-prints "rupees ten" as the fee.

## 2. Proposed initial forms (Phase 2)

| # | Form | Recommendation | Why |
|---|---|---|---|
| 1 | **KMV 12** (Karnataka) | **Technically ready**; local publication only until permission is obtained (DEC-10) | Genuinely Karnataka-specific, clean text PDF, passes every check; manifest validated and fill-tested |
| 2 | **CMV Form 1** (Central rule, Karnataka copy) | **Technically ready** (DEC-9 decided); local publication only until permission is obtained (DEC-10) | Accompanies every learner's-licence application; simple yes/no declarations |
| 3 | **Form No. 121** (Central, CBDT) | **Deferred**: does not fit contract v1 | See §4 |

A third publishable form needs either a CMV Form 9 manifest (its PDF now passes) or a
contract change for Form 121. No form beyond KMV 12 and CMV Form 1 is proposed for publication.

## 3. PDF and manifest preparation (Phase 3)

Manifests: `docs/onboarding/<slug>/manifest.json` (contract v1, semver 1.0.0).
Both were validated with the backend contract validator against the real PDF geometry
(structure, identity, page bounds, rotation); since DEC-9 both also pass the full
upload path (`validate_blank_pdf` + manifest checks). Both were filled with fictional answer
sets by the real frontend engine (`pdf-fill.ts`), including values at maximum length,
and the output was rendered and inspected.

### KMV 12 — `ka-kmv-12-conductor-licence`

- **Fields:** 16. **Mappings:** 16, all `text_at`/`mark_at` on page 0.
- **Manifest SHA-256:** `b74350fe4661f283056d3807c33731de8942855281db9071dcd4c66382e79cf8`
- **Contract validation:** 0 issues.
- **Fill tests:** typical and maximum-length sets, no overflow.
- **Coverage:** items 1–12 and "Dated" are all mapped:
  - Name and father's name: 40 characters (print space; no character pattern, since the form states none).
  - Date of birth: `not_future`.
  - Present and permanent address: 2-line boxes, 100 characters.
  - Qualification and convictions: text.
  - Item 8: previous licence (radio) and issuing authority (shown only if held).
  - Items 9 and 10: declarations, each a required check mark.
  - Item 11: reside / carry on business (radio), plus place and district.
  - Item 12: fee, entered by the applicant (no amount is hard-coded).
  - Application date.
- **Not mapped (by design):**
  - Signature and duplicate signature: signed by hand; no eSign.
  - Attachments: proof of date of birth, qualification proof, two photographs.
  - "Strike out whichever is not applicable": the chosen statement is printed next to the line instead of striking text.
  - Age ≥ 18: not computable in v1, so it is covered only by the item 10 declaration.
- **Quality notes:**
  - Addresses are free text, so there is no PIN validation: the 2-line space cannot hold the structured address layout.
  - At maximum length the second address line sits just above the printed rule.

### CMV Form 1 — `cmv-form-1-physical-fitness`

- **Fields:** 15. **Mappings:** 22.
- **Manifest SHA-256:** `1d276c32b4bbbf369b86cd49e423272fa2b1c5aef545d8fc863ab10d46830735`
- **Contract validation:** 0 issues on the full upload path.
- **Fill tests:** all-No and all-Yes/maximum-length sets, no overflow.
- **Coverage:**
  - Name and S/W/D of.
  - Permanent address as free text across the form's 4 dotted lines (no PIN rule, since the form states none).
  - Optional temporary/official address ("if any"), laid out the same way.
  - Date of birth, and age on the application date.
  - Identification marks (1) and (2), both required (neither is marked "if any").
  - Questions (a)–(g) as Yes/No: the answer that does not apply is struck through with a cross, as the form instructs.
  - Question labels use the official wording. For (b), the text continues in the help because labels are capped at 200 characters.
- **Not mapped:**
  - Signature or thumb impression.
  - Details for a "Yes" on (g) and the amplification required by Note (1): the form has no space, and the help text says to give them separately.
  - The closing declaration: it has no box, so it is affirmed by signature.
- **Assumptions to review:**
  - Identity uses `KARNATAKA_STATE`: the file is the Karnataka Transport Department's copy
    of a Central-rule form. A slug without a `ka-` prefix (`cmv-form-1-physical-fitness`)
    could collide with another state's copy later; consider `ka-cmv-form-1-physical-fitness`
    before the template is created, because the slug cannot change afterwards.
  - Age is entered by the applicant (not computed); only non-negative, at most 3 digits.
  - Identification mark (1) is printed on the "(1)" row and mark (2) on the dotted line beside "(2)". The official layout is ambiguous here.

## 4. Form No. 121: why it is deferred

- **Collisions:** the Part A answer cells are 136×17 pt and already contain printed guidance ("(refer Note 1)" … "(refer Note 7)", "Yes/no"). Filled text would print over the form's own text.
- **Address too long:** item 2 needs nine components (Note 2) in a single 17-pt cell.
- **Computed field:** item 12 is the sum of items 10 and 11(b). Computed fields are out of contract v1.
- **Repeating table:** item 14 is a two-row ITR table. It could be mapped as fixed rows, but that is still a table.
- **Part B:** pages 2–3 are completed by the payer, not the citizen.
- **Electronic route:** Form 121 can also be submitted electronically to the payer, which reduces the value of a paper version.

Feasible later, with a contract v2 decision on small-font cell filling and computed sums.
Until then, the file is recorded as a verified official source only.

## 5. Integrity record

```
3795a568273f3f95642ca2b8f4f1c9b3744e90c88334f7fe41abd26f887263ab  KMV 12
c936da8e47f9e913961ca3566ced4ec24713d8c64cd553f6cfb26df380f1682c  CMV Form 1
d6b87a64193be5cdc4d7df7c16a7d9511fa70bb58cfb5fceda7caaa81412db74  Form No. 121
b55c959e4d9d20b70cf106bfe745681fb9fd96daf7e26244198d70f0fb117b00  CMV Form 9
```

Copies of the blanks, fictional answer sets and rendered sample fills are in
`Claude outputs/f6/` (not committed; official PDFs are not stored in git).

## 6. Before Phase 4 (publication)

1. **Approved domains:** an admin must add `transport.karnataka.gov.in`
   (`STATUTORY_EXACT_HOST`, or `karnataka.gov.in` as `GOV_TLD_SUFFIX`) and
   `incometaxindia.gov.in` through the F5 Sources page. The dev database holds only the
   test-fixture domain today.
2. **DEC-10:** the Transport site requires prior permission to republish; obtain it (or
   a documented legal view) before any non-local publication, and confirm each form
   matches its current notified text.
3. **DEC-9:** decided (option b); CMV Form 1 and Form 9 now pass the PDF check.
4. **Then publish KMV 12 and CMV Form 1 through the console.** Steps: create → validate → upload →
   preview → submit → verify (a second person) → approve (a third person) → publish →
   citizen flow. Use the four fictional local accounts from F5.
5. **Tracked separately and unchanged:** DEC-1, DEC-2, DEC-3 and DEC-8. eSign stays
   disabled, and no DPDP compliance is claimed.
