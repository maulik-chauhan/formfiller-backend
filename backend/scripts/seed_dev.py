"""Seed a local development database with TEST-ONLY templates.

    python -m scripts.seed_dev                         # publish the fixture templates
    python -m scripts.seed_dev --deprecate <slug>      # withdraw one (lifecycle demo)

Publishes the contract fixtures (``docs/contracts/fixtures``) through the real
service layer: PDF validation, manifest contract validation, the full maker-checker
chain with three distinct administrators, and publisher signing. Nothing is inserted
behind the application's back except the administrator accounts themselves.

EVERYTHING SEEDED IS FICTIONAL. The "source" is a reserved ``.invalid`` host, the
templates are labelled as test fixtures, and none of it is a government form.
Refuses to run outside a local development/test environment.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import pathlib
import sys
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.core.constants import TemplateVersionStatus, UserRole
from app.core.dependencies import AuthContext, RequestContext
from app.modules.auth.models import User
from app.modules.templates import schemas
from app.modules.templates.models import (
    ApprovedSourceDomain,
    FormTemplate,
    GovernmentSource,
    TemplateVersion,
)
from app.modules.templates.service import TemplateAdminService, VersionUploadInput

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "docs" / "contracts" / "fixtures"
DOMAIN = "test-fixtures.invalid"
SOURCE_CODE = "TEST_FIXTURES"
#: (manifest fixture, PDF fixture, title)
TEMPLATES = (
    ("fillable-acroform", "fillable-test.pdf", "[TEST FIXTURE] Fillable sample form"),
    ("coordinate-mapped", "flat-test.pdf", "[TEST FIXTURE] Coordinate-mapped sample form"),
    ("simple-text", "flat-test.pdf", "[TEST FIXTURE] Simple text sample form"),
)
CTX = RequestContext(ip_subnet_hash="0" * 64, request_id="seed-dev")
S = TemplateVersionStatus


async def _admin(session: Any, role: UserRole, index: int) -> AuthContext:
    blind = f"{0x5EED0000 + index:064x}"
    user = (
        await session.execute(select(User).where(User.mobile_blind_index == blind))
    ).scalar_one_or_none()
    if user is None:
        now = dt.datetime.now(tz=dt.UTC)
        user = User(
            id=uuid.uuid4(),
            mobile_blind_index=blind,
            blind_index_key_version=1,
            role=role,
            account_status="ACTIVE",
            is_mobile_verified=True,
            mobile_verified_at=now,
        )
        session.add(user)
        await session.commit()
    return AuthContext(user=user, session=None, role=role)  # type: ignore[arg-type]


async def _transition(
    session: Any, auth: AuthContext, version: TemplateVersion, target: S, **extra: Any
) -> TemplateVersion:
    return await TemplateAdminService(session, ctx=CTX).transition_version(
        auth,
        version.template_id,
        version.id,
        schemas.AdminStateTransitionPayload(
            target_status=target,
            expected_bundle_sha256=version.bundle_sha256,
            review_notes="Development seed (test fixture)",
            **extra,
        ),
    )


async def seed(session: Any) -> None:
    sa = await _admin(session, UserRole.SUPER_ADMIN, 0)
    curator = await _admin(session, UserRole.TEMPLATE_CURATOR, 1)
    verifier = await _admin(session, UserRole.TEMPLATE_VERIFIER, 2)
    approver = await _admin(session, UserRole.TEMPLATE_APPROVER, 3)
    service = TemplateAdminService(session, ctx=CTX)

    if await session.get(ApprovedSourceDomain, DOMAIN) is None:
        await service.create_domain(
            sa,
            schemas.AdminCreateApprovedDomainPayload(
                domain_pattern=DOMAIN,
                domain_type="STATUTORY_EXACT_HOST",
                allow_subdomains=False,
                rationale="Reserved .invalid host for local test fixtures only",
            ),
        )
    source = (
        await session.execute(
            select(GovernmentSource).where(GovernmentSource.source_code == SOURCE_CODE)
        )
    ).scalar_one_or_none()
    if source is None:
        source = await service.create_source(
            curator,
            schemas.AdminCreateGovSourcePayload(
                source_code=SOURCE_CODE,
                jurisdiction="CENTRAL_INDIA",
                ministry_or_department="Test fixtures (not a government body)",
                official_portal_url=f"https://{DOMAIN}/",
                matched_domain_pattern=DOMAIN,
                source_type="OFFICIAL_GOV_PORTAL",
            ),
        )
        await service.record_source_check(
            curator,
            source.id,
            schemas.AdminRecordSourceCheckPayload(
                checked_url=f"https://{DOMAIN}/",
                check_method="ADMIN_MANUAL_UPLOAD",
                check_result="INITIAL_INGEST",
            ),
        )

    for manifest_name, pdf_name, title in TEMPLATES:
        manifest = json.loads(
            (FIXTURES / "manifests" / "valid" / f"{manifest_name}.json").read_text()
        )
        identity = manifest["identity"]
        existing = (
            await session.execute(select(FormTemplate).where(FormTemplate.slug == identity["slug"]))
        ).scalar_one_or_none()
        if existing is not None:
            print(f"= {identity['slug']} already seeded")
            continue
        template = await service.create_template(
            curator,
            schemas.AdminCreateTemplatePayload(
                government_source_id=source.id,
                slug=identity["slug"],
                jurisdiction=identity["jurisdiction"],
                official_form_code=identity["official_form_code"],
                category=identity["category"],
                localized_titles={"en": title},
                localized_descriptions={
                    "en": "Fictional test template for development. Not a government form."
                },
            ),
        )
        version = await service.create_version(
            curator,
            template.id,
            VersionUploadInput(
                semver=identity["semver"],
                source_pdf_download_url=f"https://{DOMAIN}/{pdf_name}",
                source_url_matched_domain=DOMAIN,
                source_downloaded_at=dt.datetime.now(tz=dt.UTC),
                field_mapping_manifest=manifest,
                government_source_id=source.id,
                pdf_bytes=(FIXTURES / "pdfs" / pdf_name).read_bytes(),
            ),
        )
        for target, actor in (
            (S.IN_REVIEW, curator),
            (S.VERIFIED, verifier),
            (S.APPROVED, approver),
            (S.PUBLISHED, approver),
        ):
            version = await _transition(session, actor, version, target)
        print(f"+ published {identity['slug']}@{version.semver} ({version.signing_key_id})")


async def deprecate(session: Any, slug: str) -> None:
    approver = await _admin(session, UserRole.TEMPLATE_APPROVER, 3)
    version = (
        await session.execute(
            select(TemplateVersion)
            .join(FormTemplate, FormTemplate.id == TemplateVersion.template_id)
            .where(FormTemplate.slug == slug, TemplateVersion.status == S.PUBLISHED)
        )
    ).scalar_one_or_none()
    if version is None:
        raise SystemExit(f"no PUBLISHED version of {slug!r}")
    await _transition(
        session,
        approver,
        version,
        S.DEPRECATED,
        deprecation_reason="Withdrawn by the development seed script (lifecycle demo)",
    )
    print(f"- deprecated {slug}@{version.semver}")


async def main() -> None:
    settings = get_settings()
    if settings.is_shared_environment:
        raise SystemExit("seed_dev refuses to run in a shared environment")
    engine = create_async_engine(settings.database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            if "--deprecate" in sys.argv:
                await deprecate(session, sys.argv[sys.argv.index("--deprecate") + 1])
            else:
                await seed(session)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
