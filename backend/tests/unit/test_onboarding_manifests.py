"""F6 onboarding manifests (docs/onboarding/<slug>/manifest.json) stay contract-valid.

The official blank PDFs are not stored in git, so PDF-bound checks (identity hash,
field geometry against the real document) are recorded in each onboarding README and
re-run at upload time by the admin validation endpoint. These tests pin what can be
checked without the PDF: the v1 structure, the JSON Schema, identity consistency with
the directory name, and the manifest hash recorded in the README.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import jsonschema
import pytest

from app.modules.templates.manifest_contract import validate_manifest
from app.modules.templates.service import canonical_manifest_json
from tests import contract_fixtures as fx

ONBOARDING = pathlib.Path(__file__).resolve().parents[3] / "docs" / "onboarding"
MANIFESTS = sorted(ONBOARDING.glob("*/manifest.json"))


def test_onboarding_manifests_present() -> None:
    assert {p.parent.name for p in MANIFESTS} >= {
        "ka-kmv-12-conductor-licence",
        "cmv-form-1-physical-fitness",
    }


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_onboarding_manifest_is_contract_valid(path: pathlib.Path) -> None:
    manifest = json.loads(path.read_text())
    assert validate_manifest(manifest) == []
    schema = json.loads((fx.CONTRACTS / "form-manifest-v1.schema.json").read_text())
    jsonschema.validate(manifest, schema)


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_onboarding_identity_matches_directory(path: pathlib.Path) -> None:
    identity = json.loads(path.read_text())["identity"]
    assert identity["slug"] == path.parent.name
    assert identity["semver"] == "1.0.0"
    assert re.fullmatch(r"[a-f0-9]{64}", identity["blank_pdf_sha256"])


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_onboarding_readme_records_current_hashes(path: pathlib.Path) -> None:
    manifest = json.loads(path.read_text())
    readme = (path.parent / "README.md").read_text()
    digest = hashlib.sha256(canonical_manifest_json(manifest).encode()).hexdigest()
    assert digest in readme, "README must record the current manifest SHA-256"
    assert manifest["identity"]["blank_pdf_sha256"] in readme
