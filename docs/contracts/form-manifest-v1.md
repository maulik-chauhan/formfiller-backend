# Form Manifest Contract — v1

Status: **prototype contract, implemented in both repositories**. Machine-readable
shape: [`form-manifest-v1.schema.json`](form-manifest-v1.schema.json). Shared fixtures
and cross-language vectors: [`fixtures/`](fixtures/) (fictional test data only — no
fixture is a government form).

| Implementation | Location |
|---|---|
| Backend validator (publication gate) | `backend/app/modules/templates/manifest_contract.py` |
| Frontend validator, renderer, PDF engine | `frontend/.../src/features/forms/` |
| Fixture generator | `backend/scripts/build_contract_fixtures.py` |

The manifest is the value of `field_mapping_manifest` in the template bundle. It tells
the client **what to ask** and **where each answer is printed on the blank PDF**. It
never contains applicant data, and answers never leave the device.

## 1. Identity

Identity is split between the manifest and the signed bundle envelope.

| Item | Where | Notes |
|---|---|---|
| `schema_version` | manifest | integer, exactly `1` |
| `kind` | manifest | `"govforms.form-manifest"` |
| `identity.slug`, `.jurisdiction`, `.category`, `.official_form_code`, `.semver` | manifest | must equal the template version the manifest is attached to |
| `identity.page_count`, `identity.blank_pdf_sha256` | manifest | must equal the uploaded blank PDF |
| `template_id`, `template_version_id` | bundle | server-assigned; cannot be inside a document that is hashed before they exist |
| `manifest_sha256` | bundle | SHA-256 of the canonical manifest (§7); a document cannot contain its own hash |
| `signature_format_version`, signature, key id | bundle | v2 digest binds every identity item above plus `manifest_sha256` |

The backend rejects an upload whose `identity` disagrees with the template or PDF
(`identity_mismatch`). The client re-checks `identity` against the verified bundle before
rendering, so a manifest can never be replayed under another template.

## 2. Structure

```
manifest  = { schema_version, kind, identity, sections[1..50], rules?[0..50] }
section   = { id, title, description?, fields[1..200] }
field     = { id, type, label, help?, required, default?, options?, validation?,
              visible_when?, profile_key? | profile_keys?, pdf[1..20] }
```

- **Order** is array order: sections, then fields within a section. There is no separate
  order property.
- `id` matches `^[a-z][a-z0-9_]{0,63}$`. Section ids are unique; field ids are unique
  across the whole manifest; rule ids are unique.
- Unknown properties are **rejected** everywhere (`structure_invalid`).
- All numbers are integers within ±(2^53−1). Any float is rejected
  (`number_not_integer`) because only integers serialise identically in Python and
  JavaScript, and the manifest hash depends on the exact bytes.
- At most 500 fields per manifest.

### Field types

| `type` | Answer value stored in the draft | Notes |
|---|---|---|
| `text` | string | single line |
| `multiline` | string | may contain `\n` |
| `number` | integer | decimals are not supported in v1 |
| `date` | string `YYYY-MM-DD` | calendar-valid |
| `select` | option `value` | rendered as a dropdown |
| `radio` | option `value` | rendered as radio buttons |
| `checkbox` | boolean | |
| `address` | `{line1, line2, city, district, state, pincode}` (strings) | fixed components |

`options` (`[{value, label}]`, 1..200, unique `value` matching `^[A-Za-z0-9_.-]{1,64}$`)
is required for `select`/`radio` and forbidden otherwise (`options_invalid`).

`default` is allowed for `text`/`multiline` (string), `number` (integer), `checkbox`
(boolean), `select`/`radio` (an option value); forbidden for `date` and `address`
(`default_invalid`). Defaults are copied into a **new** draft once; they are not applied
during validation, so a cleared answer stays cleared.

`required: true` means: non-empty text; a number; a valid date; a chosen option; a
**checked** checkbox; an address with `line1`, `city`, `state` and a valid `pincode`.
Requirement applies only while the field is visible.

