"""Template management & Maker-Checker service.

Covers government-source governance, form-template registration, versioned blank
PDF ingestion (validated + content-addressed + stored), the Maker-Checker state
machine with role separation and Ed25519 publication signing, and read access to
PUBLISHED artifacts only. Database triggers (approved-domain validation, maker-
checker CHECK, published immutability, append-only logs) provide defence in depth.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import (
    SourceCheckMethod,
    SourceCheckResult,
    TemplateVersionStatus,
    UserRole,
)
from app.core.dependencies import AuthContext, RequestContext
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.core.signing import (
    CURRENT_SIGNATURE_FORMAT_VERSION,
    sign_publisher_digest,
    template_bundle_digest_v1,
    template_bundle_digest_v2,
    verify_publisher_digest,
)
from app.modules.audit import service as audit
from app.modules.templates import schemas
from app.modules.templates.manifest_contract import TemplateIdentity, assert_valid_manifest
from app.modules.templates.models import (
    ApprovedSourceDomain,
    FormTemplate,
    GovernmentSource,
    GovernmentSourceCheck,
    TemplateApprovalLog,
    TemplateVersion,
)
from app.modules.templates.pdf_validator import validate_blank_pdf
from app.modules.templates.storage import get_storage, object_key_for

S = TemplateVersionStatus
R = UserRole


class TemplateEvent:
    DOMAIN_CREATED = "ADMIN_APPROVED_DOMAIN_CREATED"
    SOURCE_CREATED = "ADMIN_GOV_SOURCE_CREATED"
    SOURCE_CHECK_RECORDED = "ADMIN_GOV_SOURCE_CHECK_RECORDED"
    TEMPLATE_CREATED = "ADMIN_TEMPLATE_CREATED"
    VERSION_CREATED = "ADMIN_TEMPLATE_VERSION_CREATED"
    VERSION_TRANSITION = "ADMIN_TEMPLATE_VERSION_TRANSITION"


#: Permitted ``(from_status, to_status)`` transitions → roles allowed to perform
#: them. SUPER_ADMIN may drive any permitted transition; the Maker-Checker
#: distinctness rules below (and DB CHECK constraints) still apply regardless.
_TRANSITIONS: dict[tuple[str, str], frozenset[UserRole]] = {
    (S.DRAFT, S.IN_REVIEW): frozenset({R.TEMPLATE_CURATOR, R.SUPER_ADMIN}),
    (S.IN_REVIEW, S.VERIFIED): frozenset({R.TEMPLATE_VERIFIER, R.SUPER_ADMIN}),
    (S.VERIFIED, S.APPROVED): frozenset({R.TEMPLATE_APPROVER, R.SUPER_ADMIN}),
    (S.APPROVED, S.PUBLISHED): frozenset({R.TEMPLATE_APPROVER, R.SUPER_ADMIN}),
    (S.PUBLISHED, S.DEPRECATED): frozenset({R.TEMPLATE_APPROVER, R.SUPER_ADMIN}),
    # Rejection paths send a version back to DRAFT for rework.
    (S.IN_REVIEW, S.DRAFT): frozenset({R.TEMPLATE_VERIFIER, R.TEMPLATE_CURATOR, R.SUPER_ADMIN}),
    (S.VERIFIED, S.DRAFT): frozenset({R.TEMPLATE_APPROVER, R.SUPER_ADMIN}),
    (S.APPROVED, S.DRAFT): frozenset({R.TEMPLATE_APPROVER, R.SUPER_ADMIN}),
}


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


def canonical_manifest_json(manifest: dict[str, object]) -> str:
    """Canonical manifest text hashed into ``manifest_sha256``.

    Keys sorted by code point, no insignificant whitespace, every non-ASCII
    character escaped as ``\\uXXXX``. Clients reproduce exactly these bytes; the
    cross-language vectors live in ``docs/contracts/fixtures``.
    """
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _manifest_sha256(manifest: dict[str, object]) -> str:
    return hashlib.sha256(canonical_manifest_json(manifest).encode("utf-8")).hexdigest()


def bundle_digest_for(template: FormTemplate, version: TemplateVersion) -> str:
    """Recompute the signed digest of ``version`` in its own signature format."""
    if version.signature_format_version == 2:
        return template_bundle_digest_v2(
            template_id=str(template.id),
            slug=template.slug,
            jurisdiction=str(template.jurisdiction),
            category=str(template.category),
            official_form_code=template.official_form_code,
            semver=version.semver,
            published_version_id=str(version.id),
            blank_pdf_sha256=version.blank_pdf_sha256,
            manifest_sha256=version.manifest_sha256,
            page_count=version.page_count,
        )
    return template_bundle_digest_v1(
        template_id=str(template.id),
        semver=version.semver,
        blank_pdf_sha256=version.blank_pdf_sha256,
        manifest_sha256=version.manifest_sha256,
        page_count=version.page_count,
    )


@dataclass
class VersionUploadInput:
    semver: str
    source_pdf_download_url: str
    source_url_matched_domain: str
    source_downloaded_at: dt.datetime
    field_mapping_manifest: dict[str, object]
    government_source_id: uuid.UUID
    pdf_bytes: bytes


class TemplateAdminService:
    def __init__(self, session: AsyncSession, *, ctx: RequestContext) -> None:
        self.session = session
        self.ctx = ctx

    async def _audit(
        self,
        auth: AuthContext,
        event_type: str,
        resource_type: str,
        resource_id: str,
        meta: dict[str, object],
    ) -> None:
        await audit.record_event(
            self.session,
            event_type=event_type,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=self.ctx.request_id,
            actor_user_id=auth.user.id,
            actor_role=auth.role,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata=meta,
        )

    # ------------------------------------------------------------------ #
    # Approved domains
    # ------------------------------------------------------------------ #
    async def create_domain(
        self, auth: AuthContext, payload: schemas.AdminCreateApprovedDomainPayload
    ) -> ApprovedSourceDomain:
        if payload.domain_pattern != payload.domain_pattern.lower():
            raise ValidationFailedError("domain_pattern must be lowercase")
        existing = await self.session.get(ApprovedSourceDomain, payload.domain_pattern)
        if existing is not None:
            raise ConflictError("Domain pattern already registered")
        domain = ApprovedSourceDomain(
            domain_pattern=payload.domain_pattern,
            domain_type=payload.domain_type,
            allow_subdomains=payload.allow_subdomains,
            rationale=payload.rationale,
            approved_by_admin_id=auth.user.id,
        )
        self.session.add(domain)
        await self.session.flush()
        await self._audit(
            auth,
            TemplateEvent.DOMAIN_CREATED,
            "approved_source_domain",
            payload.domain_pattern,
            {"domain_pattern": payload.domain_pattern},
        )
        await self.session.commit()
        return domain

    # ------------------------------------------------------------------ #
    # Government sources
    # ------------------------------------------------------------------ #
    async def create_source(
        self, auth: AuthContext, payload: schemas.AdminCreateGovSourcePayload
    ) -> GovernmentSource:
        domain = await self.session.get(ApprovedSourceDomain, payload.matched_domain_pattern)
        if domain is None or not domain.is_active:
            raise ValidationFailedError("matched_domain_pattern is not an active approved domain")
        source = GovernmentSource(
            id=uuid.uuid4(),
            source_code=payload.source_code,
            jurisdiction=payload.jurisdiction,
            ministry_or_department=payload.ministry_or_department,
            official_portal_url=payload.official_portal_url,
            matched_domain_pattern=payload.matched_domain_pattern,
            source_type=payload.source_type,
            allow_automated_checksum_check=payload.allow_automated_checksum_check,
        )
        self.session.add(source)
        # The DB trigger validates official_portal_url against the approved domain.
        await self._flush_translating_domain_error("official_portal_url")
        await self._audit(
            auth,
            TemplateEvent.SOURCE_CREATED,
            "government_source",
            str(source.id),
            {"source_id": str(source.id), "source_code": source.source_code},
        )
        await self.session.commit()
        return source

    async def record_source_check(
        self,
        auth: AuthContext,
        source_id: uuid.UUID,
        payload: schemas.AdminRecordSourceCheckPayload,
    ) -> GovernmentSourceCheck:
        source = await self.session.get(GovernmentSource, source_id)
        if source is None:
            raise NotFoundError("Government source not found")
        check = GovernmentSourceCheck(
            id=uuid.uuid4(),
            government_source_id=source_id,
            template_version_id=payload.template_version_id,
            checked_url=payload.checked_url,
            check_method=payload.check_method,
            http_status_code=payload.http_status_code,
            etag_header=payload.etag_header,
            last_modified_header=payload.last_modified_header,
            observed_pdf_sha256=payload.observed_pdf_sha256,
            check_result=payload.check_result,
            checked_by_admin_id=auth.user.id,
        )
        self.session.add(check)
        if payload.check_result in (SourceCheckResult.INITIAL_INGEST, SourceCheckResult.UNCHANGED):
            source.last_verified_at = _now()
        await self.session.flush()
        await self._audit(
            auth,
            TemplateEvent.SOURCE_CHECK_RECORDED,
            "government_source_check",
            str(check.id),
            {"source_id": str(source_id), "check_result": str(payload.check_result)},
        )
        await self.session.commit()
        return check

    # ------------------------------------------------------------------ #
    # Templates & versions
    # ------------------------------------------------------------------ #
    async def create_template(
        self, auth: AuthContext, payload: schemas.AdminCreateTemplatePayload
    ) -> FormTemplate:
        source = await self.session.get(GovernmentSource, payload.government_source_id)
        if source is None:
            raise ValidationFailedError("government_source_id does not exist")
        if source.jurisdiction != payload.jurisdiction:
            raise ValidationFailedError("template jurisdiction must match its source")
        template = FormTemplate(
            id=uuid.uuid4(),
            government_source_id=payload.government_source_id,
            slug=payload.slug,
            jurisdiction=payload.jurisdiction,
            official_form_code=payload.official_form_code,
            category=payload.category,
            localized_titles=payload.localized_titles,
            localized_descriptions=payload.localized_descriptions,
        )
        self.session.add(template)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Template slug already exists") from exc
        await self._audit(
            auth,
            TemplateEvent.TEMPLATE_CREATED,
            "form_template",
            str(template.id),
            {"template_id": str(template.id), "slug": template.slug},
        )
        await self.session.commit()
        return template

    async def create_version(
        self, auth: AuthContext, template_id: uuid.UUID, data: VersionUploadInput
    ) -> TemplateVersion:
        template = await self.session.get(FormTemplate, template_id)
        if template is None:
            raise NotFoundError("Template not found")
        if template.government_source_id != data.government_source_id:
            raise ValidationFailedError("government_source_id does not match the template")

        validated = validate_blank_pdf(data.pdf_bytes)
        # The manifest must satisfy form-manifest contract v1 and describe exactly
        # this template version and this PDF (identity, pages, real form fields).
        assert_valid_manifest(
            data.field_mapping_manifest,
            expected=TemplateIdentity(
                slug=template.slug,
                jurisdiction=str(template.jurisdiction),
                category=str(template.category),
                official_form_code=template.official_form_code,
                semver=data.semver,
                page_count=validated.page_count,
                blank_pdf_sha256=validated.sha256,
            ),
            pdf=validated.form,
        )
        manifest_hash = _manifest_sha256(data.field_mapping_manifest)
        version_id = uuid.uuid4()
        # New versions always use the v2 digest. It binds the full identity and is
        # the exact value every reviewer confirms and the publisher key later signs.
        bundle_hash = template_bundle_digest_v2(
            template_id=str(template.id),
            slug=template.slug,
            jurisdiction=str(template.jurisdiction),
            category=str(template.category),
            official_form_code=template.official_form_code,
            semver=data.semver,
            published_version_id=str(version_id),
            blank_pdf_sha256=validated.sha256,
            manifest_sha256=manifest_hash,
            page_count=validated.page_count,
        )
        object_key = object_key_for(validated.sha256)
        get_storage().store_blank_pdf(object_key=object_key, data=data.pdf_bytes)

        version = TemplateVersion(
            id=version_id,
            signature_format_version=CURRENT_SIGNATURE_FORMAT_VERSION,
            template_id=template_id,
            semver=data.semver,
            status=S.DRAFT,
            source_pdf_download_url=data.source_pdf_download_url,
            source_url_matched_domain=data.source_url_matched_domain,
            source_downloaded_at=data.source_downloaded_at,
            blank_pdf_object_key=object_key,
            blank_pdf_sha256=validated.sha256,
            blank_pdf_size_bytes=validated.size_bytes,
            page_count=validated.page_count,
            field_mapping_manifest=data.field_mapping_manifest,
            manifest_sha256=manifest_hash,
            bundle_sha256=bundle_hash,
            created_by_admin_id=auth.user.id,
        )
        self.session.add(version)
        # DB trigger validates source_pdf_download_url against the approved domain.
        await self._flush_translating_domain_error(
            "source_pdf_download_url", unique_msg="This semver already exists for the template"
        )

        # Provenance: record the initial ingest check and the initial approval log.
        self.session.add(
            GovernmentSourceCheck(
                id=uuid.uuid4(),
                government_source_id=data.government_source_id,
                template_version_id=version.id,
                checked_url=data.source_pdf_download_url,
                check_method=SourceCheckMethod.ADMIN_MANUAL_UPLOAD,
                observed_pdf_sha256=validated.sha256,
                check_result=SourceCheckResult.INITIAL_INGEST,
                checked_by_admin_id=auth.user.id,
            )
        )
        self.session.add(
            TemplateApprovalLog(
                id=uuid.uuid4(),
                template_version_id=version.id,
                actor_admin_id=auth.user.id,
                from_status=S.DRAFT,
                to_status=S.DRAFT,
                recorded_bundle_sha256=bundle_hash,
                review_notes="Initial blank-PDF ingest",
            )
        )
        await self.session.flush()
        await self._audit(
            auth,
            TemplateEvent.VERSION_CREATED,
            "template_version",
            str(version.id),
            {
                "template_id": str(template_id),
                "version_id": str(version.id),
                "semver": version.semver,
            },
        )
        await self.session.commit()
        return version

    async def transition_version(
        self,
        auth: AuthContext,
        template_id: uuid.UUID,
        version_id: uuid.UUID,
        payload: schemas.AdminStateTransitionPayload,
    ) -> TemplateVersion:
        version = (
            await self.session.execute(
                select(TemplateVersion).where(TemplateVersion.id == version_id).with_for_update()
            )
        ).scalar_one_or_none()
        if version is None or version.template_id != template_id:
            raise NotFoundError("Template version not found")

        current = TemplateVersionStatus(version.status)
        target = payload.target_status
        key = (current, target)
        if key not in _TRANSITIONS:
            raise ConflictError(f"Illegal transition {current} -> {target}")
        if auth.role not in _TRANSITIONS[key]:
            raise ForbiddenError(f"Role {auth.role} may not perform {current} -> {target}")
        if payload.expected_bundle_sha256 != version.bundle_sha256:
            raise ConflictError("expected_bundle_sha256 does not match the current bundle")
        if target != S.DEPRECATED and (
            payload.deprecation_reason is not None or payload.replaced_by_template_slug is not None
        ):
            raise ValidationFailedError(
                "deprecation_reason / replaced_by_template_slug apply only to DEPRECATED"
            )

        now = _now()
        if target == S.PUBLISHED:
            await self._sign_for_publication(version)
        self._apply_transition(auth, version, target, now)
        if target == S.DEPRECATED:
            version.deprecation_reason = payload.deprecation_reason
            version.replaced_by_template_slug = payload.replaced_by_template_slug
        await self._update_published_pointer(version, target)

        self.session.add(
            TemplateApprovalLog(
                id=uuid.uuid4(),
                template_version_id=version.id,
                actor_admin_id=auth.user.id,
                from_status=current,
                to_status=target,
                recorded_bundle_sha256=version.bundle_sha256,
                review_notes=payload.review_notes,
            )
        )
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            # DB CHECK (maker-checker) or immutability trigger rejected it.
            raise ConflictError("Transition rejected by database integrity rules") from exc

        await self._audit(
            auth,
            TemplateEvent.VERSION_TRANSITION,
            "template_version",
            str(version.id),
            {
                "version_id": str(version.id),
                "template_id": str(template_id),
                "from_status": str(current),
                "to_status": str(target),
            },
        )
        await self.session.commit()
        return version

    def _apply_transition(
        self,
        auth: AuthContext,
        version: TemplateVersion,
        target: TemplateVersionStatus,
        now: dt.datetime,
    ) -> None:
        actor = auth.user.id
        if target == S.VERIFIED:
            if actor == version.created_by_admin_id:
                raise ForbiddenError("Creator cannot verify their own template version")
            version.verified_by_admin_id = actor
            version.verified_at = now
        elif target == S.APPROVED:
            if actor in {version.created_by_admin_id, version.verified_by_admin_id}:
                raise ForbiddenError("Approver must differ from creator and verifier")
            version.approved_by_admin_id = actor
            version.approved_at = now
        elif target == S.PUBLISHED:
            if version.approved_by_admin_id is None:
                raise ConflictError("Version must be APPROVED before publication")
            version.published_at = now
        elif target == S.DEPRECATED:
            version.deprecated_at = now
        elif target == S.DRAFT:
            # Rework: clear downstream review provenance.
            version.verified_by_admin_id = None
            version.verified_at = None
            version.approved_by_admin_id = None
            version.approved_at = None
        version.status = target

    async def _sign_for_publication(self, version: TemplateVersion) -> None:
        """Sign with the active publisher key after re-deriving the digest.

        The digest is recomputed from live rows rather than trusted from the stored
        column, so a publication can never sign a value that no longer describes
        the template (defence in depth against any out-of-band mutation).
        """
        if version.approved_by_admin_id is None:
            raise ConflictError("Version must be APPROVED before publication")
        template = await self.session.get(FormTemplate, version.template_id)
        if template is None:
            raise NotFoundError("Template not found")
        if _manifest_sha256(version.field_mapping_manifest) != version.manifest_sha256:
            raise ConflictError("Stored manifest no longer matches its recorded checksum")
        if bundle_digest_for(template, version) != version.bundle_sha256:
            raise ConflictError("Stored bundle digest no longer matches the template identity")
        signature, kid = sign_publisher_digest(version.bundle_sha256)
        version.publisher_ed25519_sig = signature
        version.signing_key_id = kid

    async def _update_published_pointer(
        self, version: TemplateVersion, target: TemplateVersionStatus
    ) -> None:
        """Maintain ``form_templates.current_published_version_id``."""
        if target == S.PUBLISHED:
            template = await self.session.get(FormTemplate, version.template_id)
            if template is not None:
                template.current_published_version_id = version.id
        elif target == S.DEPRECATED:
            template = await self.session.get(FormTemplate, version.template_id)
            if template is not None and template.current_published_version_id == version.id:
                template.current_published_version_id = None

    async def _flush_translating_domain_error(
        self, url_field: str, *, unique_msg: str | None = None
    ) -> None:
        try:
            await self.session.flush()
        except DBAPIError as exc:
            # IntegrityError (unique) and the plpgsql domain-validation trigger's
            # RaiseError both surface here; translate to clean 4xx responses.
            await self.session.rollback()
            text = str(exc.orig).lower()
            if "does not match active approved domain" in text:
                raise ValidationFailedError(
                    f"{url_field} does not point to an approved government domain"
                ) from exc
            if unique_msg and ("unique" in text or "duplicate" in text):
                raise ConflictError(unique_msg) from exc
            raise


class TemplateReadService:
    """Public, PUBLISHED-only read access."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_sources(
        self, *, jurisdiction: str | None, page: int, page_size: int
    ) -> tuple[list[GovernmentSource], int]:
        base = select(GovernmentSource).where(GovernmentSource.is_active.is_(True))
        if jurisdiction:
            base = base.where(GovernmentSource.jurisdiction == jurisdiction)
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        rows = (
            (
                await self.session.execute(
                    base.order_by(GovernmentSource.source_code)
                    .limit(page_size)
                    .offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), int(total or 0)

    def _published_templates_query(self) -> Select[FormTemplate]:
        """Templates whose current pointer references a PUBLISHED version."""
        published_ids = select(TemplateVersion.id).where(TemplateVersion.status == S.PUBLISHED)
        return select(FormTemplate).where(
            FormTemplate.is_active.is_(True),
            FormTemplate.current_published_version_id.in_(published_ids),
        )

    async def _load_published_version(self, template: FormTemplate) -> TemplateVersion | None:
        if template.current_published_version_id is None:
            return None
        version = await self.session.get(TemplateVersion, template.current_published_version_id)
        if version is None or version.status != S.PUBLISHED:
            return None
        return version

    async def list_templates(
        self,
        *,
        jurisdiction: str | None,
        category: str | None,
        search_code: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[tuple[FormTemplate, TemplateVersion]], int]:
        base = self._published_templates_query()
        if jurisdiction:
            base = base.where(FormTemplate.jurisdiction == jurisdiction)
        if category:
            base = base.where(FormTemplate.category == category)
        if search_code:
            base = base.where(FormTemplate.official_form_code.ilike(f"%{search_code}%"))
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        templates = (
            (
                await self.session.execute(
                    base.order_by(FormTemplate.slug).limit(page_size).offset((page - 1) * page_size)
                )
            )
            .scalars()
            .all()
        )
        pairs: list[tuple[FormTemplate, TemplateVersion]] = []
        for template in templates:
            version = await self._load_published_version(template)
            if version is not None:
                pairs.append((template, version))
        return pairs, int(total or 0)

    async def get_published_template(
        self, slug: str
    ) -> tuple[FormTemplate, TemplateVersion, GovernmentSource]:
        template = (
            await self.session.execute(
                select(FormTemplate).where(
                    FormTemplate.slug == slug, FormTemplate.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
        if template is None:
            raise NotFoundError("Published template not found")
        version = await self._load_published_version(template)
        if version is None:
            raise NotFoundError("Published template not found")
        source = await self.session.get(GovernmentSource, template.government_source_id)
        if source is None:
            raise NotFoundError("Published template not found")
        return template, version, source

    async def get_published_bundle(
        self, slug: str, semver: str
    ) -> tuple[FormTemplate, TemplateVersion, bool]:
        template = (
            await self.session.execute(select(FormTemplate).where(FormTemplate.slug == slug))
        ).scalar_one_or_none()
        if template is None:
            raise NotFoundError("Published bundle not found")
        version = (
            await self.session.execute(
                select(TemplateVersion).where(
                    TemplateVersion.template_id == template.id,
                    TemplateVersion.semver == semver,
                    TemplateVersion.status == S.PUBLISHED,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundError("Published bundle not found")
        return template, version, _verify_published_signature(template, version)

    async def get_version_status(
        self, slug: str, semver: str
    ) -> schemas.TemplateVersionStatusResponse:
        """Lifecycle of ``slug@semver`` without leaking any unpublished artifact."""
        template = (
            await self.session.execute(select(FormTemplate).where(FormTemplate.slug == slug))
        ).scalar_one_or_none()
        if template is None:
            return schemas.TemplateVersionStatusResponse(
                slug=slug, semver=semver, status="UNKNOWN_TEMPLATE"
            )
        version = (
            await self.session.execute(
                select(TemplateVersion).where(
                    TemplateVersion.template_id == template.id,
                    TemplateVersion.semver == semver,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            return schemas.TemplateVersionStatusResponse(
                slug=slug, semver=semver, status="UNKNOWN_VERSION"
            )
        if version.status == S.PUBLISHED:
            return schemas.TemplateVersionStatusResponse(
                slug=slug, semver=semver, status="PUBLISHED", published_version_id=version.id
            )
        if version.status == S.DEPRECATED:
            return schemas.TemplateVersionStatusResponse(
                slug=slug,
                semver=semver,
                status="DEPRECATED",
                published_version_id=version.id,
                deprecated_at=version.deprecated_at,
                deprecation_reason=version.deprecation_reason,
                replaced_by_template_slug=version.replaced_by_template_slug,
            )
        # DRAFT / IN_REVIEW / VERIFIED / APPROVED: exists but was never public.
        return schemas.TemplateVersionStatusResponse(
            slug=slug, semver=semver, status="NEVER_PUBLISHED"
        )


def _verify_published_signature(template: FormTemplate, version: TemplateVersion) -> bool:
    """Server self-check: digest reproduces AND the stored signature verifies.

    Rotation- and format-aware: the key is selected by the row's ``signing_key_id``
    and the digest by its ``signature_format_version``. A retired key id fails.
    """
    if not version.publisher_ed25519_sig or not version.signing_key_id:
        return False
    if bundle_digest_for(template, version) != version.bundle_sha256:
        return False
    return verify_publisher_digest(
        version.bundle_sha256, version.publisher_ed25519_sig, version.signing_key_id
    )
