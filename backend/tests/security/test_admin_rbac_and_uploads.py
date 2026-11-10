"""Security tests: admin upload hardening and public-visibility enforcement."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from app.core.constants import UserRole
from tests.manifest_fixtures import contract_manifest
from tests.pdf_fixtures import make_blank_pdf, make_pdf_with_token, make_prefilled_acroform_pdf

pytestmark = pytest.mark.asyncio


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _domain_source_template(client: AsyncClient, make_principal):  # type: ignore[no-untyped-def]
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await client.post(
        "/api/v1/admin/domains",
        json={
            "domain_pattern": "gov.in",
            "domain_type": "GOV_TLD_SUFFIX",
            "allow_subdomains": True,
            "rationale": "central",
        },
        headers=auth(sa),
    )
    src = await client.post(
        "/api/v1/admin/sources",
        json={
            "source_code": "MORD",
            "jurisdiction": "CENTRAL_INDIA",
            "ministry_or_department": "MoRD",
            "official_portal_url": "https://rural.gov.in",
            "matched_domain_pattern": "gov.in",
            "source_type": "OFFICIAL_GOV_PORTAL",
            "allow_automated_checksum_check": False,
        },
        headers=auth(curator),
    )
    source_id = src.json()["id"]
    tpl = await client.post(
        "/api/v1/admin/templates",
        json={
            "government_source_id": source_id,
            "slug": "ror-7-12",
            "jurisdiction": "CENTRAL_INDIA",
            "official_form_code": "RTC-7/12",
            "category": "REVENUE",
            "localized_titles": {"en": "RoR"},
            "localized_descriptions": {"en": "RoR"},
        },
        headers=auth(curator),
    )
    return curator, source_id, tpl.json()["id"]


def _manifest_for(pdf: bytes) -> dict:
    """Contract-valid manifest when ``pdf`` is parseable; else a placeholder.

    Tests that upload deliberately malformed bytes must be rejected by the PDF
    validator, which runs before manifest validation.
    """
    try:
        return contract_manifest(pdf)
    except Exception:
        return {"schema_version": 1}


def _version_form(source_id: str, pdf: bytes) -> tuple[dict, dict]:
    files = {"blank_pdf": ("blank.pdf", pdf, "application/pdf")}
    data = {
        "semver": "1.0.0",
        "source_pdf_download_url": "https://rural.gov.in/forms/ror.pdf",
        "source_url_matched_domain": "gov.in",
        "source_downloaded_at": "2026-11-01T00:00:00+00:00",
        "government_source_id": source_id,
        "manifest_json": json.dumps(_manifest_for(pdf)),
    }
    return files, data


async def test_unauthorized_upload_rejected(client: AsyncClient, make_principal) -> None:
    _curator, source_id, template_id = await _domain_source_template(client, make_principal)
    files, data = _version_form(source_id, make_blank_pdf(1))
    # No Authorization header → the privacy middleware rejects the multipart upload.
    r = await client.post(f"/api/v1/admin/templates/{template_id}/versions", files=files, data=data)
    assert r.status_code == 401


async def test_citizen_cannot_upload(client: AsyncClient, make_principal) -> None:
    _curator, source_id, template_id = await _domain_source_template(client, make_principal)
    _, citizen = await make_principal(UserRole.CITIZEN)
    files, data = _version_form(source_id, make_blank_pdf(1))
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files=files,
        data=data,
        headers=auth(citizen),
    )
    assert r.status_code == 403


async def test_malicious_pdf_upload_rejected(client: AsyncClient, make_principal) -> None:
    curator, source_id, template_id = await _domain_source_template(client, make_principal)
    files, data = _version_form(source_id, make_pdf_with_token(b"/JavaScript"))
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files=files,
        data=data,
        headers=auth(curator),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "PDF_VALIDATION_FAILED"


async def test_prefilled_pdf_upload_rejected(client: AsyncClient, make_principal) -> None:
    curator, source_id, template_id = await _domain_source_template(client, make_principal)
    files, data = _version_form(source_id, make_prefilled_acroform_pdf())
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files=files,
        data=data,
        headers=auth(curator),
    )
    assert r.status_code == 422


async def test_bundle_hidden_for_unpublished(client: AsyncClient, make_principal) -> None:
    curator, source_id, template_id = await _domain_source_template(client, make_principal)
    files, data = _version_form(source_id, make_blank_pdf(1))
    await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files=files,
        data=data,
        headers=auth(curator),
    )
    _, citizen = await make_principal(UserRole.CITIZEN)
    # DRAFT version must not be retrievable as a bundle even by an authenticated citizen.
    r = await client.get("/api/v1/templates/ror-7-12/versions/1.0.0/bundle", headers=auth(citizen))
    assert r.status_code == 404