`profile_key` (not on `checkbox`/`address`) names the applicant-profile entry key that
may pre-fill this field. `profile_keys` (address only) maps components to entry keys.
Profile reuse is local, optional, and never overwrites an existing answer without
confirmation. A profile value that fails the field's own validation is skipped.

## 3. Validation

`validation` keys allowed per type (any other key → `validation_not_applicable`):

| Type | Keys |
|---|---|
| `text` | `min_length`, `max_length` (0..5000), `pattern` |
| `multiline` | `min_length`, `max_length` |
| `number` | `min`, `max` (integers) |
| `date` | `min_date`, `max_date` (`YYYY-MM-DD`), `not_future`, `not_past` (booleans, not both) |
| others | none |

Lengths count Unicode code points. `pattern` is a **name**, never a regular expression:

| Name | Meaning |
|---|---|
| `digits` | `^[0-9]+$` |
| `alpha_space` | `^[A-Za-z][A-Za-z .'-]*$` |
| `alphanumeric` | `^[A-Za-z0-9]+$` |
| `pincode_in` | `^[1-9][0-9]{5}$` |
| `mobile_in` | `^[6-9][0-9]{9}$` |
| `email` | `^[^\s@]+@[^\s@]+\.[^\s@]+$` |

Arbitrary regex is refused (ReDoS and cross-engine divergence).

Answer error codes (client): `required`, `min_length`, `max_length`, `pattern`,
`not_an_integer`, `min`, `max`, `invalid_date`, `min_date`, `max_date`, `not_future`,
`not_past`, `not_an_option`, `address_incomplete`, `rule:<rule id>`.

### Conditional visibility

`visible_when = { field, op, value? | values? }`. The referenced field must be defined
**earlier** in document order (so cycles are impossible) and be:

- `select`/`radio`: `op` ∈ `equals`, `not_equals` (with `value`) or `in` (with `values`);
  every value must be one of that field's options.
- `checkbox`: `op` ∈ `is_checked`, `is_not_checked` (no value).

A hidden field is not validated and **not printed**, even if a stale answer exists. A
field whose controlling field is itself hidden is hidden.

### Cross-field rules

```
{ id, type: "date_not_before", field, other_field, message }   // field >= other_field
{ id, type: "required_if",     field, when: <condition>, message }
```

Rules are evaluated only when the fields involved are visible and (for dates) valid. A
rule error is reported on `field` as `rule:<id>` unless that field already has an error.
Unknown rule types are rejected (`rule_invalid`).

## 4. PDF mapping

Every field has at least one mapping; a field that would print nowhere is rejected.
Coordinates are integer PDF points, origin bottom-left of the unrotated page; `page` is
zero-based. Coordinate mappings are not supported on rotated pages.

| `type` | For field types | Properties |
|---|---|---|
| `acro_text` | text, multiline, number, date, select, radio, address | `field_name`, + text attributes |
| `text_at` | same | `page, x, y, width, height, font, size, align, multiline`, `line_height?`, + text attributes |
| `acro_checkbox` | checkbox; select/radio with `on_value` | `field_name`, `on_value?` |
| `mark_at` | checkbox; select/radio with `on_value` | `page, x, y, size, mark` (`check`\|`cross`), `on_value?` |
| `acro_radio` | select, radio | `field_name`, `options` {answer value → PDF export value} for **every** option |
| `acro_dropdown` | select, radio | same as `acro_radio` |

Text attributes:

- `source` — required for `address`: `line1`, `line2`, `city`, `district`, `state`,
  `pincode` or `full`. `full` prints `line1` / `line2` / `city, district` /
  `state - pincode`, skipping empty parts, joined by newlines in a multi-line target and
  by `, ` in a single-line target.
- `date_format` — required for `date`: `DD/MM/YYYY`, `DD-MM-YYYY`, `DD.MM.YYYY`,
  `YYYY-MM-DD`.
