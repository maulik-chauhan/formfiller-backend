"""Form-manifest contract v1: validator behaviour pinned to the shared fixtures."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import pathlib

import jsonschema
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.signing import template_bundle_digest_v1, template_bundle_digest_v2
from app.modules.templates.manifest_contract import (
    ManifestValidationError,
    PdfFieldInfo,
    PdfFormInfo,
    PdfPageInfo,
    TemplateIdentity,
    assert_valid_manifest,
    validate_against_pdf,
    validate_identity,
    validate_manifest,
)
from app.modules.templates.pdf_validator import validate_blank_pdf
from app.modules.templates.service import canonical_manifest_json
from tests import contract_fixtures as fx

VALID = fx.valid_manifests()
INVALID = fx.invalid_cases()
#: Invalid fixtures the JSON Schema alone must also reject (purely structural).
SCHEMA_REJECTS = {
    "arbitrary-regex",
    "float-coordinate",
    "missing-schema-version",
    "rule-unknown-type",
    "unknown-field-type",
    "unknown-mapping-type",
    "unknown-property",
    "unmapped-field",
    "unsupported-schema-version",
}


def _schema() -> jsonschema.Draft202012Validator:
    schema = json.loads((fx.CONTRACTS / "form-manifest-v1.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _identity(manifest: dict) -> TemplateIdentity:
    return TemplateIdentity(**manifest["identity"])


def _pdf_for(manifest: dict):
    sha = manifest["identity"]["blank_pdf_sha256"]
    for name in ("fillable-test.pdf", "flat-test.pdf"):
        data = fx.pdf_bytes(name)
        if hashlib.sha256(data).hexdigest() == sha:
            return validate_blank_pdf(data)
    raise AssertionError("fixture references an unknown PDF")


def test_required_fixture_set_exists() -> None:
    assert set(VALID) == {
        "simple-text",
        "multi-section",
        "required-optional",
        "dropdown-checkbox",
        "date-number",
        "conditional",
        "fillable-acroform",
        "coordinate-mapped",
    }
    assert len(INVALID) >= 15


@pytest.mark.parametrize("name", sorted(VALID))
def test_valid_fixture_passes_validator_schema_and_pdf_binding(name: str) -> None:
    manifest = VALID[name]
    assert validate_manifest(manifest) == []
    assert list(_schema().iter_errors(manifest)) == []
    pdf = _pdf_for(manifest)
    assert_valid_manifest(manifest, expected=_identity(manifest), pdf=pdf.form)


@pytest.mark.parametrize("name", sorted(INVALID))
def test_invalid_fixture_reports_expected_code(name: str) -> None:
    case = INVALID[name]
    codes = {issue.code for issue in validate_manifest(case["manifest"])}
    assert case["expect_code"] in codes, (name, codes)
    assert bool(list(_schema().iter_errors(case["manifest"]))) == (name in SCHEMA_REJECTS)


def test_fixture_index_matches_files_and_generator_output() -> None:
    index = fx.load("index.json")["files"]
    on_disk = {
        str(p.relative_to(fx.FIXTURES)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in fx.FIXTURES.rglob("*")
        if p.is_file() and p.name not in {"index.json", "README.md"}
    }
    assert on_disk == index
    from scripts.build_contract_fixtures import build

    for path, data in build().items():
        assert (fx.FIXTURES / path).read_bytes() == data, f"{path} is stale; rebuild fixtures"


def test_frontend_copy_is_byte_identical_when_present() -> None:
    copy_root = (
        pathlib.Path(__file__).resolve().parents[3]
        / "frontend"
        / "screenshot-to-sight-3914"
        / "contracts"
    )
    if not copy_root.exists():
        pytest.skip("frontend repository is not checked out next to the backend")
    for relative in ("fixtures/index.json", "form-manifest-v1.schema.json", "form-manifest-v1.md"):
        assert (copy_root / relative).read_bytes() == (fx.CONTRACTS / relative).read_bytes()


def test_canonical_json_vectors() -> None:
    for vector in fx.load("vectors/canonical-json.json"):
        canonical = canonical_manifest_json(vector["value"])
        assert canonical == vector["canonical"], vector["name"]
        assert canonical.isascii()
        assert hashlib.sha256(canonical.encode()).hexdigest() == vector["sha256"]


def test_manifest_digest_vectors() -> None:
    digests = fx.load("vectors/manifest-digests.json")
    assert set(digests) == set(VALID)
    for name, manifest in VALID.items():
        assert (
            hashlib.sha256(canonical_manifest_json(manifest).encode()).hexdigest() == digests[name]
        )


def test_signature_vectors_verify_with_test_key() -> None:
    vectors = fx.load("vectors/signature.json")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(vectors["public_key_b64"]))
    for name in ("v2", "v2_multibyte_and_delimiter"):
        f = vectors[name]["fields"]
        digest = template_bundle_digest_v2(
            template_id=f["template_id"],
            slug=f["slug"],
            jurisdiction=f["jurisdiction"],
            category=f["category"],
            official_form_code=f["official_form_code"],
            semver=f["semver"],
            published_version_id=f["template_version_id"],
            blank_pdf_sha256=f["blank_pdf_sha256"],
            manifest_sha256=f["manifest_sha256"],
            page_count=f["page_count"],
        )
        assert digest == vectors[name]["digest"]
        key.verify(base64.b64decode(vectors[name]["signature"]), digest.encode("ascii"))
    f = vectors["v1"]["fields"]
    digest = template_bundle_digest_v1(
        template_id=f["template_id"],
        semver=f["semver"],
        blank_pdf_sha256=f["blank_pdf_sha256"],
        manifest_sha256=f["manifest_sha256"],
        page_count=f["page_count"],
    )
    assert digest == vectors["v1"]["digest"]
    key.verify(base64.b64decode(vectors["v1"]["signature"]), digest.encode("ascii"))
    # Documented values from F3_FINAL_BACKEND_VERIFICATION_CONTRACT.md §4d.
    assert vectors["v2"]["digest"] == (
        "cc6e150aeeac4b290077614e608558dbcbbde4e99d24e37819a2c0220bfc7a6f"
    )
    assert vectors["v1"]["digest"] == (
        "78edbc04d105c7b90948b423ee92c2bdbee99abd1558a57ffebd092006f2346d"
    )


def test_identity_must_match_template_version() -> None:
    manifest = VALID["simple-text"]
    good = _identity(manifest)
    assert validate_identity(manifest, good) == []
    for field, value in (
        ("slug", "other-template"),
        ("semver", "9.9.9"),
        ("category", "RTI"),
        ("page_count", 3),
        ("blank_pdf_sha256", "0" * 64),
    ):
        wrong = TemplateIdentity(**{**manifest["identity"], field: value})
        assert [i.code for i in validate_identity(manifest, wrong)] == ["identity_mismatch"]


def _fillable_info() -> PdfFormInfo:
    return validate_blank_pdf(fx.pdf_bytes("fillable-test.pdf")).form


def test_pdf_introspection_reads_real_field_kinds() -> None:
    fields = _fillable_info().fields
    assert fields["applicant.full_name"].kind == "text"
    assert fields["applicant.contact_method"].kind == "radio"
    assert {"post", "phone", "email"} <= set(fields["applicant.contact_method"].states)
    assert fields["details.service_type"] == PdfFieldInfo(
        kind="dropdown", states=("STANDARD", "PRIORITY", "EXPRESS")
    )
    assert fields["details.fee_waiver"].kind == "checkbox"


def test_pdf_binding_rejects_mappings_that_do_not_exist() -> None:
    info = _fillable_info()
    base = VALID["fillable-acroform"]

    def codes(mutate) -> set[str]:
        doc = copy.deepcopy(base)
        mutate(doc)
        assert validate_manifest(doc) == []
        return {i.code for i in validate_against_pdf(doc, info)}

    assert codes(lambda d: None) == set()
    assert codes(lambda d: d["sections"][0]["fields"][0]["pdf"][0].update(field_name="nope")) == {
        "pdf_field_missing"
    }
    assert codes(
        lambda d: d["sections"][0]["fields"][0]["pdf"][0].update(field_name="details.fee_waiver")
    ) == {"pdf_field_type_mismatch"}
    assert codes(
        lambda d: d["sections"][2]["fields"][0]["pdf"][0]["options"].update(express="TELEPORT")
    ) == {"pdf_option_missing"}
    assert codes(
        lambda d: d["sections"][0]["fields"][3]["pdf"][0].update(field_name="applicant.full_name")
    ) == {"pdf_field_reused"}


def test_coordinate_mappings_must_fit_an_unrotated_page() -> None:
    manifest = VALID["simple-text"]
    page = PdfPageInfo(width=595, height=842, rotation=0)
    assert validate_against_pdf(manifest, PdfFormInfo(pages=(page, page))) == []
    small = PdfPageInfo(width=200, height=200, rotation=0)
    assert {i.code for i in validate_against_pdf(manifest, PdfFormInfo(pages=(small, small)))} == {
        "pdf_rect_out_of_bounds"
    }
    rotated = PdfPageInfo(width=595, height=842, rotation=90)
    assert {
        i.code for i in validate_against_pdf(manifest, PdfFormInfo(pages=(rotated, rotated)))
    } == {"pdf_page_rotated"}


def test_assert_valid_manifest_raises_problem_with_codes() -> None:
    with pytest.raises(ManifestValidationError) as excinfo:
        assert_valid_manifest(
            {"fields": []},
            expected=_identity(VALID["simple-text"]),
            pdf=PdfFormInfo(pages=()),
        )
    assert excinfo.value.status_code == 422
    assert excinfo.value.code == "MANIFEST_VALIDATION_FAILED"
    assert "schema_version_unsupported" in (excinfo.value.detail or "")


def test_non_object_and_hostile_inputs_are_rejected_not_crashed() -> None:
    for hostile in (None, [], "x", 1, {"schema_version": True}, {"schema_version": 1.0}):
        assert validate_manifest(hostile)
    deep = {"schema_version": 1, "kind": "govforms.form-manifest", "identity": [], "sections": {}}
    assert {i.code for i in validate_manifest(deep)} == {"structure_invalid"}
