# ruff: noqa: RUF001  (deliberately awkward Unicode in the canonical-JSON vectors)
"""Build the shared form-manifest contract fixtures.

    python -m scripts.build_contract_fixtures            # (re)write fixtures
    python -m scripts.build_contract_fixtures --check    # fail if out of date

Canonical output: ``<repo>/docs/contracts/fixtures``. The frontend keeps a
byte-identical copy (``scripts/sync_contracts.py``); ``index.json`` pins every
file's SHA-256 so either side detects drift.

EVERYTHING HERE IS FICTIONAL TEST DATA. No fixture is, or describes, an official
government form, and no field is an official field definition.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import pathlib
import sys
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.signing import template_bundle_digest_v1, template_bundle_digest_v2
from app.modules.templates.manifest_contract import validate_manifest
from app.modules.templates.service import canonical_manifest_json

ROOT = pathlib.Path(__file__).resolve().parents[2] / "docs" / "contracts" / "fixtures"
#: Published TEST seed (bytes 32..63). Never a production key.
TEST_SEED = bytes(range(32, 64))
TEST_KID = "ed25519-template-2026-v1"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_sha(name: str) -> str:
    return sha256((ROOT / "pdfs" / name).read_bytes())


def identity(slug: str, code: str, pdf: str, *, category: str = "WELFARE") -> dict[str, Any]:
    return {
        "slug": slug,
        "jurisdiction": "CENTRAL_INDIA",
        "category": category,
        "official_form_code": code,
        "semver": "1.0.0",
        "page_count": 2,
        "blank_pdf_sha256": pdf_sha(pdf),
    }


def manifest(ident: dict[str, Any], sections: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "govforms.form-manifest",
        "identity": ident,
        "sections": sections,
        **extra,
    }


def text_at(x: int, y: int, w: int, h: int, *, page: int = 0, **extra: Any) -> dict[str, Any]:
    return {
        "type": "text_at",
        "page": page,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "font": "Helvetica",
        "size": 11,
        "align": "left",
        "multiline": False,
        **extra,
    }


FLAT = "flat-test.pdf"
FILLABLE = "fillable-test.pdf"


def build_valid() -> dict[str, dict[str, Any]]:
    name_flat = {
        "id": "full_name",
        "type": "text",
        "label": "Full name",
        "required": True,
        "pdf": [text_at(76, 723, 292, 14)],
    }
    out: dict[str, dict[str, Any]] = {}

    # 1. Simple text form.
    out["simple-text"] = manifest(
        identity("test-simple-text", "TEST-FIXTURE-001", FLAT),
        [{"id": "main", "title": "Details", "fields": [copy.deepcopy(name_flat)]}],
    )

    # 2. Multi-section form.
    out["multi-section"] = manifest(
        identity("test-multi-section", "TEST-FIXTURE-002", FLAT),
        [
            {
                "id": "person",
                "title": "Person",
                "description": "Who is filling in this test form.",
                "fields": [copy.deepcopy(name_flat)],
            },
            {
                "id": "place",
                "title": "Place",
                "fields": [
                    {
                        "id": "pincode",
                        "type": "text",
                        "label": "PIN code",
                        "help": "Six digits.",
                        "required": True,
                        "validation": {"pattern": "pincode_in"},
                        "pdf": [text_at(76, 463, 92, 14)],
                    }
                ],
            },
            {
                "id": "notes",
                "title": "Notes",
                "fields": [
                    {
                        "id": "notes",
                        "type": "multiline",
                        "label": "Notes",
                        "required": False,
                        "validation": {"max_length": 600},
                        "pdf": [text_at(76, 626, 392, 108, page=1, multiline=True, line_height=14)],
                    }
                ],
            },
        ],
    )

    # 3. Required and optional fields.
    out["required-optional"] = manifest(
        identity("test-required-optional", "TEST-FIXTURE-003", FLAT),
        [
            {
                "id": "main",
                "title": "Details",
                "fields": [
                    {
                        **copy.deepcopy(name_flat),
                        "validation": {"min_length": 2, "max_length": 60, "pattern": "alpha_space"},
                        "profile_key": "full_name",
                    },
                    {
                        "id": "pincode",
                        "type": "text",
                        "label": "PIN code (optional)",
                        "required": False,
                        "validation": {"pattern": "pincode_in"},
                        "pdf": [text_at(76, 463, 92, 14)],
                    },
                ],
            }
        ],
    )

    # 4. Dropdown and checkbox (coordinate marks).
    out["dropdown-checkbox"] = manifest(
        identity("test-dropdown-checkbox", "TEST-FIXTURE-004", FLAT),
        [
            {
                "id": "main",
                "title": "Choices",
                "fields": [
                    {
                        "id": "delivery",
                        "type": "select",
                        "label": "Delivery",
                        "required": True,
                        "default": "collect",
                        "options": [
                            {"value": "collect", "label": "Collect in person"},
                            {"value": "post", "label": "Send by post"},
                        ],
                        "pdf": [
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 73,
                                "y": 631,
                                "size": 10,
                                "mark": "cross",
                                "on_value": "collect",
                            },
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 163,
                                "y": 631,
                                "size": 10,
                                "mark": "cross",
                                "on_value": "post",
                            },
                        ],
                    },
                    {
                        "id": "urgent",
                        "type": "checkbox",
                        "label": "Urgent",
                        "required": False,
                        "default": False,
                        "pdf": [
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 73,
                                "y": 607,
                                "size": 10,
                                "mark": "check",
                            }
                        ],
                    },
                ],
            }
        ],
    )

    # 5. Date and numeric.
    out["date-number"] = manifest(
        identity("test-date-number", "TEST-FIXTURE-005", FLAT),
        [
            {
                "id": "main",
                "title": "Date and count",
                "fields": [
                    {
                        "id": "event_date",
                        "type": "date",
                        "label": "Date",
                        "required": True,
                        "validation": {"min_date": "2000-01-01", "not_future": True},
                        "pdf": [text_at(76, 677, 132, 14, date_format="DD/MM/YYYY")],
                    },
                    {
                        "id": "count",
                        "type": "number",
                        "label": "Count",
                        "required": True,
                        "default": 1,
                        "validation": {"min": 1, "max": 20},
                        "pdf": [text_at(244, 677, 72, 14, align="right")],
                    },
                ],
            }
        ],
    )

    # 6. Conditional fields + cross-field rules.
    out["conditional"] = manifest(
        identity("test-conditional", "TEST-FIXTURE-006", FLAT),
        [
            {
                "id": "main",
                "title": "Delivery",
                "fields": [
                    {
                        "id": "delivery",
                        "type": "radio",
                        "label": "Delivery",
                        "required": True,
                        "options": [
                            {"value": "collect", "label": "Collect in person"},
                            {"value": "post", "label": "Send by post"},
                        ],
                        "pdf": [
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 73,
                                "y": 631,
                                "size": 10,
                                "mark": "cross",
                                "on_value": "collect",
                            },
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 163,
                                "y": 631,
                                "size": 10,
                                "mark": "cross",
                                "on_value": "post",
                            },
                        ],
                    },
                    {
                        "id": "postal_pincode",
                        "type": "text",
                        "label": "PIN code for postal delivery",
                        "required": True,
                        "validation": {"pattern": "pincode_in"},
                        "visible_when": {"field": "delivery", "op": "equals", "value": "post"},
                        "pdf": [text_at(76, 463, 92, 14)],
                    },
                    {
                        "id": "urgent",
                        "type": "checkbox",
                        "label": "Urgent",
                        "required": False,
                        "pdf": [
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 73,
                                "y": 607,
                                "size": 10,
                                "mark": "check",
                            }
                        ],
                    },
                    {
                        "id": "reason",
                        "type": "multiline",
                        "label": "Reason for urgency",
                        "required": False,
                        "visible_when": {"field": "urgent", "op": "is_checked"},
                        "pdf": [text_at(76, 626, 392, 108, page=1, multiline=True, line_height=14)],
                    },
                ],
            }
        ],
        rules=[
            {
                "id": "urgent_needs_reason",
                "type": "required_if",
                "field": "reason",
                "when": {"field": "urgent", "op": "is_checked"},
                "message": "Give a reason when the request is urgent.",
            }
        ],
    )

    # 7. PDF with fillable (AcroForm) fields — also the main end-to-end template.
    out["fillable-acroform"] = manifest(
        identity("test-fillable-form", "TEST-FIXTURE-007", FILLABLE),
        [
            {
                "id": "applicant",
                "title": "Applicant",
                "description": "Test section: who is applying.",
                "fields": [
                    {
                        "id": "full_name",
                        "type": "text",
                        "label": "Full name",
                        "help": "As it should appear on the form.",
                        "required": True,
                        "validation": {"min_length": 2, "max_length": 60, "pattern": "alpha_space"},
                        "profile_key": "full_name",
                        "pdf": [
                            {
                                "type": "acro_text",
                                "field_name": "applicant.full_name",
                                "transform": "uppercase",
                            }
                        ],
                    },
                    {
                        "id": "date_of_birth",
                        "type": "date",
                        "label": "Date of birth",
                        "required": True,
                        "validation": {"min_date": "1900-01-01", "not_future": True},
                        "profile_key": "date_of_birth",
                        "pdf": [
                            {
                                "type": "acro_text",
                                "field_name": "applicant.dob",
                                "date_format": "DD/MM/YYYY",
                            }
                        ],
                    },
                    {
                        "id": "contact_method",
                        "type": "radio",
                        "label": "Preferred contact",
                        "required": True,
                        "options": [
                            {"value": "post", "label": "Post"},
                            {"value": "phone", "label": "Phone"},
                            {"value": "email", "label": "Email"},
                        ],
                        "pdf": [
                            {
                                "type": "acro_radio",
                                "field_name": "applicant.contact_method",
                                "options": {"post": "post", "phone": "phone", "email": "email"},
                            }
                        ],
                    },
                    {
                        "id": "mobile",
                        "type": "text",
                        "label": "Mobile number",
                        "help": "Ten digits, without +91.",
                        "required": True,
                        "validation": {"pattern": "mobile_in"},
                        "visible_when": {
                            "field": "contact_method",
                            "op": "equals",
                            "value": "phone",
                        },
                        "profile_key": "mobile",
                        "pdf": [{"type": "acro_text", "field_name": "applicant.mobile"}],
                    },
                ],
            },
            {
                "id": "address",
                "title": "Address",
                "fields": [
                    {
                        "id": "home_address",
                        "type": "address",
                        "label": "Home address",
                        "required": True,
                        "profile_keys": {
                            "line1": "address_line_1",
                            "line2": "address_line_2",
                            "city": "city",
                            "district": "district",
                            "state": "state",
                            "pincode": "pin_code",
                        },
                        "pdf": [
                            {"type": "acro_text", "field_name": "address.full", "source": "full"},
                            {
                                "type": "acro_text",
                                "field_name": "address.pincode",
                                "source": "pincode",
                            },
                        ],
                    }
                ],
            },
            {
                "id": "details",
                "title": "Request details",
                "fields": [
                    {
                        "id": "service_type",
                        "type": "select",
                        "label": "Service type",
                        "required": True,
                        "default": "standard",
                        "options": [
                            {"value": "standard", "label": "Standard"},
                            {"value": "priority", "label": "Priority"},
                            {"value": "express", "label": "Express"},
                        ],
                        "pdf": [
                            {
                                "type": "acro_dropdown",
                                "field_name": "details.service_type",
                                "options": {
                                    "standard": "STANDARD",
                                    "priority": "PRIORITY",
                                    "express": "EXPRESS",
                                },
                            }
                        ],
                    },
                    {
                        "id": "fee_waiver",
                        "type": "checkbox",
                        "label": "Request a fee waiver",
                        "required": False,
                        "pdf": [{"type": "acro_checkbox", "field_name": "details.fee_waiver"}],
                    },
                    {
                        "id": "dependants",
                        "type": "number",
                        "label": "Number of dependants",
                        "required": True,
                        "validation": {"min": 0, "max": 20},
                        "visible_when": {"field": "fee_waiver", "op": "is_checked"},
                        "pdf": [{"type": "acro_text", "field_name": "details.dependants"}],
                    },
                    {
                        "id": "remarks",
                        "type": "multiline",
                        "label": "Remarks",
                        "required": False,
                        "validation": {"max_length": 400},
                        "pdf": [{"type": "acro_text", "field_name": "details.remarks"}],
                    },
                ],
            },
            {
                "id": "declaration",
                "title": "Declaration",
                "fields": [
                    {
                        "id": "agree",
                        "type": "checkbox",
                        "label": "I confirm the details are correct",
                        "required": True,
                        "pdf": [{"type": "acro_checkbox", "field_name": "declaration.agree"}],
                    },
                    {
                        "id": "declaration_date",
                        "type": "date",
                        "label": "Date of declaration",
                        "required": True,
                        "validation": {"not_future": True},
                        "pdf": [
                            {
                                "type": "acro_text",
                                "field_name": "declaration.date",
                                "date_format": "DD-MM-YYYY",
                            }
                        ],
                    },
                ],
            },
        ],
        rules=[
            {
                "id": "declared_after_birth",
                "type": "date_not_before",
                "field": "declaration_date",
                "other_field": "date_of_birth",
                "message": "The declaration date cannot be before the date of birth.",
            }
        ],
    )

    # 8. PDF requiring coordinate-based mapping (every coordinate mapping type).
    out["coordinate-mapped"] = manifest(
        identity("test-coordinate-form", "TEST-FIXTURE-008", FLAT),
        [
            {
                "id": "main",
                "title": "Details",
                "fields": [
                    {**copy.deepcopy(name_flat), "profile_key": "full_name"},
                    {
                        "id": "event_date",
                        "type": "date",
                        "label": "Date",
                        "required": True,
                        "pdf": [text_at(76, 677, 132, 14, date_format="DD.MM.YYYY")],
                    },
                    {
                        "id": "count",
                        "type": "number",
                        "label": "Count",
                        "required": False,
                        "validation": {"min": 0, "max": 999},
                        "pdf": [text_at(244, 677, 72, 14, align="right", font="Courier")],
                    },
                    {
                        "id": "delivery",
                        "type": "radio",
                        "label": "Delivery",
                        "required": True,
                        "options": [
                            {"value": "collect", "label": "Collect in person"},
                            {"value": "post", "label": "Send by post"},
                        ],
                        "pdf": [
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 73,
                                "y": 631,
                                "size": 10,
                                "mark": "cross",
                                "on_value": "collect",
                            },
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 163,
                                "y": 631,
                                "size": 10,
                                "mark": "cross",
                                "on_value": "post",
                            },
                        ],
                    },
                    {
                        "id": "urgent",
                        "type": "checkbox",
                        "label": "Urgent",
                        "required": False,
                        "pdf": [
                            {
                                "type": "mark_at",
                                "page": 0,
                                "x": 73,
                                "y": 607,
                                "size": 10,
                                "mark": "check",
                            }
                        ],
                    },
                    {
                        "id": "home_address",
                        "type": "address",
                        "label": "Address",
                        "required": True,
                        "profile_keys": {
                            "line1": "address_line_1",
                            "line2": "address_line_2",
                            "city": "city",
                            "district": "district",
                            "state": "state",
                            "pincode": "pin_code",
                        },
                        "pdf": [
                            text_at(
                                76, 508, 392, 68, multiline=True, line_height=13, source="full"
                            ),
                            text_at(76, 463, 92, 14, source="pincode"),
                        ],
                    },
                ],
            },
            {
                "id": "notes",
                "title": "Notes",
                "fields": [
                    {
                        "id": "notes",
                        "type": "multiline",
                        "label": "Notes",
                        "required": False,
                        "validation": {"max_length": 600},
                        "pdf": [
                            text_at(
                                76,
                                626,
                                392,
                                108,
                                page=1,
                                multiline=True,
                                line_height=14,
                                font="Times-Roman",
                            )
                        ],
                    }
                ],
            },
        ],
    )
    return out


def build_invalid(valid: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Each case is one targeted mutation of a valid manifest + the expected code."""
    cases: dict[str, dict[str, Any]] = {}

    def case(name: str, base: str, code: str, description: str, mutate: Any) -> None:
        doc = copy.deepcopy(valid[base])
        mutate(doc)
        cases[name] = {"description": description, "expect_code": code, "manifest": doc}

    def field(doc: dict[str, Any], s: int = 0, f: int = 0) -> dict[str, Any]:
        return doc["sections"][s]["fields"][f]  # type: ignore[no-any-return]

    case(
        "unsupported-schema-version",
        "simple-text",
        "schema_version_unsupported",
        "A future schema version must be rejected, not best-effort parsed.",
        lambda d: d.update(schema_version=2),
    )
    case(
        "missing-schema-version",
        "simple-text",
        "schema_version_unsupported",
        "Legacy/unversioned manifests are not fillable.",
        lambda d: d.pop("schema_version"),
    )
    case(
        "unknown-field-type",
        "simple-text",
        "field_type_unsupported",
        "An unknown field type is never rendered as a guess.",
        lambda d: field(d).update(type="signature_pad"),
    )
    case(
        "unknown-mapping-type",
        "simple-text",
        "mapping_type_unsupported",
        "An unknown PDF mapping type is rejected.",
        lambda d: field(d)["pdf"][0].update(type="barcode_at"),
    )
    case(
        "unknown-property",
        "simple-text",
        "structure_invalid",
        "Unknown properties are rejected rather than ignored.",
        lambda d: field(d).update(placeholder="x"),
    )
    case(
        "unmapped-field",
        "simple-text",
        "structure_invalid",
        "A field that prints nowhere would silently drop an answer.",
        lambda d: field(d).update(pdf=[]),
    )
    case(
        "duplicate-field-id",
        "required-optional",
        "duplicate_id",
        "Field ids are unique across the whole manifest.",
        lambda d: field(d, 0, 1).update(id="full_name"),
    )
    case(
        "float-coordinate",
        "simple-text",
        "number_not_integer",
        "Only integers serialise identically in both languages.",
        lambda d: field(d)["pdf"][0].update(x=76.5),
    )
    case(
        "select-without-options",
        "dropdown-checkbox",
        "options_invalid",
        "A select must declare its options.",
        lambda d: field(d).pop("options"),
    )
    case(
        "default-not-an-option",
        "dropdown-checkbox",
        "default_invalid",
        "A default must be one of the declared options.",
        lambda d: field(d).update(default="courier"),
    )
    case(
        "arbitrary-regex",
        "required-optional",
        "validation_not_applicable",
        "Only named patterns are accepted; arbitrary regex is refused.",
        lambda d: field(d)["validation"].update(pattern="^(a+)+$"),
    )
    case(
        "min-greater-than-max",
        "date-number",
        "validation_not_applicable",
        "Contradictory numeric bounds are rejected.",
        lambda d: field(d, 0, 1)["validation"].update(min=30),
    )
    case(
        "condition-on-later-field",
        "conditional",
        "condition_invalid",
        "visible_when may only reference a field defined earlier (no cycles).",
        lambda d: field(d, 0, 0).update(visible_when={"field": "urgent", "op": "is_checked"}),
    )
    case(
        "condition-value-not-an-option",
        "conditional",
        "condition_invalid",
        "A condition value must be one of the referenced field's options.",
        lambda d: field(d, 0, 1)["visible_when"].update(value="drone"),
    )
    case(
        "date-without-format",
        "date-number",
        "mapping_not_applicable",
        "A date printed as text must declare its format.",
        lambda d: field(d)["pdf"][0].pop("date_format"),
    )
    case(
        "checkbox-as-text",
        "dropdown-checkbox",
        "mapping_not_applicable",
        "A checkbox cannot be printed through a text mapping.",
        lambda d: field(d, 0, 1).update(pdf=[text_at(76, 607, 100, 14)]),
    )
    case(
        "page-out-of-range",
        "simple-text",
        "pdf_page_out_of_range",
        "A mapping cannot target a page beyond identity.page_count.",
        lambda d: field(d)["pdf"][0].update(page=2),
    )
    case(
        "rule-unknown-type",
        "conditional",
        "rule_invalid",
        "Unknown cross-field rule types are rejected.",
        lambda d: d["rules"][0].update(type="sum_equals"),
    )
    case(
        "rule-compares-non-dates",
        "fillable-acroform",
        "rule_invalid",
        "date_not_before must compare two date fields.",
        lambda d: d["rules"][0].update(other_field="full_name"),
    )
    return cases