- `option_text` — required for `select`/`radio` printed as text: `label` or `value`.
- `transform` — optional for text/multiline/address: `none` or `uppercase`.

`text_at`: `font` ∈ `Helvetica`, `Helvetica-Bold`, `Times-Roman`, `Courier`; `size` 4..72;
`align` ∈ `left`, `center`, `right`; text is black. Single-line text that is wider than
`width` and multi-line text taller than `height` **fail generation** (`text_overflow`) —
text is never silently truncated or shrunk. Text containing characters the standard
fonts cannot encode fails generation (`unencodable_text`); embedding a Unicode font is
future work.

AcroForm targets: `field_name` is the fully qualified PDF field name. At publication the
backend checks, against the real PDF, that the field exists (`pdf_field_missing`), has
the right kind (`pdf_field_type_mismatch`), that radio/dropdown export values exist
(`pdf_option_missing`), that one text field is not filled twice (`pdf_field_reused`),
and that coordinate areas are on an unrotated page (`pdf_page_rotated`,
`pdf_page_out_of_range`, `pdf_rect_out_of_bounds`). The client repeats these checks
against the downloaded PDF before filling and additionally enforces the PDF field's own
maximum length (`pdf_max_length`).

Multi-line AcroForm text boxes are laid out by the client: the form's own font size is
kept when every wrapped line fits the box; otherwise the largest of 11..6 pt that fits is
used; if none fits, generation fails (`text_overflow`). Single-line boxes keep the
form's own appearance settings.

The client fills a **copy**; the cached blank PDF is never modified. Filled PDFs are
flattened only by the viewer; form fields are left intact.

## 5. Compatibility

| Situation | Behaviour (both sides) |
|---|---|
| `schema_version` missing or ≠ 1 | reject whole manifest (`schema_version_unsupported`). Legacy pre-contract rows remain downloadable but are **not fillable**. |
| unknown field `type` | reject whole manifest (`field_type_unsupported`) |
| unknown mapping `type` | reject whole manifest (`mapping_type_unsupported`) |
| unknown property | reject (`structure_invalid`) |
| new optional capability | requires `schema_version` 2; v1 clients refuse it rather than ignore it |

A published manifest is immutable (database trigger). Any change is a new template
version with a new `semver`. A draft stores the `slug`, `semver`, `template_id` and
`template_version_id` it was created with and is never migrated to another version.
Deprecated or withdrawn versions remain readable in existing drafts with a warning;
generating a PDF from a version that is not confirmed as published requires explicit
acknowledgement in the UI.

## 6. Issue codes (manifest validation)

`schema_version_unsupported`, `structure_invalid`, `number_not_integer`, `duplicate_id`,
`field_type_unsupported`, `mapping_type_unsupported`, `options_invalid`,
`default_invalid`, `validation_not_applicable`, `condition_invalid`, `rule_invalid`,
`mapping_not_applicable`, `identity_mismatch`, `pdf_page_out_of_range`,
`pdf_field_missing`, `pdf_field_type_mismatch`, `pdf_option_missing`,
`pdf_field_reused`, `pdf_page_rotated`, `pdf_rect_out_of_bounds`.

Each `fixtures/manifests/invalid/*.json` names the code both validators must report.

## 7. Canonical form and hash

```
manifest_sha256 = sha256( json.dumps(manifest, sort_keys=True,
                                     separators=(",", ":"), ensure_ascii=True) )
```

Keys sorted by code point, no whitespace, every character outside printable ASCII
escaped as lowercase `\uXXXX` (astral characters as surrogate pairs), no Unicode
normalisation. `JSON.stringify` alone does not produce this. Vectors:
`fixtures/vectors/canonical-json.json`, `manifest-digests.json`, `signature.json`.

## 8. Not in v1

Decimals, repeating groups/tables, file or image fields, signatures of any kind,
computed fields, arbitrary regex, non-Latin text in coordinate mappings, rotated pages,
localised labels inside the manifest.
