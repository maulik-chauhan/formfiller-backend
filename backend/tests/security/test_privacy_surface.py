"""The zero-knowledge boundary as a whole-API property.

These tests fail if anyone adds an endpoint that could carry applicant data: the
route inventory is pinned, only the admin upload accepts multipart, and no table
or response model has anywhere to put answers, drafts or completed PDFs.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

from app.db.models import CANONICAL_TABLES
from app.main import create_app
from app.middleware.privacy_boundary import PolicyProfile, classify

#: Every route the backend exposes. Adding one requires a privacy review and a
#: deliberate edit here.
EXPECTED_ROUTES = {
    ("POST", "/api/v1/auth/otp/request"),
    ("POST", "/api/v1/auth/otp/verify"),
    ("POST", "/api/v1/auth/token/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/sessions"),
    ("DELETE", "/api/v1/auth/sessions/{session_id}"),
    ("DELETE", "/api/v1/auth/account"),
    ("GET", "/api/v1/templates/publisher-keys"),
    ("GET", "/api/v1/templates/sources"),
    ("GET", "/api/v1/templates"),
    ("GET", "/api/v1/templates/{slug}"),
    ("GET", "/api/v1/templates/{slug}/versions/{semver}/status"),
    ("GET", "/api/v1/templates/{slug}/versions/{semver}/bundle"),
    ("POST", "/api/v1/admin/domains"),
    ("POST", "/api/v1/admin/sources"),
    ("POST", "/api/v1/admin/sources/{source_id}/checks"),
    ("POST", "/api/v1/admin/templates"),
    ("POST", "/api/v1/admin/templates/{template_id}/versions"),
    ("POST", "/api/v1/admin/templates/{template_id}/versions/{version_id}/transitions"),
    ("GET", "/api/v1/entitlements/public-keys"),
    ("GET", "/api/v1/entitlements/wallet"),
    ("POST", "/api/v1/entitlements/leases"),
    ("POST", "/api/v1/entitlements/leases/{lease_id}/upgrade"),
    ("POST", "/api/v1/entitlements/leases/{lease_id}/commit"),
    ("POST", "/api/v1/entitlements/leases/{lease_id}/release"),
    ("GET", "/api/v1/payments/pricing"),
    ("POST", "/api/v1/payments/orders"),
    ("POST", "/api/v1/payments/orders/{order_id}/verify"),
    ("POST", "/api/v1/payments/webhooks/razorpay"),
    ("POST", "/api/v1/esign/sessions"),
    ("GET", "/api/v1/health/live"),
    ("GET", "/api/v1/health/ready"),
    ("GET", "/health"),
}
#: Reviewed exceptions: an opaque device-local random hash, and a boolean lease
#: capability flag (it permits a local feature; it carries no image).
ALLOWED_OPAQUE = {"draft_instance_id_hash", "allow_handwritten_signature_image"}
FORBIDDEN_NAME_PARTS = (
    "answer",
    "draft",
    "applicant",
    "profile",
    "passphrase",
    "vault",
    "filled",
    "completed_pdf",
    "signature_image",
)


def _routes() -> list[tuple[str, APIRoute]]:
    return list(create_app().state.api_routes)


def test_route_inventory_is_exactly_the_reviewed_set() -> None:
    actual = {(m, path) for path, r in _routes() for m in r.methods if m not in {"HEAD", "OPTIONS"}}
    assert actual == EXPECTED_ROUTES
    # The pinned list must also be everything the OpenAPI document advertises.
    spec = create_app().openapi()["paths"]
    assert {(m.upper(), p) for p, ops in spec.items() for m in ops} == EXPECTED_ROUTES


def test_only_the_admin_template_upload_accepts_non_json_bodies() -> None:
    upload = "/api/v1/admin/templates/{template_id}/versions"
    for path, route in _routes():
        has_file_or_form = bool(route.dependant.body_params) and any(
            type(p.field_info).__name__ in {"File", "Form"} for p in route.dependant.body_params
        )
        assert has_file_or_form == (path == upload), path
        for method in route.methods:
            concrete = path.replace("{template_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
            profile = classify(concrete, method).profile
            if path == upload:
                assert profile is PolicyProfile.ADMIN_TEMPLATE_UPLOAD
            else:
                assert profile is not PolicyProfile.ADMIN_TEMPLATE_UPLOAD


@pytest.mark.parametrize(
    "method,path",
    sorted((m, p) for m, p in EXPECTED_ROUTES if m in {"POST", "DELETE"}),
)
def test_citizen_and_admin_json_routes_reject_uploads_and_embedded_binaries(
    method: str, path: str
) -> None:
    if path.endswith("/webhooks/razorpay") or path.endswith("{template_id}/versions"):
        pytest.skip("separately-profiled route")
    concrete = (
        path.replace("{session_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
        .replace("{lease_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
        .replace("{order_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
        .replace("{source_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
        .replace("{template_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
        .replace("{version_id}", "0" * 8 + "-0000-4000-8000-" + "0" * 12)
    )
    client = TestClient(create_app())
    multipart = client.request(method, concrete, files={"f": ("filled.pdf", b"%PDF-1.7 data")})
    assert multipart.status_code == 415, (path, multipart.status_code)
    octet = client.request(
        method, concrete, content=b"\x00\x01", headers={"Content-Type": "application/pdf"}
    )
    assert octet.status_code == 415
    for payload in (
        '{"x":"data:application/pdf;base64,AAAA"}',
        '{"x":"JVBERi0xLjcK"}',
        '{"x":"data:image/png;base64,iVBORw0KGgo"}',
    ):
        embedded = client.request(
            method, concrete, content=payload, headers={"Content-Type": "application/json"}
        )
        assert embedded.status_code == 422
        assert embedded.json()["code"] == "PRIVACY_BOUNDARY_VIOLATION"
    big = client.request(
        method,
        concrete,
        content=b'{"x":"' + b"a" * 5000 + b'"}',
        headers={"Content-Type": "application/json"},
    )
    assert big.status_code == 413


def test_no_table_or_column_can_hold_applicant_data() -> None:
    from app.db.base import Base

    assert len(CANONICAL_TABLES) == 19
    assert set(Base.metadata.tables) == set(CANONICAL_TABLES)
    for table in Base.metadata.tables.values():
        names = [table.name, *(c.name for c in table.columns)]
        for name in names:
            if name in ALLOWED_OPAQUE:
                continue
            assert not any(part in name for part in FORBIDDEN_NAME_PARTS), name


def test_no_request_or_response_schema_names_applicant_data() -> None:
    spec = create_app().openapi()
    for schema_name, schema in spec["components"]["schemas"].items():
        for prop in schema.get("properties", {}):
            if prop in ALLOWED_OPAQUE:
                continue
            assert not any(part in prop.lower() for part in FORBIDDEN_NAME_PARTS), (
                schema_name,
                prop,
            )


def test_committed_openapi_snapshot_matches_the_application() -> None:
    """The shared contract file must be regenerated whenever the API changes."""
    from scripts.export_openapi import TARGET, render

    assert TARGET.read_text() == render(), "run: python -m scripts.export_openapi"