def build_canonical_vectors() -> list[dict[str, Any]]:
    values: list[tuple[str, Any]] = [
        ("empty-object", {}),
        ("key-order", {"b": 1, "a": 2, "aa": 3, "B": 4}),
        ("nested", {"z": [3, {"y": None, "x": True}], "a": {"c": False, "b": []}}),
        ("integers", {"zero": 0, "neg": -7, "big": 9007199254740991}),
        ("ascii-escapes", {"s": 'quote " backslash \\ slash / tab \t newline \n cr \r'}),
        ("control-chars", {"s": "\u0000\u0001\u001f\u007f\b\f"}),
        ("non-ascii-bmp", {"name": "फ़ॉर्म", "city": "Bengaluru – ಬೆಂಗಳೂರು", "sym": "₹ é ñ"}),
        ("astral", {"emoji": "😀", "key😀": "v"}),
        ("non-ascii-key-order", {"é": 1, "z": 2, "ಕ": 3, "😀": 4, "￿": 5}),
        ("html-sensitive", {"s": "<script>&'</script>  "}),
    ]
    vectors = []
    for name, value in values:
        canonical = canonical_manifest_json(value)
        vectors.append(
            {
                "name": name,
                "value": value,
                "canonical": canonical,
                "sha256": sha256(canonical.encode("utf-8")),
            }
        )
    return vectors


