"""Integration tests for M4 template management & Maker-Checker on PostgreSQL."""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.constants import UserRole
from tests.pdf_fixtures import make_blank_pdf

pytestmark = pytest.mark.asyncio


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


DOMAIN = {
    "domain_pattern": "gov.in",
    "domain_type": "GOV_TLD_SUFFIX",
    "allow_subdomains": True,
    "rationale": "Central government TLD",
}


async def _create_domain(client: AsyncClient, token: str) -> None:
    r = await client.post("/api/v1/admin/domains", json=DOMAIN, headers=auth(token))
    assert r.status_code == 201, r.text


async def _create_source(client: AsyncClient, token: str) -> str:
    body = {
        "source_code": "MORD",
        "jurisdiction": "CENTRAL_INDIA",
        "ministry_or_department": "Ministry of Rural Development",
        "official_portal_url": "https://rural.gov.in",
        "matched_domain_pattern": "gov.in",
        "source_type": "OFFICIAL_GOV_PORTAL",
        "allow_automated_checksum_check": False,
    }
    r = await client.post("/api/v1/admin/sources", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_template(
    client: AsyncClient, token: str, source_id: str, slug: str = "ror-7-12"
) -> str:
    body = {
        "government_source_id": source_id,
        "slug": slug,
        "jurisdiction": "CENTRAL_INDIA",
        "official_form_code": "RTC-7/12",
        "category": "REVENUE",
        "localized_titles": {"en": "Record of Rights 7/12"},
        "localized_descriptions": {"en": "Land record of rights"},
    }
    r = await client.post("/api/v1/admin/templates", json=body, headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload_version(
    client: AsyncClient, token: str, template_id: str, source_id: str, semver: str = "1.0.0"
) -> dict[str, Any]:
    files = {"blank_pdf": ("blank.pdf", make_blank_pdf(2), "application/pdf")}
    data = {
        "semver": semver,
        "source_pdf_download_url": "https://rural.gov.in/forms/ror.pdf",
        "source_url_matched_domain": "gov.in",
        "source_downloaded_at": "2026-11-01T00:00:00+00:00",
        "government_source_id": source_id,
        "manifest_json": json.dumps({"fields": [{"name": "applicant", "page": 1}]}),
    }
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files=files,
        data=data,
        headers=auth(token),
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _transition(
    client: AsyncClient,
    token: str,
    template_id: str,
    vid: str,
    target: str,
    bundle: str,
    notes: str = "ok",
) -> Any:
    return await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/{vid}/transitions",
        json={"target_status": target, "expected_bundle_sha256": bundle, "review_notes": notes},
        headers=auth(token),
    )


# --------------------------------------------------------------------------- #
# Governance / RBAC
# --------------------------------------------------------------------------- #
async def test_domain_creation_requires_super_admin(client: AsyncClient, make_principal) -> None:
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    forbidden = await client.post("/api/v1/admin/domains", json=DOMAIN, headers=auth(curator))
    assert forbidden.status_code == 403

    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    ok = await client.post("/api/v1/admin/domains", json=DOMAIN, headers=auth(sa))
    assert ok.status_code == 201


async def test_admin_endpoints_reject_anonymous(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/admin/domains", json=DOMAIN)).status_code == 401
    assert (await client.post("/api/v1/admin/sources", json={})).status_code == 401


async def test_citizen_cannot_create_source(client: AsyncClient, make_principal) -> None:
    _, citizen = await make_principal(UserRole.CITIZEN)
    r = await client.post("/api/v1/admin/sources", json={}, headers=auth(citizen))
    assert r.status_code == 403


async def test_source_requires_approved_domain(client: AsyncClient, make_principal) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await _create_domain(client, sa)
    # matched pattern not registered.
    body = {
        "source_code": "BOGUS",
        "jurisdiction": "CENTRAL_INDIA",
        "ministry_or_department": "X",
        "official_portal_url": "https://x.example.com",
        "matched_domain_pattern": "example.com",
        "source_type": "OFFICIAL_GOV_PORTAL",
        "allow_automated_checksum_check": False,
    }
    r = await client.post("/api/v1/admin/sources", json=body, headers=auth(curator))
    assert r.status_code == 422


async def test_source_url_must_match_domain_trigger(client: AsyncClient, make_principal) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await _create_domain(client, sa)
    body = {
        "source_code": "EVIL",
        "jurisdiction": "CENTRAL_INDIA",
        "ministry_or_department": "Evil",
        "official_portal_url": "https://evil.com/forms",  # host not under gov.in
        "matched_domain_pattern": "gov.in",
        "source_type": "OFFICIAL_GOV_PORTAL",
        "allow_automated_checksum_check": False,
    }
    r = await client.post("/api/v1/admin/sources", json=body, headers=auth(curator))
    assert r.status_code == 422  # rejected by the DB validation trigger


# --------------------------------------------------------------------------- #
# Maker-Checker happy path + separation
# --------------------------------------------------------------------------- #
async def test_full_maker_checker_publish_flow(client: AsyncClient, make_principal) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    _, verifier = await make_principal(UserRole.TEMPLATE_VERIFIER)
    _, approver = await make_principal(UserRole.TEMPLATE_APPROVER)

    await _create_domain(client, sa)
    source_id = await _create_source(client, curator)
    template_id = await _create_template(client, curator, source_id)
    version = await _upload_version(client, curator, template_id, source_id)
    vid, bundle = version["id"], version["bundle_sha256"]
    assert version["status"] == "DRAFT"

    r1 = await _transition(client, curator, template_id, vid, "IN_REVIEW", bundle)
    assert r1.status_code == 200
    r2 = await _transition(client, verifier, template_id, vid, "VERIFIED", bundle)
    assert r2.status_code == 200
    r3 = await _transition(client, approver, template_id, vid, "APPROVED", bundle)
    assert r3.status_code == 200
    r4 = await _transition(client, approver, template_id, vid, "PUBLISHED", bundle)
    assert r4.status_code == 200
    published = r4.json()
    assert published["status"] == "PUBLISHED"
    assert published["publisher_ed25519_sig"]
    assert published["signing_key_id"] == "ed25519-template-2026-v1"


async def test_maker_checker_distinctness_rules(client: AsyncClient, make_principal) -> None:
    # Use super-admins to isolate the id-distinctness logic from role gating.
    _, a = await make_principal(UserRole.SUPER_ADMIN)
    _, b = await make_principal(UserRole.SUPER_ADMIN)
    _, c = await make_principal(UserRole.SUPER_ADMIN)

    await _create_domain(client, a)
    source_id = await _create_source(client, a)
    template_id = await _create_template(client, a, source_id)
    version = await _upload_version(client, a, template_id, source_id)  # creator = A
    vid, bundle = version["id"], version["bundle_sha256"]

    assert (await _transition(client, a, template_id, vid, "IN_REVIEW", bundle)).status_code == 200
    # Creator A cannot verify their own version.
    assert (await _transition(client, a, template_id, vid, "VERIFIED", bundle)).status_code == 403
    # Distinct verifier B succeeds.
    assert (await _transition(client, b, template_id, vid, "VERIFIED", bundle)).status_code == 200
    # Approver must differ from verifier (B) and creator (A).
    assert (await _transition(client, b, template_id, vid, "APPROVED", bundle)).status_code == 403
    assert (await _transition(client, a, template_id, vid, "APPROVED", bundle)).status_code == 403
    assert (await _transition(client, c, template_id, vid, "APPROVED", bundle)).status_code == 200


async def test_wrong_expected_bundle_rejected(client: AsyncClient, make_principal) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await _create_domain(client, sa)
    source_id = await _create_source(client, curator)
    template_id = await _create_template(client, curator, source_id)
    version = await _upload_version(client, curator, template_id, source_id)
    r = await _transition(client, curator, template_id, version["id"], "IN_REVIEW", "f" * 64)
    assert r.status_code == 409


async def test_illegal_transition_rejected(client: AsyncClient, make_principal) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await _create_domain(client, sa)
    source_id = await _create_source(client, curator)
    template_id = await _create_template(client, curator, source_id)
    version = await _upload_version(client, curator, template_id, source_id)
    # DRAFT -> VERIFIED is not permitted.
    r = await _transition(
        client, curator, template_id, version["id"], "VERIFIED", version["bundle_sha256"]
    )
    assert r.status_code == 409


async def test_published_version_is_immutable(client: AsyncClient, make_principal) -> None:
    _, a = await make_principal(UserRole.SUPER_ADMIN)
    _, b = await make_principal(UserRole.SUPER_ADMIN)
    _, c = await make_principal(UserRole.SUPER_ADMIN)
    await _create_domain(client, a)
    source_id = await _create_source(client, a)
    template_id = await _create_template(client, a, source_id)
    version = await _upload_version(client, a, template_id, source_id)
    vid, bundle = version["id"], version["bundle_sha256"]
    await _transition(client, a, template_id, vid, "IN_REVIEW", bundle)
    await _transition(client, b, template_id, vid, "VERIFIED", bundle)
    await _transition(client, c, template_id, vid, "APPROVED", bundle)
    await _transition(client, c, template_id, vid, "PUBLISHED", bundle)
    # Any transition off PUBLISHED except DEPRECATED is illegal.
    back = await _transition(client, c, template_id, vid, "VERIFIED", bundle)
    assert back.status_code == 409


async def test_duplicate_semver_rejected(client: AsyncClient, make_principal) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await _create_domain(client, sa)
    source_id = await _create_source(client, curator)
    template_id = await _create_template(client, curator, source_id)
    await _upload_version(client, curator, template_id, source_id, semver="1.0.0")
    files = {"blank_pdf": ("blank.pdf", make_blank_pdf(1), "application/pdf")}
    data = {
        "semver": "1.0.0",
        "source_pdf_download_url": "https://rural.gov.in/forms/ror.pdf",
        "source_url_matched_domain": "gov.in",
        "source_downloaded_at": "2026-11-01T00:00:00+00:00",
        "government_source_id": source_id,
        "manifest_json": json.dumps({"fields": []}),
    }
    dup = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files=files,
        data=data,
        headers=auth(curator),
    )
    assert dup.status_code == 409


# --------------------------------------------------------------------------- #
# Public visibility
# --------------------------------------------------------------------------- #
async def test_public_sees_only_published(client: AsyncClient, make_principal) -> None:
    _, a = await make_principal(UserRole.SUPER_ADMIN)
    _, b = await make_principal(UserRole.SUPER_ADMIN)
    _, c = await make_principal(UserRole.SUPER_ADMIN)
    _, citizen = await make_principal(UserRole.CITIZEN)

    await _create_domain(client, a)
    source_id = await _create_source(client, a)
    template_id = await _create_template(client, a, source_id)
    version = await _upload_version(client, a, template_id, source_id)
    vid, bundle = version["id"], version["bundle_sha256"]

    # Draft/unpublished is invisible to the public.
    assert (await client.get("/api/v1/templates")).json()["total"] == 0
    assert (await client.get("/api/v1/templates/ror-7-12")).status_code == 404

    await _transition(client, a, template_id, vid, "IN_REVIEW", bundle)
    await _transition(client, b, template_id, vid, "VERIFIED", bundle)
    await _transition(client, c, template_id, vid, "APPROVED", bundle)
    # APPROVED-but-unpublished is still invisible.
    assert (await client.get("/api/v1/templates")).json()["total"] == 0

    await _transition(client, c, template_id, vid, "PUBLISHED", bundle)
    listing = await client.get("/api/v1/templates")
    assert listing.json()["total"] == 1
    detail = await client.get("/api/v1/templates/ror-7-12")
    assert detail.status_code == 200
    assert detail.json()["published_semver"] == "1.0.0"

    # Bundle requires authentication; signature is verified.
    assert (await client.get("/api/v1/templates/ror-7-12/versions/1.0.0/bundle")).status_code == 401
    bundle_resp = await client.get(
        "/api/v1/templates/ror-7-12/versions/1.0.0/bundle", headers=auth(citizen)
    )
    assert bundle_resp.status_code == 200
    assert bundle_resp.json()["signature_verified"] is True


async def test_public_source_listing(client: AsyncClient, make_principal) -> None:
    _, a = await make_principal(UserRole.SUPER_ADMIN)
    await _create_domain(client, a)
    await _create_source(client, a)
    r = await client.get("/api/v1/templates/sources?jurisdiction=CENTRAL_INDIA")
    assert r.status_code == 200
    assert r.json()["total"] == 1
