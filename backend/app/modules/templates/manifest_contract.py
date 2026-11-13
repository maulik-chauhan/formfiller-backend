"""Form-manifest contract v1 — server-side validator.

Authoritative specification: ``docs/contracts/form-manifest-v1.md`` (repo root).
The frontend carries a line-for-line port in
``src/features/forms/manifest.ts``; both are exercised against the shared fixtures
in ``docs/contracts/fixtures`` so the two implementations cannot drift silently.

The validator is deliberately hand-written over plain JSON values (not Pydantic
models) so that every rejection maps to one stable, documented issue ``code`` that
is identical in Python and TypeScript.

Nothing here ever sees applicant data: a manifest describes the *shape* of a form
and where answers are printed on the blank PDF. Answers never reach the backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from app.core.errors import AppError, ProblemDetail

SCHEMA_VERSION = 1
MANIFEST_KIND = "govforms.form-manifest"

FIELD_TYPES = ("text", "multiline", "number", "date", "select", "radio", "checkbox", "address")
MAPPING_TYPES = ("acro_text", "acro_checkbox", "acro_radio", "acro_dropdown", "text_at", "mark_at")
#: Named, pre-reviewed patterns. Arbitrary regular expressions are never accepted
#: from a manifest (ReDoS / cross-engine divergence).
PATTERNS: dict[str, str] = {
    "digits": r"^[0-9]+$",
    "alpha_space": r"^[A-Za-z][A-Za-z .'-]*$",
    "alphanumeric": r"^[A-Za-z0-9]+$",
    "pincode_in": r"^[1-9][0-9]{5}$",
    "mobile_in": r"^[6-9][0-9]{9}$",
    "email": r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
}
DATE_FORMATS = ("DD/MM/YYYY", "DD-MM-YYYY", "DD.MM.YYYY", "YYYY-MM-DD")
FONTS = ("Helvetica", "Helvetica-Bold", "Times-Roman", "Courier")
ALIGNMENTS = ("left", "center", "right")
ADDRESS_COMPONENTS = ("line1", "line2", "city", "district", "state", "pincode")
ADDRESS_SOURCES = (*ADDRESS_COMPONENTS, "full")
OPTION_TEXT = ("label", "value")
TRANSFORMS = ("none", "uppercase")
MARKS = ("check", "cross")
RULE_TYPES = ("date_not_before", "required_if")

MAX_SECTIONS = 50
MAX_FIELDS_PER_SECTION = 200
MAX_FIELDS_TOTAL = 500
MAX_OPTIONS = 200
MAX_MAPPINGS_PER_FIELD = 20
MAX_RULES = 50
MAX_TEXT_LENGTH = 5000
MAX_SAFE_INTEGER = 2**53 - 1

_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_OPTION_VALUE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PROFILE_KEY = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_ISO_DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")

_TEXTUAL_TYPES = frozenset({"text", "multiline", "number", "date", "select", "radio", "address"})
_CHOICE_TYPES = frozenset({"select", "radio"})


@dataclass(frozen=True)
class Issue:
    code: str
    path: str
    message: str


class ManifestValidationError(AppError):
    status_code = 422
    code = "MANIFEST_VALIDATION_FAILED"
    title = "Template manifest failed contract validation"

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        shown = "; ".join(f"[{i.code}] {i.path}: {i.message}" for i in issues[:10])
        more = f" (+{len(issues) - 10} more)" if len(issues) > 10 else ""
        super().__init__(shown + more)

    def to_problem(self, instance: str | None = None) -> ProblemDetail:
        problem = super().to_problem(instance)
        problem.errors = [
            {"field": i.path, "code": i.code, "message": i.message} for i in self.issues[:100]
        ]
        return problem


@dataclass(frozen=True)
class PdfFieldInfo:
    """Shape of one AcroForm field in the blank PDF (no values — it is blank)."""

    kind: str  # "text" | "checkbox" | "radio" | "dropdown" | "other"
    states: tuple[str, ...] = ()


@dataclass(frozen=True)
class PdfPageInfo:
    width: float
    height: float
    rotation: int


@dataclass(frozen=True)
class PdfFormInfo:
    pages: tuple[PdfPageInfo, ...]
    fields: dict[str, PdfFieldInfo] = field(default_factory=dict)


@dataclass(frozen=True)
class TemplateIdentity:
    slug: str
    jurisdiction: str
    category: str
    official_form_code: str
    semver: str
    page_count: int
    blank_pdf_sha256: str


# --------------------------------------------------------------------------- #
# Small typed helpers
# --------------------------------------------------------------------------- #
def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value: Any, *, min_len: int = 1, max_len: int = MAX_TEXT_LENGTH) -> TypeGuard[str]:
    return isinstance(value, str) and min_len <= len(value) <= max_len


def _valid_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not _ISO_DATE.match(value):
        return False
    year, month, day = (int(part) for part in value.split("-"))
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return 1 <= day <= days


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(self, code: str, path: str, message: str) -> None:
        self.issues.append(Issue(code, path, message))

    def keys(
        self,
        obj: dict[str, Any],
        path: str,
        *,
        required: tuple[str, ...],
        optional: tuple[str, ...],
    ) -> bool:
        ok = True
        for key in required:
            if key not in obj:
                self.add("structure_invalid", f"{path}.{key}", "is required")
                ok = False
        allowed = set(required) | set(optional)
        for key in obj:
            if key not in allowed:
                self.add("structure_invalid", f"{path}.{key}", "is not a recognised property")
                ok = False
        return ok


def _scan_numbers(value: Any, path: str, out: _Collector) -> None:
    """Reject floats and unsafe integers anywhere in the document.

    The manifest digest is computed over canonical JSON in two languages; only
    integers within the IEEE-754 safe range serialise identically in both.
    """
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, float):
        out.add("number_not_integer", path, "numbers must be integers")
    elif isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            out.add("number_not_integer", path, "integer is outside the safe range")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_numbers(item, f"{path}[{index}]", out)
    elif isinstance(value, dict):
        for key, item in value.items():
            _scan_numbers(item, f"{path}.{key}", out)


# --------------------------------------------------------------------------- #
# Structural + semantic validation (no PDF needed)
# --------------------------------------------------------------------------- #
def validate_manifest(manifest: Any) -> list[Issue]:
    """Return every contract violation in ``manifest`` (empty list == valid)."""
    out = _Collector()
    if not isinstance(manifest, dict):
        out.add("structure_invalid", "$", "manifest must be a JSON object")
        return out.issues
    _scan_numbers(manifest, "$", out)

    version = manifest.get("schema_version")
    if not _is_int(version) or version != SCHEMA_VERSION:
        out.add(
            "schema_version_unsupported",
            "$.schema_version",
            f"only schema_version {SCHEMA_VERSION} is supported",
        )
        return out.issues
    if manifest.get("kind") != MANIFEST_KIND:
        out.add("structure_invalid", "$.kind", f"must be {MANIFEST_KIND!r}")
    out.keys(
        manifest,
        "$",
        required=("schema_version", "kind", "identity", "sections"),
        optional=("rules",),
    )

    page_count = _validate_identity(manifest.get("identity"), out)

    sections = manifest.get("sections")
    fields_by_id: dict[str, dict[str, Any]] = {}
    if not isinstance(sections, list) or not 1 <= len(sections) <= MAX_SECTIONS:
        out.add("structure_invalid", "$.sections", f"must list 1..{MAX_SECTIONS} sections")
    else:
        section_ids: set[str] = set()
        for s_index, section in enumerate(sections):
            _validate_section(
                section, f"$.sections[{s_index}]", section_ids, fields_by_id, page_count, out
            )
        if len(fields_by_id) > MAX_FIELDS_TOTAL:
            out.add("structure_invalid", "$.sections", f"more than {MAX_FIELDS_TOTAL} fields")

    if "rules" in manifest:
        _validate_rules(manifest["rules"], fields_by_id, out)
    return out.issues


def _validate_identity(identity: Any, out: _Collector) -> int | None:
    path = "$.identity"
    if not isinstance(identity, dict):
        out.add("structure_invalid", path, "must be an object")
        return None
    out.keys(
        identity,
        path,
        required=(
            "slug",
            "jurisdiction",
            "category",
            "official_form_code",
            "semver",
            "page_count",
            "blank_pdf_sha256",
        ),
        optional=(),
    )
    slug = identity.get("slug")
    if not (_is_str(slug, max_len=80) and _SLUG.match(slug)):
        out.add("structure_invalid", f"{path}.slug", "must be a template slug")
    for key, limit in (("jurisdiction", 64), ("category", 64), ("official_form_code", 64)):
        if not _is_str(identity.get(key), max_len=limit):
            out.add("structure_invalid", f"{path}.{key}", "must be a non-empty string")
    semver = identity.get("semver")
    if not (isinstance(semver, str) and _SEMVER.match(semver)):
        out.add("structure_invalid", f"{path}.semver", "must be MAJOR.MINOR.PATCH")
    sha = identity.get("blank_pdf_sha256")
    if not (isinstance(sha, str) and _HEX64.match(sha)):
        out.add("structure_invalid", f"{path}.blank_pdf_sha256", "must be 64 lowercase hex chars")
    page_count = identity.get("page_count")
    if not _is_int(page_count) or page_count < 1:
        out.add("structure_invalid", f"{path}.page_count", "must be an integer >= 1")
        return None
    return int(page_count)


def _validate_section(
    section: Any,
    path: str,
    section_ids: set[str],
    fields_by_id: dict[str, dict[str, Any]],
    page_count: int | None,
    out: _Collector,
) -> None:
    if not isinstance(section, dict):
        out.add("structure_invalid", path, "must be an object")
        return
    out.keys(section, path, required=("id", "title", "fields"), optional=("description",))
    section_id = section.get("id")
    if not (isinstance(section_id, str) and _ID.match(section_id)):
        out.add("structure_invalid", f"{path}.id", "must match ^[a-z][a-z0-9_]{0,63}$")
    elif section_id in section_ids:
        out.add("duplicate_id", f"{path}.id", f"section id {section_id!r} is used twice")
    else:
        section_ids.add(section_id)
    if not _is_str(section.get("title"), max_len=200):
        out.add("structure_invalid", f"{path}.title", "must be a 1..200 character string")
    if "description" in section and not _is_str(section["description"], max_len=1000):
        out.add("structure_invalid", f"{path}.description", "must be a 1..1000 character string")
    fields = section.get("fields")
    if not isinstance(fields, list) or not 1 <= len(fields) <= MAX_FIELDS_PER_SECTION:
        out.add(
            "structure_invalid", f"{path}.fields", f"must list 1..{MAX_FIELDS_PER_SECTION} fields"
        )
        return
    for f_index, item in enumerate(fields):
        _validate_field(item, f"{path}.fields[{f_index}]", fields_by_id, page_count, out)


def _option_values(spec: dict[str, Any]) -> list[str]:
    options = spec.get("options")
    if not isinstance(options, list):
        return []
    return [o["value"] for o in options if isinstance(o, dict) and isinstance(o.get("value"), str)]


def _validate_field(
    spec: Any,
    path: str,
    fields_by_id: dict[str, dict[str, Any]],
    page_count: int | None,
    out: _Collector,
) -> None:
    if not isinstance(spec, dict):
        out.add("structure_invalid", path, "must be an object")
        return
    field_id = spec.get("id")
    id_ok = isinstance(field_id, str) and bool(_ID.match(field_id))
    if not id_ok:
        out.add("structure_invalid", f"{path}.id", "must match ^[a-z][a-z0-9_]{0,63}$")
    elif field_id in fields_by_id:
        out.add("duplicate_id", f"{path}.id", f"field id {field_id!r} is used twice")
        id_ok = False

    field_type = spec.get("type")
    if field_type not in FIELD_TYPES:
        # Unknown types are never rendered as a best guess: the whole manifest fails.
        out.add(
            "field_type_unsupported", f"{path}.type", f"field type {field_type!r} is not supported"
        )
        return

    out.keys(
        spec,
        path,
        required=("id", "type", "label", "required", "pdf"),
        optional=(
            "help",
            "default",
            "options",
            "validation",
            "visible_when",
            "profile_key",
            "profile_keys",
        ),
    )
    if not _is_str(spec.get("label"), max_len=200):
        out.add("structure_invalid", f"{path}.label", "must be a 1..200 character string")
    if "help" in spec and not _is_str(spec["help"], max_len=500):
        out.add("structure_invalid", f"{path}.help", "must be a 1..500 character string")
    if not isinstance(spec.get("required"), bool):
        out.add("structure_invalid", f"{path}.required", "must be true or false")

    _validate_options(spec, field_type, path, out)
    _validate_default(spec, field_type, path, out)
    _validate_field_validation(spec, field_type, path, out)
    if "visible_when" in spec:
        _validate_condition(spec["visible_when"], f"{path}.visible_when", fields_by_id, out)
    _validate_profile_keys(spec, field_type, path, out)

    mappings = spec.get("pdf")
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= MAX_MAPPINGS_PER_FIELD:
        out.add(
            "structure_invalid",
            f"{path}.pdf",
            f"must list 1..{MAX_MAPPINGS_PER_FIELD} PDF mappings (unmapped fields are not allowed)",
        )
    else:
        for m_index, mapping in enumerate(mappings):
            _validate_mapping(mapping, spec, field_type, f"{path}.pdf[{m_index}]", page_count, out)

    # Registered last so a field can never reference itself in ``visible_when``.
    if id_ok:
        fields_by_id[str(field_id)] = spec


def _validate_options(spec: dict[str, Any], field_type: str, path: str, out: _Collector) -> None:
    if field_type not in _CHOICE_TYPES:
        if "options" in spec:
            out.add("options_invalid", f"{path}.options", f"not allowed on a {field_type} field")
        return
    options = spec.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= MAX_OPTIONS:
        out.add("options_invalid", f"{path}.options", f"must list 1..{MAX_OPTIONS} options")
        return
    seen: set[str] = set()
    for index, option in enumerate(options):
        o_path = f"{path}.options[{index}]"
        if not isinstance(option, dict) or set(option) != {"value", "label"}:
            out.add("options_invalid", o_path, "must be exactly {value, label}")
            continue
        value = option["value"]
        if not (isinstance(value, str) and _OPTION_VALUE.match(value)):
            out.add("options_invalid", f"{o_path}.value", "must match ^[A-Za-z0-9_.-]{1,64}$")
        elif value in seen:
            out.add("options_invalid", f"{o_path}.value", f"option {value!r} is listed twice")
        else:
            seen.add(value)
        if not _is_str(option["label"], max_len=200):
            out.add("options_invalid", f"{o_path}.label", "must be a 1..200 character string")


def _validate_default(spec: dict[str, Any], field_type: str, path: str, out: _Collector) -> None:
    if "default" not in spec:
        return
    default = spec["default"]
    d_path = f"{path}.default"
    if field_type in ("text", "multiline"):
        if not _is_str(default):
            out.add("default_invalid", d_path, "must be a non-empty string")
    elif field_type == "number":
        if not _is_int(default):
            out.add("default_invalid", d_path, "must be an integer")
    elif field_type == "checkbox":
        if not isinstance(default, bool):
            out.add("default_invalid", d_path, "must be true or false")
    elif field_type in _CHOICE_TYPES:
        if default not in _option_values(spec):
            out.add("default_invalid", d_path, "must be one of the option values")
    else:
        out.add("default_invalid", d_path, f"defaults are not supported on a {field_type} field")


def _validate_field_validation(
    spec: dict[str, Any], field_type: str, path: str, out: _Collector
) -> None:
    if "validation" not in spec:
        return
    rules = spec["validation"]
    v_path = f"{path}.validation"
    if not isinstance(rules, dict):
        out.add("validation_not_applicable", v_path, "must be an object")
        return
    allowed: tuple[str, ...]
    if field_type == "text":
        allowed = ("min_length", "max_length", "pattern")
    elif field_type == "multiline":
        allowed = ("min_length", "max_length")
    elif field_type == "number":
        allowed = ("min", "max")
    elif field_type == "date":
        allowed = ("min_date", "max_date", "not_future", "not_past")
    else:
        allowed = ()
    for key in rules:
        if key not in allowed:
            out.add(
                "validation_not_applicable", f"{v_path}.{key}", f"not valid on a {field_type} field"
            )
    for key in ("min_length", "max_length"):
        if key in rules and key in allowed:
            value = rules[key]
            if not _is_int(value) or not 0 <= value <= MAX_TEXT_LENGTH:
                out.add(
                    "validation_not_applicable",
                    f"{v_path}.{key}",
                    f"must be an integer 0..{MAX_TEXT_LENGTH}",
                )
    if (
        _is_int(rules.get("min_length"))
        and _is_int(rules.get("max_length"))
        and rules["min_length"] > rules["max_length"]
    ):
        out.add("validation_not_applicable", v_path, "min_length exceeds max_length")
    if "pattern" in rules and "pattern" in allowed and rules["pattern"] not in PATTERNS:
        out.add(
            "validation_not_applicable",
            f"{v_path}.pattern",
            "must name a supported pattern (arbitrary regular expressions are not accepted)",
        )
    for key in ("min", "max"):
        if key in rules and key in allowed and not _is_int(rules[key]):
            out.add("validation_not_applicable", f"{v_path}.{key}", "must be an integer")
    if _is_int(rules.get("min")) and _is_int(rules.get("max")) and rules["min"] > rules["max"]:
        out.add("validation_not_applicable", v_path, "min exceeds max")
    for key in ("min_date", "max_date"):
        if key in rules and key in allowed and not _valid_iso_date(rules[key]):
            out.add("validation_not_applicable", f"{v_path}.{key}", "must be a YYYY-MM-DD date")
    if (
        _valid_iso_date(rules.get("min_date"))
        and _valid_iso_date(rules.get("max_date"))
        and rules["min_date"] > rules["max_date"]
    ):
        out.add("validation_not_applicable", v_path, "min_date is after max_date")
    for key in ("not_future", "not_past"):
        if key in rules and key in allowed and not isinstance(rules[key], bool):
            out.add("validation_not_applicable", f"{v_path}.{key}", "must be true or false")
    if rules.get("not_future") is True and rules.get("not_past") is True:
        out.add("validation_not_applicable", v_path, "not_future and not_past cannot both be set")


def _validate_condition(
    condition: Any, path: str, fields_by_id: dict[str, dict[str, Any]], out: _Collector
) -> None:
    if not isinstance(condition, dict):
        out.add("condition_invalid", path, "must be an object")
        return
    target = fields_by_id.get(condition.get("field", ""))
    if not isinstance(condition.get("field"), str) or target is None:
        out.add(
            "condition_invalid",
            f"{path}.field",
            "must reference a field defined earlier in the manifest",
        )
        return
    op = condition.get("op")
    target_type = target.get("type")
    values = _option_values(target)
    if target_type == "checkbox":
        if op not in ("is_checked", "is_not_checked") or set(condition) != {"field", "op"}:
            out.add(
                "condition_invalid",
                path,
                "a checkbox condition must be exactly {field, op: is_checked|is_not_checked}",
            )
    elif target_type in _CHOICE_TYPES:
        if op in ("equals", "not_equals"):
            if set(condition) != {"field", "op", "value"} or condition["value"] not in values:
                out.add(
                    "condition_invalid", path, "value must be one of the referenced field's options"
                )
        elif op == "in":
            chosen = condition.get("values")
            if (
                set(condition) != {"field", "op", "values"}
                or not isinstance(chosen, list)
                or not chosen
                or any(v not in values for v in chosen)
                or len(set(chosen)) != len(chosen)
            ):
                out.add(
                    "condition_invalid",
                    path,
                    "values must be a non-empty list of the referenced field's options",
                )
        else:
            out.add("condition_invalid", f"{path}.op", "must be equals, not_equals or in")
    else:
        out.add(
            "condition_invalid",
            f"{path}.field",
            "conditions may only reference select, radio or checkbox fields",
        )


def _validate_profile_keys(
    spec: dict[str, Any], field_type: str, path: str, out: _Collector
) -> None:
    if "profile_key" in spec:
        key = spec["profile_key"]
        if field_type in ("checkbox", "address"):
            out.add(
                "structure_invalid", f"{path}.profile_key", f"not allowed on a {field_type} field"
            )
        elif not (isinstance(key, str) and _PROFILE_KEY.match(key)):
            out.add("structure_invalid", f"{path}.profile_key", "must match ^[a-z0-9][a-z0-9_]*$")
    if "profile_keys" in spec:
        keys = spec["profile_keys"]
        p_path = f"{path}.profile_keys"
        if field_type != "address":
            out.add("structure_invalid", p_path, "only allowed on an address field")
        elif not isinstance(keys, dict) or not keys:
            out.add("structure_invalid", p_path, "must map address components to profile keys")
        else:
            for component, key in keys.items():
                if component not in ADDRESS_COMPONENTS:
                    out.add("structure_invalid", f"{p_path}.{component}", "unknown component")
                elif not (isinstance(key, str) and _PROFILE_KEY.match(key)):
                    out.add("structure_invalid", f"{p_path}.{component}", "invalid profile key")


def _validate_text_attrs(
    mapping: dict[str, Any], field_type: str, path: str, out: _Collector
) -> None:
    """Attributes shared by the two text-producing mapping types."""
    if field_type == "address":
        if mapping.get("source") not in ADDRESS_SOURCES:
            out.add(
                "mapping_not_applicable",
                f"{path}.source",
                "an address mapping must name the component it prints (or 'full')",
            )
    elif "source" in mapping:
        out.add("mapping_not_applicable", f"{path}.source", "only valid for an address field")
    if field_type == "date":
        if mapping.get("date_format") not in DATE_FORMATS:
            out.add(
                "mapping_not_applicable",
                f"{path}.date_format",
                "a date mapping must declare a supported date_format",
            )
    elif "date_format" in mapping:
        out.add("mapping_not_applicable", f"{path}.date_format", "only valid for a date field")
    if field_type in _CHOICE_TYPES:
        if mapping.get("option_text") not in OPTION_TEXT:
            out.add(
                "mapping_not_applicable",
                f"{path}.option_text",
                "a select/radio text mapping must print either 'label' or 'value'",
            )
    elif "option_text" in mapping:
        out.add("mapping_not_applicable", f"{path}.option_text", "only valid for select/radio")
    if "transform" in mapping:
        if field_type not in ("text", "multiline", "address"):
            out.add(
                "mapping_not_applicable",
                f"{path}.transform",
                "only valid for text, multiline and address fields",
            )
        elif mapping["transform"] not in TRANSFORMS:
            out.add("structure_invalid", f"{path}.transform", "must be 'none' or 'uppercase'")


def _validate_on_value(
    mapping: dict[str, Any], spec: dict[str, Any], field_type: str, path: str, out: _Collector
) -> None:
    if field_type == "checkbox":
        if "on_value" in mapping:
            out.add("mapping_not_applicable", f"{path}.on_value", "not valid for a checkbox field")
    elif field_type in _CHOICE_TYPES:
        if mapping.get("on_value") not in _option_values(spec):
            out.add(
                "mapping_not_applicable",
                f"{path}.on_value",
                "must name the option that turns this mark on",
            )
    else:
        out.add(
            "mapping_not_applicable",
            path,
            f"a {field_type} field cannot be printed as a check mark",
        )


def _validate_export_map(
    mapping: dict[str, Any], spec: dict[str, Any], field_type: str, path: str, out: _Collector
) -> None:
    if field_type not in _CHOICE_TYPES:
        out.add(
            "mapping_not_applicable", path, f"a {field_type} field cannot use {mapping['type']}"
        )
        return
    exports = mapping.get("options")
    values = _option_values(spec)
    if (
        not isinstance(exports, dict)
        or set(exports) != set(values)
        or any(not _is_str(v, max_len=64) for v in exports.values())
    ):
        out.add(
            "mapping_not_applicable",
            f"{path}.options",
            "must map every option value of the field to a PDF export value",
        )


def _validate_box(
    mapping: dict[str, Any], keys: tuple[str, ...], path: str, out: _Collector
) -> None:
    for key in keys:
        value = mapping.get(key)
        minimum = 1 if key in ("width", "height") else 0
        if not _is_int(value) or not minimum <= value <= 20_000:
            out.add(
                "structure_invalid",
                f"{path}.{key}",
                f"must be an integer number of PDF points >= {minimum}",
            )


def _validate_page(
    mapping: dict[str, Any], path: str, page_count: int | None, out: _Collector
) -> None:
    page = mapping.get("page")
    if not _is_int(page) or page < 0:
        out.add("structure_invalid", f"{path}.page", "must be a zero-based page index")
    elif page_count is not None and page >= page_count:
        out.add(
            "pdf_page_out_of_range",
            f"{path}.page",
            f"page index {page} does not exist (page_count is {page_count})",
        )


def _validate_mapping(
    mapping: Any,
    spec: dict[str, Any],
    field_type: str,
    path: str,
    page_count: int | None,
    out: _Collector,
) -> None:
    if not isinstance(mapping, dict):
        out.add("structure_invalid", path, "must be an object")
        return
    mapping_type = mapping.get("type")
    if mapping_type not in MAPPING_TYPES:
        out.add(
            "mapping_type_unsupported",
            f"{path}.type",
            f"PDF mapping type {mapping_type!r} is not supported",
        )
        return

    text_optional = ("source", "date_format", "option_text", "transform")
    if mapping_type == "acro_text":
        out.keys(mapping, path, required=("type", "field_name"), optional=text_optional)
        _validate_field_name(mapping, path, out)
        if field_type not in _TEXTUAL_TYPES:
            out.add(
                "mapping_not_applicable", path, f"a {field_type} field cannot be printed as text"
            )
        else:
            _validate_text_attrs(mapping, field_type, path, out)
    elif mapping_type == "text_at":
        out.keys(
            mapping,
            path,
            required=(
                "type",
                "page",
                "x",
                "y",
                "width",
                "height",
                "font",
                "size",
                "align",
                "multiline",
            ),
            optional=(*text_optional, "line_height"),
        )
        _validate_page(mapping, path, page_count, out)
        _validate_box(mapping, ("x", "y", "width", "height"), path, out)
        if mapping.get("font") not in FONTS:
            out.add("structure_invalid", f"{path}.font", f"must be one of {', '.join(FONTS)}")
        size = mapping.get("size")
        if not _is_int(size) or not 4 <= size <= 72:
            out.add("structure_invalid", f"{path}.size", "must be an integer 4..72")
        if mapping.get("align") not in ALIGNMENTS:
            out.add("structure_invalid", f"{path}.align", "must be left, center or right")
        if not isinstance(mapping.get("multiline"), bool):
            out.add("structure_invalid", f"{path}.multiline", "must be true or false")
        if "line_height" in mapping:
            line_height = mapping["line_height"]
            if mapping.get("multiline") is not True:
                out.add(
                    "mapping_not_applicable",
                    f"{path}.line_height",
                    "only valid when multiline is true",
                )
            elif not _is_int(line_height) or not (_is_int(size) and size <= line_height <= 200):
                out.add(
                    "structure_invalid",
                    f"{path}.line_height",
                    "must be an integer between the font size and 200",
                )
        if field_type not in _TEXTUAL_TYPES:
            out.add(
                "mapping_not_applicable", path, f"a {field_type} field cannot be printed as text"
            )
        else:
            _validate_text_attrs(mapping, field_type, path, out)
    elif mapping_type == "acro_checkbox":
        out.keys(mapping, path, required=("type", "field_name"), optional=("on_value",))
        _validate_field_name(mapping, path, out)
        _validate_on_value(mapping, spec, field_type, path, out)
    elif mapping_type == "mark_at":
        out.keys(
            mapping,
            path,
            required=("type", "page", "x", "y", "size", "mark"),
            optional=("on_value",),
        )
        _validate_page(mapping, path, page_count, out)
        _validate_box(mapping, ("x", "y"), path, out)
        size = mapping.get("size")
        if not _is_int(size) or not 4 <= size <= 72:
            out.add("structure_invalid", f"{path}.size", "must be an integer 4..72")
        if mapping.get("mark") not in MARKS:
            out.add("structure_invalid", f"{path}.mark", "must be 'check' or 'cross'")
        _validate_on_value(mapping, spec, field_type, path, out)
    else:  # acro_radio | acro_dropdown
        out.keys(mapping, path, required=("type", "field_name", "options"), optional=())
        _validate_field_name(mapping, path, out)
        _validate_export_map(mapping, spec, field_type, path, out)


def _validate_field_name(mapping: dict[str, Any], path: str, out: _Collector) -> None:
    if not _is_str(mapping.get("field_name"), max_len=256):
        out.add("structure_invalid", f"{path}.field_name", "must be the PDF form field's full name")


def _validate_rules(rules: Any, fields_by_id: dict[str, dict[str, Any]], out: _Collector) -> None:
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        out.add("rule_invalid", "$.rules", f"must be a list of at most {MAX_RULES} rules")
        return
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        path = f"$.rules[{index}]"
        if not isinstance(rule, dict):
            out.add("rule_invalid", path, "must be an object")
            continue
        rule_id = rule.get("id")
        if not (isinstance(rule_id, str) and _ID.match(rule_id)):
            out.add("rule_invalid", f"{path}.id", "must match ^[a-z][a-z0-9_]{0,63}$")
        elif rule_id in seen:
            out.add("duplicate_id", f"{path}.id", f"rule id {rule_id!r} is used twice")
        else:
            seen.add(rule_id)
        rule_type = rule.get("type")
        if rule_type not in RULE_TYPES:
            out.add("rule_invalid", f"{path}.type", f"rule type {rule_type!r} is not supported")
            continue
        if not _is_str(rule.get("message"), max_len=300):
            out.add("rule_invalid", f"{path}.message", "must be a 1..300 character string")
        target = (
            fields_by_id.get(rule.get("field", "")) if isinstance(rule.get("field"), str) else None
        )
        if target is None:
            out.add("rule_invalid", f"{path}.field", "must reference a defined field")
            continue
        if rule_type == "date_not_before":
            other = (
                fields_by_id.get(rule.get("other_field", ""))
                if isinstance(rule.get("other_field"), str)
                else None
            )
            if set(rule) != {"id", "type", "field", "other_field", "message"}:
                out.add("rule_invalid", path, "must be {id, type, field, other_field, message}")
            elif (
                other is None
                or target.get("type") != "date"
                or other.get("type") != "date"
                or rule["field"] == rule["other_field"]
            ):
                out.add("rule_invalid", path, "must compare two different date fields")
        else:  # required_if
            if set(rule) != {"id", "type", "field", "when", "message"}:
                out.add("rule_invalid", path, "must be {id, type, field, when, message}")
                continue
            before = len(out.issues)
            _validate_condition(rule["when"], f"{path}.when", fields_by_id, out)
            if len(out.issues) == before and rule["when"].get("field") == rule["field"]:
                out.add("rule_invalid", path, "a field cannot be required by its own value")


# --------------------------------------------------------------------------- #
# Binding checks (need the template record and the blank PDF)
# --------------------------------------------------------------------------- #
def validate_identity(manifest: dict[str, Any], expected: TemplateIdentity) -> list[Issue]:
    """The manifest must describe exactly the template version it is attached to."""
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        return [Issue("identity_mismatch", "$.identity", "missing identity block")]
    issues: list[Issue] = []
    for key in (
        "slug",
        "jurisdiction",
        "category",
        "official_form_code",
        "semver",
        "page_count",
        "blank_pdf_sha256",
    ):
        if identity.get(key) != getattr(expected, key):
            issues.append(
                Issue(
                    "identity_mismatch",
                    f"$.identity.{key}",
                    "does not match the template version this manifest is attached to",
                )
            )
    return issues


def _iter_mappings(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for s_index, section in enumerate(manifest.get("sections", [])):
        for f_index, spec in enumerate(section.get("fields", [])):
            for m_index, mapping in enumerate(spec.get("pdf", [])):
                found.append(
                    (f"$.sections[{s_index}].fields[{f_index}].pdf[{m_index}]", spec, mapping)
                )
    return found


_EXPECTED_PDF_KIND = {
    "acro_text": "text",
    "acro_checkbox": "checkbox",
    "acro_radio": "radio",
    "acro_dropdown": "dropdown",
}


def validate_against_pdf(manifest: dict[str, Any], pdf: PdfFormInfo) -> list[Issue]:
    """Every mapping must target something that really exists in the blank PDF."""
    issues: list[Issue] = []
    text_targets: dict[str, str] = {}
    for path, _spec, mapping in _iter_mappings(manifest):
        mapping_type = mapping["type"]
        if mapping_type in _EXPECTED_PDF_KIND:
            name = mapping["field_name"]
            info = pdf.fields.get(name)
            if info is None:
                issues.append(
                    Issue("pdf_field_missing", f"{path}.field_name", f"PDF has no field {name!r}")
                )
                continue
            expected = _EXPECTED_PDF_KIND[mapping_type]
            if info.kind != expected:
                issues.append(
                    Issue(
                        "pdf_field_type_mismatch",
                        f"{path}.field_name",
                        f"PDF field {name!r} is a {info.kind} field, not a {expected} field",
                    )
                )
                continue
            if mapping_type == "acro_text":
                if name in text_targets:
                    issues.append(
                        Issue(
                            "pdf_field_reused",
                            f"{path}.field_name",
                            f"PDF text field {name!r} is already filled by {text_targets[name]}",
                        )
                    )
                text_targets[name] = path
            if mapping_type in ("acro_radio", "acro_dropdown") and info.states:
                for export in mapping["options"].values():
                    if export not in info.states:
                        issues.append(
                            Issue(
                                "pdf_option_missing",
                                f"{path}.options",
                                f"PDF field {name!r} has no option {export!r}",
                            )
                        )
        else:
            page_index = mapping["page"]
            if page_index >= len(pdf.pages):
                issues.append(
                    Issue("pdf_page_out_of_range", f"{path}.page", "page does not exist in the PDF")
                )
                continue
            page = pdf.pages[page_index]
            if page.rotation % 360 != 0:
                issues.append(
                    Issue(
                        "pdf_page_rotated",
                        f"{path}.page",
                        "coordinate mappings are not supported on rotated pages",
                    )
                )
                continue
            width = mapping.get("width", mapping.get("size", 0))
            height = mapping.get("height", mapping.get("size", 0))
            if mapping["x"] + width > page.width + 0.5 or mapping["y"] + height > page.height + 0.5:
                issues.append(
                    Issue("pdf_rect_out_of_bounds", path, "the mapped area extends beyond the page")
                )
    return issues


def assert_valid_manifest(
    manifest: Any, *, expected: TemplateIdentity, pdf: PdfFormInfo
) -> dict[str, Any]:
    """Run every check; raise :class:`ManifestValidationError` on any violation."""
    issues = validate_manifest(manifest)
    if not issues:
        issues = validate_identity(manifest, expected) + validate_against_pdf(manifest, pdf)
    if issues:
        raise ManifestValidationError(issues)
    result: dict[str, Any] = manifest
    return result