def build_signature_vectors() -> dict[str, Any]:
    key = Ed25519PrivateKey.from_private_bytes(TEST_SEED)
    public = base64.b64encode(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
    ).decode()
    common: dict[str, Any] = {
        "template_id": "11111111-1111-4111-8111-111111111111",
        "semver": "1.0.0",
        "blank_pdf_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "page_count": 3,
    }
    v2_fields: dict[str, Any] = {
        **common,
        "slug": "ror-7-12",
        "jurisdiction": "KARNATAKA_STATE",
        "category": "REVENUE",
        "official_form_code": "7/12",
        "template_version_id": "22222222-2222-4222-8222-222222222222",
    }
    # A second v2 vector with a delimiter inside a field and multi-byte text, to
    # prove the length prefix counts UTF-8 bytes (not UTF-16 units).
    v2_tricky = {**v2_fields, "official_form_code": "फ़ॉर्म|7/12", "page_count": 12}

    def sign(digest: str) -> str:
        return base64.b64encode(key.sign(digest.encode("ascii"))).decode()

    def v2(fields: dict[str, Any]) -> dict[str, Any]:
        digest = template_bundle_digest_v2(
            template_id=fields["template_id"],
            slug=fields["slug"],
            jurisdiction=fields["jurisdiction"],
            category=fields["category"],
            official_form_code=fields["official_form_code"],
            semver=fields["semver"],
            published_version_id=fields["template_version_id"],
            blank_pdf_sha256=fields["blank_pdf_sha256"],
            manifest_sha256=fields["manifest_sha256"],
            page_count=fields["page_count"],
        )
        return {
            "fields": {**fields, "signature_format_version": 2},
            "digest": digest,
            "signature": sign(digest),
        }

    v1_digest = template_bundle_digest_v1(
        template_id=v2_fields["template_id"],
        semver=v2_fields["semver"],
        blank_pdf_sha256=v2_fields["blank_pdf_sha256"],
        manifest_sha256=v2_fields["manifest_sha256"],
        page_count=3,
    )
    return {
        "warning": "TEST KEY ONLY. Never pin this key in a staging or production build.",
        "kid": TEST_KID,
        "public_key_b64": public,
        "v1": {
            "fields": {
                **v2_fields,
                "signature_format_version": 1,
            },
            "digest": v1_digest,
            "signature": sign(v1_digest),
        },
        "v2": v2(v2_fields),
        "v2_multibyte_and_delimiter": v2(v2_tricky),
    }


