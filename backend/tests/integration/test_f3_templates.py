"""F3/F4 template integration: discovery keys, v2 signatures, lifecycle, contract gate."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import uuid
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from httpx import AsyncClient

from app.core import signing
from app.core.constants import UserRole
from tests import contract_fixtures as fx
from tests.integration.test_templates_admin import (
    _create_domain,
    _create_source,
    _transition,
    auth,
)

pytestmark = pytest.mark.asyncio

FILLABLE = fx.valid_manifests()["fillable-acroform"]


async def _roles(make_principal) -> dict[str, str]:
    return {
        "sa": (await make_principal(UserRole.SUPER_ADMIN))[1],
        "curator": (await make_principal(UserRole.TEMPLATE_CURATOR))[1],
        "verifier": (await make_principal(UserRole.TEMPLATE_VERIFIER))[1],
        "approver": (await make_principal(UserRole.TEMPLATE_APPROVER))[1],
        "citizen": (await make_principal(UserRole.CITIZEN))[1],
    }


async def _template(client: AsyncClient, token: str, source_id: str, manifest: dict) -> str:
    ident = manifest["identity"]
    r = await client.post(
        "/api/v1/admin/templates",
        json={
            "government_source_id": source_id,
            "slug": ident["slug"],
            "jurisdiction": ident["jurisdiction"],
            "official_form_code": ident["official_form_code"],
            "category": ident["category"],
            "localized_titles": {"en": "[TEST FIXTURE] Fillable form"},
            "localized_descriptions": {"en": "Fictional test template."},
        },
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(
    client: AsyncClient, token: str, template_id: str, source_id: str, manifest: Any, pdf: bytes
):
    return await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files={"blank_pdf": ("blank.pdf", pdf, "application/pdf")},
        data={
            "semver": "1.0.0",
            "source_pdf_download_url": "https://rural.gov.in/forms/test.pdf",
            "source_url_matched_domain": "gov.in",
            "source_downloaded_at": "2026-11-01T00:00:00+00:00",
            "government_source_id": source_id,
            "manifest_json": manifest if isinstance(manifest, str) else json.dumps(manifest),
        },
        headers=auth(token),
    )


async def _publish(client: AsyncClient, roles: dict[str, str], manifest: dict = FILLABLE):
    await _create_domain(client, roles["sa"])
    source_id = await _create_source(client, roles["curator"])
    template_id = await _template(client, roles["curator"], source_id, manifest)
    pdf = fx.pdf_bytes("fillable-test.pdf")
    created = await _upload(client, roles["curator"], template_id, source_id, manifest, pdf)
    assert created.status_code == 201, created.text
    version = created.json()
    for target, who in (
        ("IN_REVIEW", "curator"),
        ("VERIFIED", "verifier"),
        ("APPROVED", "approver"),
        ("PUBLISHED", "approver"),
    ):
        r = await _transition(
            client, roles[who], template_id, version["id"], target, version["bundle_sha256"]
        )
        assert r.status_code == 200, r.text
        version = r.json()
    return template_id, version


async def test_publisher_keys_are_public_typed_and_not_elt_keys(client: AsyncClient) -> None:
    r = await client.get("/api/v1/templates/publisher-keys")
    assert r.status_code == 200
    body = r.json()
    assert "trust anchor" in body["trust_note"]
    (key,) = body["keys"]
    assert key == {
        "kid": "ed25519-template-2026-v1",
        "alg": "EdDSA",
        "crv": "Ed25519",
        "public_key_b64": signing.public_key_b64(),
        "status": "active",
        "purpose": "template-publisher",
        "current_signature_format_version": 2,
    }
    elt = (await client.get("/api/v1/entitlements/public-keys")).json()["keys"]
    assert key["public_key_b64"] not in {k["public_key_b64"] for k in elt}
    assert key["kid"] not in {k["kid"] for k in elt}


async def test_bundle_is_independently_verifiable_from_the_response_alone(
    client: AsyncClient, make_principal
) -> None:
    roles = await _roles(make_principal)
    template_id, version = await _publish(client, roles)
    slug = FILLABLE["identity"]["slug"]

    detail = (await client.get(f"/api/v1/templates/{slug}")).json()
    assert detail["template_id"] == template_id
    assert detail["published_version_id"] == version["id"]

    assert (await client.get(f"/api/v1/templates/{slug}/versions/1.0.0/bundle")).status_code == 401
    r = await client.get(
        f"/api/v1/templates/{slug}/versions/1.0.0/bundle", headers=auth(roles["citizen"])
    )
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["signature_format_version"] == 2 and b["signature_alg"] == "EdDSA"
    assert b["signature_verified"] is True
    assert b["blank_pdf_download_url_expires"] is False
    assert b["blank_pdf_download_url"].endswith(f"/blank_pdfs/{b['blank_pdf_sha256']}.pdf")
    assert b["field_mapping_manifest"] == FILLABLE
    assert b["template_id"] == detail["template_id"]

    # A client's view: recompute everything from the response, trust nothing else.
    canonical = json.dumps(
        b["field_mapping_manifest"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )
    assert hashlib.sha256(canonical.encode()).hexdigest() == b["manifest_sha256"]

    def net(s: str) -> str:
        return f"{len(s.encode())}:{s}"

    fields = [
        b["template_id"],
        b["slug"],
        b["jurisdiction"],
        b["category"],
        b["official_form_code"],
        b["semver"],
        b["template_version_id"],
        b["blank_pdf_sha256"],
        b["manifest_sha256"],
        str(b["page_count"]),
    ]
    preimage = "govforms-template-bundle-v2|" + "|".join(net(f) for f in fields)
    digest = hashlib.sha256(preimage.encode()).hexdigest()
    assert digest == b["bundle_sha256"]
    keys = (await client.get("/api/v1/templates/publisher-keys")).json()["keys"]
    pub = next(k for k in keys if k["kid"] == b["signing_key_id"])
    Ed25519PublicKey.from_public_bytes(base64.b64decode(pub["public_key_b64"])).verify(
        base64.b64decode(b["publisher_ed25519_sig"]), digest.encode("ascii")
    )
    assert hashlib.sha256(fx.pdf_bytes("fillable-test.pdf")).hexdigest() == b["blank_pdf_sha256"]


async def test_status_lifecycle_and_withdrawal_provenance(
    client: AsyncClient, make_principal
) -> None:
    roles = await _roles(make_principal)
    slug = FILLABLE["identity"]["slug"]

    async def status(s: str = slug, v: str = "1.0.0") -> dict:
        r = await client.get(f"/api/v1/templates/{s}/versions/{v}/status")
        assert r.status_code == 200
        return r.json()

    assert (await status())["status"] == "UNKNOWN_TEMPLATE"

    await _create_domain(client, roles["sa"])
    source_id = await _create_source(client, roles["curator"])
    template_id = await _template(client, roles["curator"], source_id, FILLABLE)
    assert (await status())["status"] == "UNKNOWN_VERSION"

    created = await _upload(
        client,
        roles["curator"],
        template_id,
        source_id,
        FILLABLE,
        fx.pdf_bytes("fillable-test.pdf"),
    )
    version = created.json()
    never = await status()
    assert never["status"] == "NEVER_PUBLISHED"
    assert never["published_version_id"] is None  # no unpublished artifact leaks

    bundle = version["bundle_sha256"]
    for target, who in (
        ("IN_REVIEW", "curator"),
        ("VERIFIED", "verifier"),
        ("APPROVED", "approver"),
    ):
        assert (
            await _transition(client, roles[who], template_id, version["id"], target, bundle)
        ).status_code == 200

    # Withdrawal fields are refused on any transition other than DEPRECATED.
    premature = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/{version['id']}/transitions",
        json={
            "target_status": "PUBLISHED",
            "expected_bundle_sha256": bundle,
            "review_notes": "x",
            "deprecation_reason": "too early",
        },
        headers=auth(roles["approver"]),
    )
    assert premature.status_code == 422
    assert (
        await _transition(
            client, roles["approver"], template_id, version["id"], "PUBLISHED", bundle
        )
    ).status_code == 200
    published = await status()
    assert published["status"] == "PUBLISHED"
    assert published["published_version_id"] == version["id"]
    assert (await status(v="2.0.0"))["status"] == "UNKNOWN_VERSION"

    # F5: a named replacement must be a real template, so register it first.
    replacement = await client.post(
        "/api/v1/admin/templates",
        json={
            "government_source_id": source_id,
            "slug": "test-newer-form",
            "jurisdiction": FILLABLE["identity"]["jurisdiction"],
            "official_form_code": "TEST-NEWER",
            "category": FILLABLE["identity"]["category"],
            "localized_titles": {"en": "[TEST FIXTURE] Newer form"},
            "localized_descriptions": {"en": "Fictional test template."},
        },
        headers=auth(roles["curator"]),
    )
    assert replacement.status_code == 201, replacement.text

    withdrawn = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/{version['id']}/transitions",
        json={
            "target_status": "DEPRECATED",
            "expected_bundle_sha256": bundle,
            "review_notes": "superseded",
            "deprecation_reason": "Replaced by a newer official revision",
            "replaced_by_template_slug": "test-newer-form",
        },
        headers=auth(roles["approver"]),
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["deprecation_reason"] == "Replaced by a newer official revision"
    deprecated = await status()
    assert deprecated["status"] == "DEPRECATED"
    assert deprecated["deprecation_reason"] == "Replaced by a newer official revision"
    assert deprecated["replaced_by_template_slug"] == "test-newer-form"
    assert deprecated["deprecated_at"] is not None

    # Withdrawn versions drop out of the catalogue/detail/bundle but stay explainable.
    assert (await client.get(f"/api/v1/templates/{slug}")).status_code == 404
    gone = await client.get(
        f"/api/v1/templates/{slug}/versions/1.0.0/bundle", headers=auth(roles["citizen"])
    )
    assert gone.status_code == 404
    listing = (await client.get("/api/v1/templates")).json()
    assert listing["templates"] == []


async def test_published_signature_format_and_provenance_are_immutable(
    client: AsyncClient, make_principal, db_session
) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    roles = await _roles(make_principal)
    _, version = await _publish(client, roles)
    for statement in (
        "UPDATE template_versions SET signature_format_version = 1 WHERE id = :id",
        "UPDATE template_versions SET deprecation_reason = 'sneaky' WHERE id = :id",
        "UPDATE template_versions SET field_mapping_manifest = '{}'::jsonb WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            await db_session.execute(text(statement), {"id": version["id"]})
        await db_session.rollback()


async def test_legacy_v1_row_still_verifies_and_reports_format_1(
    client: AsyncClient, make_principal, db_session
) -> None:
    """A pre-0003 publication keeps verifying through the legacy digest path."""
    from app.modules.templates.models import (
        ApprovedSourceDomain,
        FormTemplate,
        GovernmentSource,
        TemplateVersion,
    )

    roles = await _roles(make_principal)
    admins = [(await make_principal(UserRole.SUPER_ADMIN))[0] for _ in range(3)]
    now = dt.datetime.now(tz=dt.UTC)
    db_session.add(
        ApprovedSourceDomain(
            domain_pattern="gov.in",
            domain_type="GOV_TLD_SUFFIX",
            allow_subdomains=True,
            rationale="t",
            approved_by_admin_id=admins[0],
        )
    )
    await db_session.flush()
    source_id, template_id, version_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(
        GovernmentSource(
            id=source_id,
            source_code="LEGACY",
            jurisdiction="CENTRAL_INDIA",
            ministry_or_department="Legacy",
            official_portal_url="https://rural.gov.in",
            matched_domain_pattern="gov.in",
            source_type="OFFICIAL_GOV_PORTAL",
        )
    )
    await db_session.flush()
    db_session.add(
        FormTemplate(
            id=template_id,
            government_source_id=source_id,
            slug="legacy-form",
            jurisdiction="CENTRAL_INDIA",
            official_form_code="LEG-1",
            category="REVENUE",
            localized_titles={"en": "Legacy"},
            localized_descriptions={"en": "Legacy"},
        )
    )
    await db_session.flush()
    manifest = {"fields": []}  # pre-contract shape
    manifest_sha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    digest = signing.template_bundle_digest_v1(
        template_id=str(template_id),
        semver="1.0.0",
        blank_pdf_sha256="d" * 64,
        manifest_sha256=manifest_sha,
        page_count=1,
    )
    signature, kid = signing.sign_publisher_digest(digest)
    db_session.add(
        TemplateVersion(
            id=version_id,
            template_id=template_id,
            semver="1.0.0",
            status="PUBLISHED",
            source_pdf_download_url="https://rural.gov.in/forms/legacy.pdf",
            source_url_matched_domain="gov.in",
            source_downloaded_at=now,
            blank_pdf_object_key=f"blank_pdfs/{'d' * 64}.pdf",
            blank_pdf_sha256="d" * 64,
            blank_pdf_size_bytes=100,
            page_count=1,
            field_mapping_manifest=manifest,
            manifest_sha256=manifest_sha,
            bundle_sha256=digest,
            publisher_ed25519_sig=signature,
            signing_key_id=kid,
            created_by_admin_id=admins[0],
            verified_by_admin_id=admins[1],
            approved_by_admin_id=admins[2],
            published_at=now,
        )
    )
    await db_session.commit()
    r = await client.get(
        "/api/v1/templates/legacy-form/versions/1.0.0/bundle", headers=auth(roles["citizen"])
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["signature_format_version"] == 1
    assert body["signature_verified"] is True


async def test_upload_rejects_manifests_outside_the_contract(
    client: AsyncClient, make_principal
) -> None:
    roles = await _roles(make_principal)
    await _create_domain(client, roles["sa"])
    source_id = await _create_source(client, roles["curator"])
    template_id = await _template(client, roles["curator"], source_id, FILLABLE)
    pdf = fx.pdf_bytes("fillable-test.pdf")

    async def rejected(manifest: Any, code: str) -> None:
        r = await _upload(client, roles["curator"], template_id, source_id, manifest, pdf)
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "MANIFEST_VALIDATION_FAILED"
        assert code in r.json()["detail"], r.json()["detail"]

    await rejected({"fields": []}, "schema_version_unsupported")
    for name, case in fx.invalid_cases().items():
        if name in {"unsupported-schema-version", "unknown-field-type", "float-coordinate"}:
            await rejected(case["manifest"], case["expect_code"])

    wrong_slug = copy.deepcopy(FILLABLE)
    wrong_slug["identity"]["slug"] = "another-form"
    await rejected(wrong_slug, "identity_mismatch")

    wrong_pdf = copy.deepcopy(FILLABLE)
    wrong_pdf["identity"]["blank_pdf_sha256"] = "0" * 64
    await rejected(wrong_pdf, "identity_mismatch")

    ghost = copy.deepcopy(FILLABLE)
    ghost["sections"][0]["fields"][0]["pdf"][0]["field_name"] = "does.not.exist"
    await rejected(ghost, "pdf_field_missing")

    # Floats written as text in the multipart field are still floats.
    as_text = json.dumps(FILLABLE).replace('"page_count": 2', '"page_count": 2.0')
    await rejected(as_text, "number_not_integer")

    # Nothing was stored for any rejected upload.
    status = await client.get(
        f"/api/v1/templates/{FILLABLE['identity']['slug']}/versions/1.0.0/status"
    )
    assert status.json()["status"] == "UNKNOWN_VERSION"


async def test_openapi_declares_bearer_on_exactly_the_protected_routes(
    client: AsyncClient,
) -> None:
    spec = (await client.get("/openapi.json")).json()
    assert spec["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    paths = spec["paths"]

    def secured(path: str, method: str) -> bool:
        return paths[path][method].get("security") == [{"BearerAuth": []}]

    protected = [
        ("/api/v1/templates/{slug}/versions/{semver}/bundle", "get"),
        ("/api/v1/auth/logout", "post"),
        ("/api/v1/auth/sessions", "get"),
        ("/api/v1/auth/account", "delete"),
        ("/api/v1/entitlements/wallet", "get"),
        ("/api/v1/admin/domains", "post"),
        ("/api/v1/admin/templates/{template_id}/versions", "post"),
    ]
    public = [
        ("/api/v1/templates", "get"),
        ("/api/v1/templates/{slug}", "get"),
        ("/api/v1/templates/sources", "get"),
        ("/api/v1/templates/publisher-keys", "get"),
        ("/api/v1/templates/{slug}/versions/{semver}/status", "get"),
        ("/api/v1/entitlements/public-keys", "get"),
        ("/api/v1/auth/otp/request", "post"),
        ("/api/v1/auth/otp/verify", "post"),
        ("/api/v1/auth/token/refresh", "post"),
        ("/api/v1/payments/webhooks/razorpay", "post"),
        ("/health", "get"),
    ]
    assert all(secured(p, m) for p, m in protected)
    assert not any(secured(p, m) for p, m in public)
