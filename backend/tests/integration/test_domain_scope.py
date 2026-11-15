"""F6 hardening: exact-host source domains, strict URL host parsing, domain scope edits."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.constants import UserRole

pytestmark = pytest.mark.asyncio

KA = "transport.karnataka.gov.in"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _domain(pattern: str, kind: str, subdomains: bool) -> dict[str, object]:
    return {
        "domain_pattern": pattern,
        "domain_type": kind,
        "allow_subdomains": subdomains,
        "rationale": "Official department website (test)",
    }


def _source(code: str, url: str, pattern: str) -> dict[str, object]:
    return {
        "source_code": code,
        "jurisdiction": "KARNATAKA_STATE",
        "ministry_or_department": "Transport Department (test)",
        "official_portal_url": url,
        "matched_domain_pattern": pattern,
        "source_type": "OFFICIAL_GOV_PORTAL",
        "allow_automated_checksum_check": False,
    }


async def _matches(db_session, url: str, pattern: str) -> bool:  # type: ignore[no-untyped-def]
    return bool(
        await db_session.scalar(
            text("SELECT fn_validate_url_against_approved_domains(:u, :p)"),
            {"u": url, "p": pattern},
        )
    )


async def _scope(client: AsyncClient, token: str, pattern: str, body: dict[str, object]):  # type: ignore[no-untyped-def]
    return await client.post(
        f"/api/v1/admin/domains/{pattern}/scope", json=body, headers=auth(token)
    )


async def test_exact_host_matching(client: AsyncClient, make_principal, db_session) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    r = await client.post(
        "/api/v1/admin/domains",
        json=_domain(KA, "STATUTORY_EXACT_HOST", False),
        headers=auth(sa),
    )
    assert r.status_code == 201, r.text
    assert r.json()["allow_subdomains"] is False

    assert await _matches(db_session, f"https://{KA}/storage/pdf-files/FORM%20KMV%2012.pdf", KA)
    assert await _matches(db_session, f"https://{KA}", KA)
    assert await _matches(db_session, "https://TRANSPORT.Karnataka.gov.in/x.pdf", KA)
    for url in (
        f"https://{KA}.evil.example/x.pdf",  # suffix attack
        f"https://sub.{KA}/x.pdf",  # unapproved subdomain in exact-host mode
        f"https://evil{KA}/x.pdf",  # no label boundary
        f"http://{KA}/x.pdf",  # not https
    ):
        assert not await _matches(db_session, url, KA), url


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example#.gov.in/x.pdf",
        "https://evil.example?.gov.in/x.pdf",
        "https://evil.example\\.gov.in/x.pdf",
        "https://user@forms.gov.in/x.pdf",
        "https://forms.gov.in:8443/x.pdf",
        "https://forms.gov.in?x=1",
        "https:///forms.gov.in/x.pdf",
    ],
)
async def test_url_host_parsing_is_strict(
    client: AsyncClient, make_principal, db_session, url: str
) -> None:
    """Even with subdomains allowed, only a plain host followed by '/' or the end matches."""
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    r = await client.post(
        "/api/v1/admin/domains", json=_domain("gov.in", "GOV_TLD_SUFFIX", True), headers=auth(sa)
    )
    assert r.status_code == 201
    assert await _matches(db_session, "https://forms.gov.in/x.pdf", "gov.in")
    assert not await _matches(db_session, url, "gov.in")


async def test_exact_host_cannot_be_created_with_subdomains(
    client: AsyncClient, make_principal
) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    r = await client.post(
        "/api/v1/admin/domains",
        json=_domain(KA, "STATUTORY_EXACT_HOST", True),
        headers=auth(sa),
    )
    assert r.status_code == 422
    assert "exact-host" in r.text


async def test_legacy_exact_host_with_subdomains_can_be_narrowed(
    client: AsyncClient, make_principal, db_session
) -> None:
    """The F6.3 state: an exact-host row saved with subdomains on, corrected by an audited edit."""
    sa_id, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    await db_session.execute(
        text(
            "INSERT INTO approved_source_domains (domain_pattern, domain_type, "
            "allow_subdomains, rationale) VALUES (:p, 'STATUTORY_EXACT_HOST', TRUE, 'legacy')"
        ),
        {"p": KA},
    )
    await db_session.commit()
    r = await client.post(
        "/api/v1/admin/sources", json=_source("KA_T", f"https://{KA}/", KA), headers=auth(curator)
    )
    assert r.status_code == 201, r.text
    assert await _matches(db_session, f"https://sub.{KA}/x.pdf", KA)  # the gap

    body = {
        "expected_allow_subdomains": True,
        "allow_subdomains": False,
        "rationale": "Exact host only: forms are served from this host",
    }
    assert (await _scope(client, curator, KA, body)).status_code == 403
    r = await _scope(client, sa, KA, body)
    assert r.status_code == 200, r.text
    assert r.json()["allow_subdomains"] is False
    assert r.json()["domain_type"] == "STATUTORY_EXACT_HOST"
    assert r.json()["is_active"] is True

    assert await _matches(db_session, f"https://{KA}/x.pdf", KA)
    assert not await _matches(db_session, f"https://sub.{KA}/x.pdf", KA)

    # A second edit from the same (now stale) screen is refused.
    assert (await _scope(client, sa, KA, body)).status_code == 409
    # Re-widening an exact host is refused.
    widen = {**body, "expected_allow_subdomains": False, "allow_subdomains": True}
    assert (await _scope(client, sa, KA, widen)).status_code == 422

    row = (
        await db_session.execute(
            text(
                "SELECT actor_user_id, resource_id, metadata_jsonb FROM audit_logs "
                "WHERE event_type = 'ADMIN_APPROVED_DOMAIN_UPDATED'"
            )
        )
    ).one()
    assert row.actor_user_id == sa_id
    assert row.resource_id == KA
    assert row.metadata_jsonb == {
        "domain_pattern": KA,
        "changed_fields": "allow_subdomains,rationale",
        "previous_allow_subdomains": True,
        "allow_subdomains": False,
    }
    assert "Exact host only" not in json.dumps(row.metadata_jsonb)


async def test_narrowing_refused_while_subdomain_urls_are_in_use(
    client: AsyncClient, make_principal, db_session
) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, curator = await make_principal(UserRole.TEMPLATE_CURATOR)
    for pattern in ("gov.in", "nic.in"):
        r = await client.post(
            "/api/v1/admin/domains",
            json=_domain(pattern, "GOV_TLD_SUFFIX", True),
            headers=auth(sa),
        )
        assert r.status_code == 201
    r = await client.post(
        "/api/v1/admin/sources",
        json=_source("MORTH", "https://morth.gov.in/forms", "gov.in"),
        headers=auth(curator),
    )
    assert r.status_code == 201

    body = {"expected_allow_subdomains": True, "allow_subdomains": False, "rationale": "narrow"}
    r = await _scope(client, sa, "gov.in", body)
    assert r.status_code == 409
    assert "in use" in r.text
    # Nothing changed, and the other domain was never touched.
    rows = dict(
        (
            await db_session.execute(
                text("SELECT domain_pattern, allow_subdomains FROM approved_source_domains")
            )
        ).all()
    )
    assert rows == {"gov.in": True, "nic.in": True}
    count = await db_session.scalar(
        text("SELECT COUNT(*) FROM audit_logs WHERE event_type = 'ADMIN_APPROVED_DOMAIN_UPDATED'")
    )
    assert count == 0


async def test_domain_update_edge_cases(client: AsyncClient, make_principal, db_session) -> None:
    _, sa = await make_principal(UserRole.SUPER_ADMIN)
    _, citizen = await make_principal(UserRole.CITIZEN)
    body = {"expected_allow_subdomains": True, "allow_subdomains": True, "rationale": "x"}
    assert (await _scope(client, sa, "unknown.gov.in", body)).status_code == 404
    assert (await _scope(client, citizen, "unknown.gov.in", body)).status_code == 403
    assert (await client.post("/api/v1/admin/domains/x.gov.in/scope", json=body)).status_code == 401
    assert (await _scope(client, sa, "BAD_PATTERN", body)).status_code == 422
    bad = {**body, "domain_type": "GOV_TLD_SUFFIX"}  # type and pattern are not editable
    assert (await _scope(client, sa, "gov.in", bad)).status_code == 422

    r = await client.post(
        "/api/v1/admin/domains", json=_domain("gov.in", "GOV_TLD_SUFFIX", True), headers=auth(sa)
    )
    assert r.status_code == 201
    # A no-op edit changes nothing and writes no audit row.
    same = {**body, "rationale": "Official department website (test)"}
    assert (await _scope(client, sa, "gov.in", same)).status_code == 200
    count = await db_session.scalar(
        text("SELECT COUNT(*) FROM audit_logs WHERE event_type = 'ADMIN_APPROVED_DOMAIN_UPDATED'")
    )
    assert count == 0