def build_fill_cases() -> dict[str, Any]:
    """Answer sets with the validation outcome and printed values they must yield.

    Only the frontend fills PDFs; these pin its behaviour to the contract text.
    ``today`` is injected so date rules are deterministic.
    """
    address = {
        "line1": "12 Test Lane",
        "line2": "",
        "city": "Sampleville",
        "district": "North",
        "state": "Karnataka",
        "pincode": "560001",
    }
    good = {
        "full_name": "Asha Test",
        "date_of_birth": "1990-02-28",
        "contact_method": "phone",
        "mobile": "9876543210",
        "home_address": address,
        "service_type": "priority",
        "fee_waiver": True,
        "dependants": 2,
        "remarks": "Line one\nLine two",
        "agree": True,
        "declaration_date": "2026-11-10",
    }
    return {
        "today": "2026-11-10",
        "cases": [
            {
                "name": "fillable-complete",
                "manifest": "fillable-acroform",
                "answers": good,
                "expect_errors": {},
                "expect_visible": [
                    "full_name",
                    "date_of_birth",
                    "contact_method",
                    "mobile",
                    "home_address",
                    "service_type",
                    "fee_waiver",
                    "dependants",
                    "remarks",
                    "agree",
                    "declaration_date",
                ],
                "expect_print": [
                    {"target": "applicant.full_name", "kind": "text", "text": "ASHA TEST"},
                    {"target": "applicant.dob", "kind": "text", "text": "28/02/1990"},
                    {"target": "applicant.contact_method", "kind": "radio", "select": "phone"},
                    {"target": "applicant.mobile", "kind": "text", "text": "9876543210"},
                    {
                        "target": "address.full",
                        "kind": "text",
                        "text": "12 Test Lane\nSampleville, North\nKarnataka - 560001",
                    },
                    {"target": "address.pincode", "kind": "text", "text": "560001"},
                    {"target": "details.service_type", "kind": "dropdown", "select": "PRIORITY"},
                    {"target": "details.fee_waiver", "kind": "checkbox", "checked": True},
                    {"target": "details.dependants", "kind": "text", "text": "2"},
                    {"target": "details.remarks", "kind": "text", "text": "Line one\nLine two"},
                    {"target": "declaration.agree", "kind": "checkbox", "checked": True},
                    {"target": "declaration.date", "kind": "text", "text": "10-11-2026"},
                ],
            },
            {
                "name": "fillable-hidden-fields-not-printed",
                "manifest": "fillable-acroform",
                "answers": {
                    **good,
                    "contact_method": "post",
                    "fee_waiver": False,
                    # Stale answers for now-hidden fields must be ignored entirely.
                    "mobile": "not-a-number",
                    "dependants": 99,
                },
                "expect_errors": {},
                "expect_visible": [
                    "full_name",
                    "date_of_birth",
                    "contact_method",
                    "home_address",
                    "service_type",
                    "fee_waiver",
                    "remarks",
                    "agree",
                    "declaration_date",
                ],
                "expect_print_absent": ["applicant.mobile", "details.dependants"],
                "expect_print": [
                    {"target": "applicant.contact_method", "kind": "radio", "select": "post"},
                    {"target": "details.fee_waiver", "kind": "checkbox", "checked": False},
                ],
            },
            {
                "name": "fillable-errors",
                "manifest": "fillable-acroform",
                "answers": {
                    "full_name": "A",
                    "date_of_birth": "2027-01-01",
                    "contact_method": "phone",
                    "mobile": "12345",
                    "home_address": {**address, "pincode": "012345", "line1": ""},
                    "service_type": "teleport",
                    "fee_waiver": True,
                    "dependants": 21,
                    "agree": False,
                    "declaration_date": "2026-11-10",
                },
                "expect_errors": {
                    "full_name": "min_length",
                    "date_of_birth": "not_future",
                    "mobile": "pattern",
                    "home_address": "address_incomplete",
                    "service_type": "not_an_option",
                    "dependants": "max",
                    "agree": "required",
                    "declaration_date": "rule:declared_after_birth",
                },
            },
            {
                "name": "fillable-empty",
                "manifest": "fillable-acroform",
                "answers": {},
                "expect_errors": {
                    "full_name": "required",
                    "date_of_birth": "required",
                    "contact_method": "required",
                    "home_address": "required",
                    "service_type": "required",
                    "agree": "required",
                    "declaration_date": "required",
                },
            },
            {
                "name": "conditional-required-if",
                "manifest": "conditional",
                "answers": {"delivery": "post", "urgent": True},
                "expect_errors": {
                    "postal_pincode": "required",
                    "reason": "rule:urgent_needs_reason",
                },
                "expect_visible": ["delivery", "postal_pincode", "urgent", "reason"],
            },
            {
                "name": "conditional-collect",
                "manifest": "conditional",
                "answers": {"delivery": "collect"},
                "expect_errors": {},
                "expect_visible": ["delivery", "urgent"],
                "expect_print": [
                    {"target": "mark:0:73:631", "kind": "mark", "mark": "cross"},
                ],
                "expect_print_absent": ["mark:0:163:631", "mark:0:73:607"],
            },
            {
                "name": "coordinate-complete",
                "manifest": "coordinate-mapped",
                "answers": {
                    "full_name": "Ravi Example",
                    "event_date": "2026-01-05",
                    "count": 7,
                    "delivery": "post",
                    "urgent": True,
                    "home_address": {**address, "line2": "Block B"},
                    "notes": "Fictional note.",
                },
                "expect_errors": {},
                "expect_print": [
                    {"target": "text:0:76:723", "kind": "text", "text": "Ravi Example"},
                    {"target": "text:0:76:677", "kind": "text", "text": "05.01.2026"},
                    {"target": "text:0:244:677", "kind": "text", "text": "7"},
                    {"target": "mark:0:163:631", "kind": "mark", "mark": "cross"},
                    {"target": "mark:0:73:607", "kind": "mark", "mark": "check"},
                    {
                        "target": "text:0:76:508",
                        "kind": "text",
                        "text": "12 Test Lane\nBlock B\nSampleville, North\nKarnataka - 560001",
                    },
                    {"target": "text:0:76:463", "kind": "text", "text": "560001"},
                    {"target": "text:1:76:626", "kind": "text", "text": "Fictional note."},
                ],
                "expect_print_absent": ["mark:0:73:631"],
            },
        ],
    }


