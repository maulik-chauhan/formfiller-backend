"""F5 admin console: access control, quality checks, maker-checker, lifecycle, history.

All templates here are fictional TEST FIXTURES; none is a government form.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.constants import UserRole
from tests import contract_fixtures as fx
from tests.integration.test_f3_templates import FILLABLE, _publish, _roles, _template, _upload
from tests.integration.test_templates_admin import (
    _create_domain,
    _create_source,
    _transition,
    auth,
)

pytestmark = pytest.mark.asyncio

PDF = fx.pdf_bytes("fillable-test.pdf")
FLAT_PDF = fx.pdf_bytes("flat-test.pdf")
UUID0 = "00000000-0000-4000-8000-000000000000"


def manifest_for(semver: str = "1.0.0") -> dict[str, Any]:
    doc = copy.deepcopy(FILLABLE)
    doc["identity"]["semver"] = semver
    return doc


async def _setup(client: AsyncClient, make_principal) -> tuple[dict[str, str], str, str]:
    roles = await _roles(make_principal)
    await _create_domain(client, roles["sa"])
    source_id = await _create_source(client, roles["curator"])
    template_id = await _template(client, roles["curator"], source_id, FILLABLE)
    return roles, source_id, template_id


def _form(source_id: str, manifest: Any, *, semver: str = "1.0.0", **over: str) -> dict[str, str]:
    data = {
        "semver": semver,
        "source_pdf_download_url": "https://rural.gov.in/forms/test.pdf",
        "source_url_matched_domain": "gov.in",
        "source_downloaded_at": "2026-11-01T00:00:00+00:00",
        "government_source_id": source_id,
        "manifest_json": manifest if isinstance(manifest, str) else json.dumps(manifest),
    }
    data.update(over)
    return data


async def _validate(
    client: AsyncClient, token: str, template_id: str, data: dict[str, str], pdf: bytes = PDF
) -> dict[str, Any]:
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/validate",
        files={"blank_pdf": ("blank.pdf", pdf, "application/pdf")},
        data=data,
        headers=auth(token),
    )
    assert r.status_code == 200, r.text
    return r.json()


def codes(report: dict[str, Any], severity: str = "error") -> set[str]:
    return {i["code"] for i in report["issues"] if i["severity"] == severity}


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #
async def test_admin_me_reports_role_and_permissions(client: AsyncClient, make_principal) -> None:
    roles = await _roles(make_principal)
    expected = {
        "curator": ("TEMPLATE_CURATOR", True, False, False, False),
        "verifier": ("TEMPLATE_VERIFIER", False, True, False, False),
        "approver": ("TEMPLATE_APPROVER", False, False, True, False),
        "sa": ("SUPER_ADMIN", True, True, True, True),
    }
    for who, (role, create, verify, approve, domains) in expected.items():
        r = await client.get("/api/v1/admin/me", headers=auth(roles[who]))
        assert r.status_code == 200
        body = r.json()
        assert (
            body["role"],
            body["can_create"],
            body["can_verify"],
            body["can_approve"],
            body["can_manage_domains"],
        ) == (role, create, verify, approve, domains)
    assert (await client.get("/api/v1/admin/me", headers=auth(roles["citizen"]))).status_code == 403
    assert (await client.get("/api/v1/admin/me")).status_code == 401


async def test_every_admin_read_refuses_citizens_and_anonymous(
    client: AsyncClient, make_principal
) -> None:
    roles, _, template_id = await _setup(client, make_principal)
    paths = [
        "/api/v1/admin/domains",
        "/api/v1/admin/templates",
        f"/api/v1/admin/templates/{template_id}",
        f"/api/v1/admin/templates/{template_id}/versions/{UUID0}",
        f"/api/v1/admin/templates/{template_id}/versions/{UUID0}/blank-pdf",
    ]
    for path in paths:
        assert (await client.get(path)).status_code == 401, path
        assert (await client.get(path, headers=auth(roles["citizen"]))).status_code == 403, path
    meta = await client.post(
        f"/api/v1/admin/templates/{template_id}/metadata",
        json={"localized_titles": {"en": "x"}},
        headers=auth(roles["citizen"]),
    )
    assert meta.status_code == 403


async def test_only_makers_may_validate_upload_edit_or_rework(
    client: AsyncClient, make_principal
) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    for who in ("verifier", "approver", "citizen"):
        r = await client.post(
            f"/api/v1/admin/templates/{template_id}/versions/validate",
            files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
            data=_form(source_id, FILLABLE),
            headers=auth(roles[who]),
        )
        assert r.status_code == 403, who
        r = await client.post(
            f"/api/v1/admin/templates/{template_id}/metadata",
            json={"localized_titles": {"en": "x"}},
            headers=auth(roles[who]),
        )
        assert r.status_code == 403, who
    # Without a bearer the privacy middleware refuses the multipart body outright.
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/validate",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data=_form(source_id, FILLABLE),
    )
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Listing and detail
# --------------------------------------------------------------------------- #
async def test_list_shows_every_lifecycle_state_with_filters(
    client: AsyncClient, make_principal
) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    r = await client.get("/api/v1/admin/templates", headers=auth(roles["verifier"]))
    (item,) = r.json()["templates"]
    assert item["version_count"] == 0 and item["latest_version"] is None
    assert (
        await _upload(client, roles["curator"], template_id, source_id, FILLABLE, PDF)
    ).status_code == 201

    listing = (await client.get("/api/v1/admin/templates", headers=auth(roles["approver"]))).json()
    assert listing["total"] == 1
    assert listing["templates"][0]["latest_version"]["status"] == "DRAFT"
    assert listing["templates"][0]["published_semver"] is None
    # The public catalogue still shows nothing.
    assert (await client.get("/api/v1/templates")).json()["total"] == 0

    for params, expected in (
        ({"status": "DRAFT"}, 1),
        ({"status": "IN_REVIEW"}, 0),
        ({"q": "fillable"}, 1),
        ({"q": "TEST-FIXTURE-007"}, 1),
        ({"q": "no-such-form"}, 0),
        ({"category": FILLABLE["identity"]["category"]}, 1),
        ({"category": "RTI"}, 0),
    ):
        r = await client.get("/api/v1/admin/templates", params=params, headers=auth(roles["sa"]))
        assert r.json()["total"] == expected, params


async def test_template_detail_and_blank_pdf_preview(client: AsyncClient, make_principal) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    version = (
        await _upload(client, roles["curator"], template_id, source_id, FILLABLE, PDF)
    ).json()
    detail = (
        await client.get(f"/api/v1/admin/templates/{template_id}", headers=auth(roles["verifier"]))
    ).json()
    assert detail["government_source"]["source_code"] == "MORD"
    assert detail["titles_editable"] is True and detail["identity_editable"] is False
    assert [v["semver"] for v in detail["versions"]] == ["1.0.0"]

    pdf = await client.get(
        f"/api/v1/admin/templates/{template_id}/versions/{version['id']}/blank-pdf",
        headers=auth(roles["verifier"]),
    )
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.headers["cache-control"] == "no-store"
    assert hashlib.sha256(pdf.content).hexdigest() == version["blank_pdf_sha256"]
    missing = await client.get(
        f"/api/v1/admin/templates/{template_id}/versions/{UUID0}", headers=auth(roles["sa"])
    )
    assert missing.status_code == 404


# --------------------------------------------------------------------------- #
# Dry-run quality checks
# --------------------------------------------------------------------------- #
async def test_validate_reports_pdf_inspection_and_stores_nothing(
    client: AsyncClient, make_principal
) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    report = await _validate(client, roles["curator"], template_id, _form(source_id, FILLABLE))
    assert report["valid"] is True and report["issues"] == []
    assert report["pdf"]["page_count"] == 2
    assert report["pdf"]["sha256"] == hashlib.sha256(PDF).hexdigest()
    fields = {f["name"]: f for f in report["pdf"]["fields"]}
    assert fields["applicant.full_name"]["kind"] == "text"
    assert fields["applicant.contact_method"] == {
        "name": "applicant.contact_method",
        "kind": "radio",
        "options": ["post", "phone", "email"],
    }
    assert fields["details.service_type"]["options"] == ["STANDARD", "PRIORITY", "EXPRESS"]
    assert report["expected_identity"] == FILLABLE["identity"]
    detail = (
        await client.get(f"/api/v1/admin/templates/{template_id}", headers=auth(roles["sa"]))
    ).json()
    assert detail["versions"] == []


async def test_validate_gives_actionable_errors_for_bad_input(
    client: AsyncClient, make_principal
) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    curator = roles["curator"]

    async def errs(data: dict[str, str], pdf: bytes = PDF) -> set[str]:
        return codes(await _validate(client, curator, template_id, data, pdf))

    assert await errs(_form(source_id, "{not json")) == {"manifest_json_invalid"}
    assert await errs(_form(source_id, {"fields": []})) == {"schema_version_unsupported"}
    unknown = copy.deepcopy(FILLABLE)
    unknown["sections"][0]["fields"][0]["type"] = "signature_pad"
    assert await errs(_form(source_id, unknown)) == {"field_type_unsupported"}
    wrong_field = copy.deepcopy(FILLABLE)
    wrong_field["sections"][0]["fields"][0]["pdf"][0]["field_name"] = "applicant.fullname"
    assert await errs(_form(source_id, wrong_field)) == {"pdf_field_missing"}
    wrong_kind = copy.deepcopy(FILLABLE)
    wrong_kind["sections"][0]["fields"][0]["pdf"][0]["field_name"] = "details.fee_waiver"
    assert await errs(_form(source_id, wrong_kind)) == {"pdf_field_type_mismatch"}
    off_page = copy.deepcopy(fx.valid_manifests()["coordinate-mapped"])
    off_page["identity"] = {
        **FILLABLE["identity"],
        "blank_pdf_sha256": off_page["identity"]["blank_pdf_sha256"],
    }
    off_page["sections"][0]["fields"][0]["pdf"][0]["x"] = 500
    assert "pdf_rect_out_of_bounds" in await errs(_form(source_id, off_page), FLAT_PDF)
    assert await errs(_form(source_id, FILLABLE, semver="1.0")) >= {"semver_invalid"}
    # Wrong PDF for this manifest: both the identity and the field mappings are reported.
    assert await errs(_form(source_id, FILLABLE), FLAT_PDF) == {
        "identity_mismatch",
        "pdf_field_missing",
    }
    assert await errs(_form(source_id, FILLABLE), b"not a pdf") == {"pdf_invalid"}
    assert await errs(
        _form(source_id, FILLABLE, source_pdf_download_url="https://evil.example.com/f.pdf")
    ) == {"source_url_not_approved"}
    assert await errs(
        _form(source_id, FILLABLE, source_downloaded_at="2099-01-01T00:00:00+00:00")
    ) == {"source_downloaded_at_invalid"}
    assert await errs(_form(source_id, FILLABLE, source_downloaded_at="2026-11-01T00:00:00")) == {
        "source_downloaded_at_invalid"
    }

    report = await _validate(client, curator, template_id, _form(source_id, wrong_field))
    (issue,) = report["issues"]
    assert issue["path"] == "manifest.sections[0].fields[0].pdf[0].field_name"
    assert "applicant.fullname" in issue["message"]


async def test_validate_flags_duplicates_and_version_order(
    client: AsyncClient, make_principal
) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    curator = roles["curator"]
    assert (
        await _upload(client, curator, template_id, source_id, FILLABLE, PDF)
    ).status_code == 201
    report = await _validate(client, curator, template_id, _form(source_id, FILLABLE))
    assert codes(report) == {"semver_exists"}
    report = await _validate(
        client, curator, template_id, _form(source_id, manifest_for("0.9.0"), semver="0.9.0")
    )
    assert report["valid"] is True
    assert codes(report, "warning") == {"semver_not_increasing"}
    same = await _validate(
        client, curator, template_id, _form(source_id, manifest_for("1.0.1"), semver="1.0.1")
    )
    assert same["valid"] is True and codes(same, "warning") == set()


async def test_rejected_upload_returns_structured_manifest_errors(
    client: AsyncClient, make_principal
) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    bad = copy.deepcopy(FILLABLE)
    bad["sections"][0]["fields"][0]["pdf"][0]["field_name"] = "nope"
    r = await _upload(client, roles["curator"], template_id, source_id, bad, PDF)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "MANIFEST_VALIDATION_FAILED"
    assert body["errors"] == [
        {
            "field": "$.sections[0].fields[0].pdf[0].field_name",
            "code": "pdf_field_missing",
            "message": "PDF has no field 'nope'",
        }
    ]
    future = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data=_form(source_id, FILLABLE, source_downloaded_at="2099-01-01T00:00:00+00:00"),
        headers=auth(roles["curator"]),
    )
    assert future.status_code == 422
    assert "future" in future.json()["detail"]


# --------------------------------------------------------------------------- #
# Metadata editing before publication
# --------------------------------------------------------------------------- #
async def test_metadata_editing_rules(client: AsyncClient, make_principal, db_session) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    curator = roles["curator"]
    url = f"/api/v1/admin/templates/{template_id}/metadata"

    r = await client.post(
        url,
        json={"official_form_code": "TEST-FIXTURE-007B", "category": "RTI"},
        headers=auth(curator),
    )
    assert r.status_code == 200
    assert (r.json()["official_form_code"], r.json()["category"]) == ("TEST-FIXTURE-007B", "RTI")
    r = await client.post(url, json={"slug": "renamed"}, headers=auth(curator))
    assert r.status_code == 422  # the slug is permanent (unknown field)
    r = await client.post(
        url,
        json={
            "official_form_code": FILLABLE["identity"]["official_form_code"],
            "category": FILLABLE["identity"]["category"],
        },
        headers=auth(curator),
    )
    assert r.status_code == 200

    assert (
        await _upload(client, curator, template_id, source_id, FILLABLE, PDF)
    ).status_code == 201
    r = await client.post(url, json={"official_form_code": "CHANGED"}, headers=auth(curator))
    assert r.status_code == 409
    assert "before the first upload" in r.json()["detail"]
    r = await client.post(
        url,
        json={"localized_titles": {"en": "[TEST FIXTURE] Renamed before publication"}},
        headers=auth(curator),
    )
    assert r.status_code == 200
    r = await client.post(url, json={"localized_titles": {"en": "  "}}, headers=auth(curator))
    assert r.status_code == 422

    events = (
        (
            await db_session.execute(
                text(
                    "select metadata_jsonb from audit_logs where event_type = "
                    "'ADMIN_TEMPLATE_METADATA_UPDATED' order by occurred_at"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [e["changed_fields"] for e in events] == [
        "official_form_code,category",
        "official_form_code,category",
        "localized_titles",
    ]
    # Field names only — never the values that were entered.
    assert all("TEST" not in json.dumps(e) for e in events)


async def test_titles_freeze_after_publication(client: AsyncClient, make_principal) -> None:
    roles = await _roles(make_principal)
    template_id, _version = await _publish(client, roles)
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/metadata",
        json={"localized_titles": {"en": "Changed after publication"}},
        headers=auth(roles["curator"]),
    )
    assert r.status_code == 409
    detail = (
        await client.get(f"/api/v1/admin/templates/{template_id}", headers=auth(roles["sa"]))
    ).json()
    assert detail["titles_editable"] is False and detail["published_semver"] == "1.0.0"


# --------------------------------------------------------------------------- #
# Maker-checker, rejection, rework
# --------------------------------------------------------------------------- #
async def test_version_actions_explain_maker_checker_rules(
    client: AsyncClient, make_principal
) -> None:
    _, a = await make_principal(UserRole.SUPER_ADMIN)
    roles, source_id, template_id = await _setup(client, make_principal)
    version = (await _upload(client, a, template_id, source_id, FILLABLE, PDF)).json()
    vid, bundle = version["id"], version["bundle_sha256"]
    path = f"/api/v1/admin/templates/{template_id}/versions/{vid}"

    def actions(body: dict[str, Any]) -> dict[str, tuple[bool, str | None]]:
        return {x["target_status"]: (x["allowed"], x["reason"]) for x in body["actions"]}

    assert (await _transition(client, a, template_id, vid, "IN_REVIEW", bundle)).status_code == 200
    own = actions((await client.get(path, headers=auth(a))).json())
    assert own["VERIFIED"][0] is False and "someone else" in own["VERIFIED"][1]
    assert own["DRAFT"] == (True, None)
    # The backend enforces what the UI explains: the maker cannot check their own work.
    assert (await _transition(client, a, template_id, vid, "VERIFIED", bundle)).status_code == 403
    by_approver = actions((await client.get(path, headers=auth(roles["approver"]))).json())
    assert by_approver["VERIFIED"][0] is False and "role" in by_approver["VERIFIED"][1]
    assert actions((await client.get(path, headers=auth(roles["verifier"]))).json())[
        "VERIFIED"
    ] == (True, None)

    assert (
        await _transition(client, roles["verifier"], template_id, vid, "VERIFIED", bundle)
    ).status_code == 200
    as_maker = actions((await client.get(path, headers=auth(a))).json())
    assert as_maker["APPROVED"][0] is False
    assert (await _transition(client, a, template_id, vid, "APPROVED", bundle)).status_code == 403


async def test_reject_rework_and_resubmit(client: AsyncClient, make_principal) -> None:
    roles, source_id, template_id = await _setup(client, make_principal)
    curator, verifier = roles["curator"], roles["verifier"]
    _, other_curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    version = (await _upload(client, curator, template_id, source_id, FILLABLE, PDF)).json()
    vid, first_bundle = version["id"], version["bundle_sha256"]
    base = f"/api/v1/admin/templates/{template_id}/versions/{vid}"

    async def replace(token: str, manifest: dict[str, Any]) -> Any:
        return await client.post(
            f"{base}/artifacts",
            files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
            data=_form(source_id, manifest),
            headers=auth(token),
        )

    await _transition(client, curator, template_id, vid, "IN_REVIEW", first_bundle)
    assert (await replace(curator, FILLABLE)).status_code == 409  # not a draft any more
    not_draft = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/validate",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data={**_form(source_id, FILLABLE), "replace_version_id": vid},
        headers=auth(curator),
    )
    assert not_draft.status_code == 409
    rejected = await _transition(
        client, verifier, template_id, vid, "DRAFT", first_bundle, "Remarks label is unclear"
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "DRAFT"

    fixed = copy.deepcopy(FILLABLE)
    fixed["sections"][2]["fields"][3]["label"] = "Additional remarks"
    assert (await replace(other_curator, fixed)).status_code == 403  # not this version's maker
    assert (await replace(roles["verifier"], fixed)).status_code == 403
    broken = copy.deepcopy(fixed)
    broken["sections"][2]["fields"][3]["pdf"][0]["field_name"] = "nope"
    bad = await replace(curator, broken)
    assert bad.status_code == 422 and "pdf_field_missing" in bad.json()["detail"]
    # The rework dry run accepts this draft's own version number.
    dry = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/validate",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data={**_form(source_id, fixed), "replace_version_id": vid},
        headers=auth(curator),
    )
    assert dry.status_code == 200 and dry.json()["valid"] is True, dry.text
    assert codes(dry.json(), "warning") == set()
    plain = await _validate(client, curator, template_id, _form(source_id, fixed))
    assert codes(plain) == {"semver_exists"}
    moved = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/validate",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data={**_form(source_id, manifest_for("2.0.0"), semver="2.0.0"), "replace_version_id": vid},
        headers=auth(curator),
    )
    assert "semver_fixed" in codes(moved.json())
    ok = await replace(curator, fixed)
    assert ok.status_code == 200, ok.text
    new_bundle = ok.json()["bundle_sha256"]
    assert new_bundle != first_bundle and ok.json()["semver"] == "1.0.0"

    # The old digest no longer matches, so a stale reviewer screen cannot advance it.
    assert (
        await _transition(client, curator, template_id, vid, "IN_REVIEW", first_bundle)
    ).status_code == 409
    assert (
        await _transition(client, curator, template_id, vid, "IN_REVIEW", new_bundle)
    ).status_code == 200

    detail = (await client.get(base, headers=auth(roles["approver"]))).json()
    history = [
        (h["from_status"], h["to_status"], h["actor_role"]) for h in detail["approval_history"]
    ]
    assert history == [
        ("DRAFT", "DRAFT", "TEMPLATE_CURATOR"),
        ("DRAFT", "IN_REVIEW", "TEMPLATE_CURATOR"),
        ("IN_REVIEW", "DRAFT", "TEMPLATE_VERIFIER"),
        ("DRAFT", "DRAFT", "TEMPLATE_CURATOR"),
        ("DRAFT", "IN_REVIEW", "TEMPLATE_CURATOR"),
    ]
    assert detail["approval_history"][2]["review_notes"] == "Remarks label is unclear"
    assert detail["field_mapping_manifest"]["sections"][2]["fields"][3]["label"] == (
        "Additional remarks"
    )
    events = [e["event_type"] for e in detail["audit_events"]]
    assert "ADMIN_TEMPLATE_VERSION_ARTIFACTS_REPLACED" in events


# --------------------------------------------------------------------------- #
# Publication, supersession, withdrawal, immutability
# --------------------------------------------------------------------------- #
async def test_publishing_a_new_version_supersedes_the_old_one(
    client: AsyncClient, make_principal
) -> None:
    roles = await _roles(make_principal)
    template_id, v1 = await _publish(client, roles)
    source_id = (
        await client.get(f"/api/v1/admin/templates/{template_id}", headers=auth(roles["sa"]))
    ).json()["government_source_id"]
    created = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data=_form(source_id, manifest_for("1.1.0"), semver="1.1.0"),
        headers=auth(roles["curator"]),
    )
    assert created.status_code == 201, created.text
    v2 = created.json()
    for target, who in (
        ("IN_REVIEW", "curator"),
        ("VERIFIED", "verifier"),
        ("APPROVED", "approver"),
        ("PUBLISHED", "approver"),
    ):
        r = await _transition(
            client, roles[who], template_id, v2["id"], target, v2["bundle_sha256"]
        )
        assert r.status_code == 200, r.text

    slug = FILLABLE["identity"]["slug"]
    old = (await client.get(f"/api/v1/templates/{slug}/versions/1.0.0/status")).json()
    assert old["status"] == "DEPRECATED"
    assert old["deprecation_reason"] == "Superseded by version 1.1.0"
    assert (await client.get(f"/api/v1/templates/{slug}")).json()["published_semver"] == "1.1.0"
    old_detail = (
        await client.get(
            f"/api/v1/admin/templates/{template_id}/versions/{v1['id']}", headers=auth(roles["sa"])
        )
    ).json()
    assert old_detail["status"] == "DEPRECATED"
    assert old_detail["signature_verified"] is True  # artifacts and signature untouched
    assert old_detail["approval_history"][-1]["review_notes"] == (
        "Automatically superseded by version 1.1.0"
    )
    assert "ADMIN_TEMPLATE_VERSION_SUPERSEDED" in [
        e["event_type"] for e in old_detail["audit_events"]
    ]
    statuses = [
        v["status"]
        for v in (
            await client.get(f"/api/v1/admin/templates/{template_id}", headers=auth(roles["sa"]))
        ).json()["versions"]
    ]
    assert sorted(statuses) == ["DEPRECATED", "PUBLISHED"]


async def test_withdrawal_requires_a_reason_and_a_real_replacement(
    client: AsyncClient, make_principal
) -> None:
    roles = await _roles(make_principal)
    template_id, v = await _publish(client, roles)
    url = f"/api/v1/admin/templates/{template_id}/versions/{v['id']}/transitions"

    async def withdraw(**extra: str) -> Any:
        return await client.post(
            url,
            json={
                "target_status": "DEPRECATED",
                "expected_bundle_sha256": v["bundle_sha256"],
                "review_notes": "withdrawn",
                **extra,
            },
            headers=auth(roles["approver"]),
        )

    assert (await withdraw()).status_code == 422
    assert (await withdraw(deprecation_reason="  ")).status_code == 422
    assert (
        await withdraw(deprecation_reason="x", replaced_by_template_slug="ghost-form")
    ).status_code == 422
    same = await withdraw(
        deprecation_reason="x", replaced_by_template_slug=FILLABLE["identity"]["slug"]
    )
    assert same.status_code == 422 and "different template" in same.json()["detail"]
    curator_try = await client.post(
        url,
        json={
            "target_status": "DEPRECATED",
            "expected_bundle_sha256": v["bundle_sha256"],
            "review_notes": "withdrawn",
            "deprecation_reason": "Withdrawn by the issuing department",
        },
        headers=auth(roles["curator"]),
    )
    assert curator_try.status_code == 403
    ok = await withdraw(deprecation_reason="Withdrawn by the issuing department")
    assert ok.status_code == 200 and ok.json()["status"] == "DEPRECATED"
    # A withdrawn version can never come back.
    again = await _transition(
        client, roles["approver"], template_id, v["id"], "PUBLISHED", v["bundle_sha256"]
    )
    assert again.status_code == 409


async def test_published_versions_stay_immutable(
    client: AsyncClient, make_principal, db_session
) -> None:
    roles = await _roles(make_principal)
    template_id, v = await _publish(client, roles)
    source_id = (
        await client.get(f"/api/v1/admin/templates/{template_id}", headers=auth(roles["sa"]))
    ).json()["government_source_id"]
    r = await client.post(
        f"/api/v1/admin/templates/{template_id}/versions/{v['id']}/artifacts",
        files={"blank_pdf": ("blank.pdf", PDF, "application/pdf")},
        data=_form(source_id, FILLABLE),
        headers=auth(roles["curator"]),
    )
    assert r.status_code == 409
    for statement in (
        "UPDATE template_versions SET blank_pdf_sha256 = repeat('0', 64) WHERE id = :id",
        "UPDATE template_versions SET field_mapping_manifest = '{}'::jsonb WHERE id = :id",
        "DELETE FROM template_versions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            await db_session.execute(text(statement), {"id": v["id"]})
        await db_session.rollback()


async def test_audit_and_approval_history_are_append_only(
    client: AsyncClient, make_principal, db_session
) -> None:
    roles = await _roles(make_principal)
    template_id, v = await _publish(client, roles)
    detail = (
        await client.get(
            f"/api/v1/admin/templates/{template_id}/versions/{v['id']}", headers=auth(roles["sa"])
        )
    ).json()
    assert [h["to_status"] for h in detail["approval_history"]] == [
        "DRAFT",
        "IN_REVIEW",
        "VERIFIED",
        "APPROVED",
        "PUBLISHED",
    ]
    assert {h["actor_role"] for h in detail["approval_history"]} == {
        "TEMPLATE_CURATOR",
        "TEMPLATE_VERIFIER",
        "TEMPLATE_APPROVER",
    }
    assert detail["audit_events"][0]["event_type"] == "ADMIN_TEMPLATE_VERSION_CREATED"
    assert detail["signature_verified"] is True
    for statement in (
        "UPDATE template_approval_logs SET review_notes = 'rewritten'",
        "DELETE FROM template_approval_logs",
        "UPDATE audit_logs SET event_type = 'X'",
        "DELETE FROM audit_logs",
    ):
        with pytest.raises(DBAPIError):
            await db_session.execute(text(statement))
        await db_session.rollback()


async def test_domain_listing(client: AsyncClient, make_principal) -> None:
    roles, _, _ = await _setup(client, make_principal)
    body = (await client.get("/api/v1/admin/domains", headers=auth(roles["curator"]))).json()
    assert [d["domain_pattern"] for d in body["domains"]] == ["gov.in"]