def dump(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False) + "\n").encode()


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    valid = build_valid()
    for name, doc in valid.items():
        issues = validate_manifest(doc)
        if issues:
            raise SystemExit(f"valid fixture {name} is invalid: {issues}")
        files[f"manifests/valid/{name}.json"] = dump(doc)
    for name, wrapped in build_invalid(valid).items():
        codes = {issue.code for issue in validate_manifest(wrapped["manifest"])}
        if wrapped["expect_code"] not in codes:
            raise SystemExit(
                f"invalid fixture {name}: expected {wrapped['expect_code']}, got {codes}"
            )
        files[f"manifests/invalid/{name}.json"] = dump(wrapped)
    files["vectors/canonical-json.json"] = dump(build_canonical_vectors())
    files["vectors/signature.json"] = dump(build_signature_vectors())
    files["vectors/fill-cases.json"] = dump(build_fill_cases())
    files["vectors/manifest-digests.json"] = dump(
        {name: sha256(canonical_manifest_json(doc).encode("utf-8")) for name, doc in valid.items()}
    )
    for pdf in sorted((ROOT / "pdfs").glob("*.pdf")):
        files[f"pdfs/{pdf.name}"] = pdf.read_bytes()
    index = {path: sha256(data) for path, data in sorted(files.items())}
    files["index.json"] = dump(
        {
            "note": "TEST FIXTURES ONLY - fictional data, not government forms.",
            "contract": "form-manifest-v1",
            "files": index,
        }
    )
    return files


def main() -> None:
    files = build()
    check = "--check" in sys.argv
    stale = []
    for path, data in files.items():
        target = ROOT / path
        if check:
            if not target.exists() or target.read_bytes() != data:
                stale.append(path)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    if check and stale:
        raise SystemExit(f"contract fixtures are out of date: {stale}")
    print(f"{'checked' if check else 'wrote'} {len(files)} fixture files in {ROOT}")


if __name__ == "__main__":
    main()
