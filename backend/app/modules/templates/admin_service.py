"""Admin console services (F5): read models, dry-run validation, draft rework.

Everything here is internal tooling for template curators, verifiers and approvers.
It reads and writes only template governance data (blank government PDFs, field
manifests, provenance). No applicant data exists in these tables or flows.

Write paths reuse the same validators as the strict upload path in ``service.py``
(blank-PDF validator, manifest contract, identity binding, approved-domain trigger),
so a dry-run report and a real upload can never disagree about what is valid.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import Jurisdiction, TemplateCategory, TemplateVersionStatus, UserRole
from app.core.dependencies import AuthContext, RequestContext
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationFailedError
from app.core.signing import CURRENT_SIGNATURE_FORMAT_VERSION, template_bundle_digest_v2
from app.modules.audit import service as audit
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.templates import schemas
from app.modules.templates.manifest_contract import (
    Issue,
    TemplateIdentity,
    assert_valid_manifest,
    validate_against_pdf,
    validate_identity,
    validate_manifest,
)
from app.modules.templates.models import (
    ApprovedSourceDomain,
    FormTemplate,
    GovernmentSource,
    TemplateApprovalLog,
    TemplateVersion,
)
from app.modules.templates.pdf_validator import (
    PdfValidationError,
    ValidatedPdf,
    validate_blank_pdf,
)
from app.modules.templates.service import (
    _TRANSITIONS,
    TemplateEvent,
    VersionUploadInput,
    _manifest_sha256,
    _verify_published_signature,
    check_source_downloaded_at,
)
from app.modules.templates.storage import get_storage, object_key_for

S = TemplateVersionStatus
R = UserRole

#: Roles that may use the admin console at all. Citizens get 403 everywhere.
ADMIN_ROLES: tuple[UserRole, ...] = (
    R.TEMPLATE_CURATOR,
    R.TEMPLATE_VERIFIER,
    R.TEMPLATE_APPROVER,
    R.SUPER_ADMIN,
)
MAKER_ROLES: tuple[UserRole, ...] = (R.TEMPLATE_CURATOR, R.SUPER_ADMIN)
_SEMVER_PARTS = 3


def _semver_key(value: str) -> tuple[int, ...] | None:
    parts = value.split(".")
    if len(parts) != _SEMVER_PARTS or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


@dataclass(frozen=True)
class TemplateListFilters:
    query: str | None
    status: TemplateVersionStatus | None
    jurisdiction: str | None
    category: str | None
    page: int
    page_size: int


def _version_summary(version: TemplateVersion) -> schemas.AdminVersionSummary:
    return schemas.AdminVersionSummary.model_validate(version)


class TemplateConsoleService:
    def __init__(self, session: AsyncSession, *, ctx: RequestContext) -> None:
        self.session = session
        self.ctx = ctx

    # ------------------------------------------------------------------ #
    # Identity
    # ------------------------------------------------------------------ #
    @staticmethod
    def principal(auth: AuthContext) -> schemas.AdminPrincipalResponse:
        role = auth.role

        def may(*targets: tuple[S, S]) -> bool:
            return any(role in _TRANSITIONS[t] for t in targets)

        return schemas.AdminPrincipalResponse(
            user_id=auth.user.id,
            role=role.value,
            can_create=role in MAKER_ROLES,
            can_verify=may((S.IN_REVIEW, S.VERIFIED)),
            can_approve=may((S.VERIFIED, S.APPROVED), (S.APPROVED, S.PUBLISHED)),
            can_manage_domains=role == R.SUPER_ADMIN,
        )

    # ------------------------------------------------------------------ #
    # Read models
    # ------------------------------------------------------------------ #
    async def list_domains(self) -> list[ApprovedSourceDomain]:
        rows = await self.session.execute(
            select(ApprovedSourceDomain).order_by(ApprovedSourceDomain.domain_pattern)
        )
        return list(rows.scalars().all())

    async def list_templates(
        self, filters: TemplateListFilters
    ) -> tuple[list[schemas.AdminTemplateListItem], int]:
        base: Select[Any] = select(FormTemplate)
        if filters.query:
            needle = f"%{filters.query.strip()}%"
            base = base.where(
                or_(
                    FormTemplate.slug.ilike(needle),
                    FormTemplate.official_form_code.ilike(needle),
                    FormTemplate.localized_titles["en"].astext.ilike(needle),
                )
            )
        if filters.jurisdiction:
            base = base.where(FormTemplate.jurisdiction == filters.jurisdiction)
        if filters.category:
            base = base.where(FormTemplate.category == filters.category)
        if filters.status:
            having = select(TemplateVersion.template_id).where(
                TemplateVersion.status == filters.status
            )
            base = base.where(FormTemplate.id.in_(having))
        total = await self.session.scalar(select(func.count()).select_from(base.subquery()))
        templates = (
            (
                await self.session.execute(
                    base.order_by(FormTemplate.created_at.desc(), FormTemplate.slug)
                    .limit(filters.page_size)
                    .offset((filters.page - 1) * filters.page_size)
                )
            )
            .scalars()
            .all()
        )
        items: list[schemas.AdminTemplateListItem] = []
        for template in templates:
            versions = await self._versions(template.id)
            published = next((v for v in versions if v.status == S.PUBLISHED), None)
            items.append(
                schemas.AdminTemplateListItem(
                    id=template.id,
                    slug=template.slug,
                    jurisdiction=Jurisdiction(template.jurisdiction),
                    category=TemplateCategory(template.category),
                    official_form_code=template.official_form_code,
                    localized_titles=template.localized_titles,
                    is_active=template.is_active,
                    version_count=len(versions),
                    latest_version=_version_summary(versions[0]) if versions else None,
                    published_semver=published.semver if published else None,
                    created_at=template.created_at,
                )
            )
        return items, int(total or 0)

    async def _versions(self, template_id: uuid.UUID) -> list[TemplateVersion]:
        rows = await self.session.execute(
            select(TemplateVersion)
            .where(TemplateVersion.template_id == template_id)
            .order_by(TemplateVersion.created_at.desc())
        )
        return list(rows.scalars().all())

    async def _template(self, template_id: uuid.UUID) -> FormTemplate:
        template = await self.session.get(FormTemplate, template_id)
        if template is None:
            raise NotFoundError("Template not found")
        return template

    async def _version(self, template_id: uuid.UUID, version_id: uuid.UUID) -> TemplateVersion:
        version = await self.session.get(TemplateVersion, version_id)
        if version is None or version.template_id != template_id:
            raise NotFoundError("Template version not found")
        return version

    async def get_template(self, template_id: uuid.UUID) -> schemas.AdminTemplateDetailResponse:
        template = await self._template(template_id)
        source = await self.session.get(GovernmentSource, template.government_source_id)
        if source is None:
            raise NotFoundError("Government source not found")
        versions = await self._versions(template.id)
        ever_public = any(v.status in (S.PUBLISHED, S.DEPRECATED) for v in versions)
        published = next((v for v in versions if v.status == S.PUBLISHED), None)
        base = schemas.AdminTemplateResponse.model_validate(template).model_dump()
        return schemas.AdminTemplateDetailResponse(
            **base,
            government_source=schemas.GovernmentSourceResponse.model_validate(source),
            published_semver=published.semver if published else None,
            titles_editable=not ever_public,
            identity_editable=not versions,
            versions=[_version_summary(v) for v in versions],
        )

    async def get_version(
        self, auth: AuthContext, template_id: uuid.UUID, version_id: uuid.UUID
    ) -> schemas.AdminVersionDetailResponse:
        template = await self._template(template_id)
        version = await self._version(template_id, version_id)
        logs = (
            await self.session.execute(
                select(TemplateApprovalLog, User.role)
                .join(User, User.id == TemplateApprovalLog.actor_admin_id, isouter=True)
                .where(TemplateApprovalLog.template_version_id == version.id)
                .order_by(TemplateApprovalLog.created_at, TemplateApprovalLog.id)
            )
        ).all()
        events = (
            (
                await self.session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.resource_type == "template_version",
                        AuditLog.resource_id == str(version.id),
                    )
                    .order_by(AuditLog.occurred_at)
                )
            )
            .scalars()
            .all()
        )
        signed = version.status in (S.PUBLISHED, S.DEPRECATED)
        base = schemas.AdminTemplateVersionResponse.model_validate(version).model_dump()
        return schemas.AdminVersionDetailResponse(
            **base,
            source_downloaded_at=version.source_downloaded_at,
            verified_at=version.verified_at,
            approved_at=version.approved_at,
            deprecated_at=version.deprecated_at,
            field_mapping_manifest=version.field_mapping_manifest,
            signature_verified=_verify_published_signature(template, version) if signed else None,
            can_replace_artifacts=(
                version.status == S.DRAFT
                and auth.role in MAKER_ROLES
                and auth.user.id == version.created_by_admin_id
            ),
            actions=self._actions(auth, version),
            approval_history=[
                schemas.AdminApprovalLogEntry(
                    id=log.id,
                    from_status=S(log.from_status),
                    to_status=S(log.to_status),
                    actor_admin_id=log.actor_admin_id,
                    actor_role=role,
                    review_notes=log.review_notes,
                    recorded_bundle_sha256=log.recorded_bundle_sha256,
                    created_at=log.created_at,
                )
                for log, role in logs
            ],
            audit_events=[
                schemas.AdminAuditEntry(
                    event_type=e.event_type,
                    actor_user_id=e.actor_user_id,
                    actor_role=e.actor_role,
                    occurred_at=e.occurred_at,
                    metadata=e.metadata_jsonb,
                )
                for e in events
            ],
        )

    @staticmethod
    def _actions(auth: AuthContext, version: TemplateVersion) -> list[schemas.AdminAllowedAction]:
        """Every legal next state, with whether *this* admin may take it and why not.

        Mirrors the checks in ``TemplateAdminService.transition_version``; the backend
        still enforces them on the actual call.
        """
        current = S(version.status)
        actor = auth.user.id
        out: list[schemas.AdminAllowedAction] = []
        for (src, dst), roles in _TRANSITIONS.items():
            if src != current:
                continue
            reason: str | None = None
            if auth.role not in roles:
                reason = f"Your role ({auth.role}) cannot move a version from {src} to {dst}"
            elif dst == S.VERIFIED and actor == version.created_by_admin_id:
                reason = "You created this version, so someone else must verify it"
            elif dst == S.APPROVED and actor in {
                version.created_by_admin_id,
                version.verified_by_admin_id,
            }:
                reason = "The approver must be different from the creator and the verifier"
            out.append(
                schemas.AdminAllowedAction(target_status=dst, allowed=reason is None, reason=reason)
            )
        return out

    async def version_for_rework(
        self, template_id: uuid.UUID, version_id: uuid.UUID
    ) -> TemplateVersion:
        version = await self._version(template_id, version_id)
        if version.status != S.DRAFT:
            raise ConflictError(
                f"Only a DRAFT version can be reworked; this one is {version.status}"
            )
        return version

    async def read_blank_pdf(self, template_id: uuid.UUID, version_id: uuid.UUID) -> bytes:
        version = await self._version(template_id, version_id)
        data = get_storage().read_blank_pdf(version.blank_pdf_object_key)
        if data is None:
            raise NotFoundError("The blank PDF for this version is not in storage")
        if hashlib.sha256(data).hexdigest() != version.blank_pdf_sha256:
            # Never hand an admin a file that differs from what reviewers signed off.
            raise ConflictError("Stored blank PDF no longer matches its recorded checksum")
        return data

    # ------------------------------------------------------------------ #
    # Dry-run validation
    # ------------------------------------------------------------------ #
    async def validate_upload(
        self,
        template_id: uuid.UUID,
        data: VersionUploadInput,
        *,
        replacing: TemplateVersion | None = None,
        manifest_json_error: str | None = None,
    ) -> schemas.VersionValidationReport:
        """Run every upload check and report all problems at once. Stores nothing."""
        template = await self._template(template_id)
        issues: list[schemas.ValidationIssue] = []

        def error(code: str, path: str, message: str) -> None:
            issues.append(
                schemas.ValidationIssue(severity="error", code=code, path=path, message=message)
            )

        def warn(code: str, path: str, message: str) -> None:
            issues.append(
                schemas.ValidationIssue(severity="warning", code=code, path=path, message=message)
            )

        # --- Version identity -------------------------------------------------
        if _semver_key(data.semver) is None:
            error("semver_invalid", "semver", "Use MAJOR.MINOR.PATCH, for example 1.0.0")
        if replacing is not None and data.semver != replacing.semver:
            error(
                "semver_fixed",
                "semver",
                f"A rework keeps the version number {replacing.semver}",
            )
        replacing_id = replacing.id if replacing is not None else None
        siblings = [v for v in await self._versions(template.id) if v.id != replacing_id]
        if replacing is None and any(v.semver == data.semver for v in siblings):
            error(
                "semver_exists",
                "semver",
                f"Version {data.semver} already exists for this template; use a new version number",
            )
        key = _semver_key(data.semver)
        newest = max((k for k in (_semver_key(v.semver) for v in siblings) if k), default=None)
        if replacing is None and key and newest and key <= newest:
            warn(
                "semver_not_increasing",
                "semver",
                "This version number is not higher than an existing version of this template",
            )

        # --- Official source metadata ----------------------------------------
        if data.government_source_id != template.government_source_id:
            error(
                "government_source_mismatch",
                "government_source_id",
                "The government source must be the one this template was registered with",
            )
        source = await self.session.get(GovernmentSource, template.government_source_id)
        problem = check_source_downloaded_at(data.source_downloaded_at)
        if problem:
            error("source_downloaded_at_invalid", "source_downloaded_at", problem)
        approved = await self.session.scalar(
            text("SELECT fn_validate_url_against_approved_domains(:url, :pattern)"),
            {"url": data.source_pdf_download_url, "pattern": data.source_url_matched_domain},
        )
        if not approved:
            error(
                "source_url_not_approved",
                "source_pdf_download_url",
                "The official download URL must be https and on the approved domain "
                f"{data.source_url_matched_domain!r} (or one of its subdomains, if allowed)",
            )
        elif source is not None and data.source_url_matched_domain != source.matched_domain_pattern:
            warn(
                "source_domain_differs",
                "source_url_matched_domain",
                f"The PDF comes from {data.source_url_matched_domain!r}, but the template's "
                f"official source is registered under {source.matched_domain_pattern!r}",
            )

        # --- Blank PDF --------------------------------------------------------
        validated: ValidatedPdf | None = None
        try:
            validated = validate_blank_pdf(data.pdf_bytes)
        except PdfValidationError as exc:
            error("pdf_invalid", "blank_pdf", exc.detail or exc.title)

        # --- Manifest ---------------------------------------------------------
        manifest_issues: list[Issue] = []
        if manifest_json_error is not None:
            error("manifest_json_invalid", "manifest", manifest_json_error)
        else:
            manifest_issues = validate_manifest(data.field_mapping_manifest)
        if manifest_json_error is None and not manifest_issues and validated is not None:
            manifest_issues = validate_identity(
                data.field_mapping_manifest, self._identity(template, data.semver, validated)
            ) + validate_against_pdf(data.field_mapping_manifest, validated.form)
        for issue in manifest_issues:
            error(issue.code, f"manifest{issue.path[1:]}", issue.message)

        manifest_hash: str | None = None
        if manifest_json_error is None:
            try:
                manifest_hash = _manifest_sha256(data.field_mapping_manifest)
            except (TypeError, ValueError):
                manifest_hash = None
        if validated is not None and manifest_hash is not None:
            twin = next(
                (
                    v
                    for v in siblings
                    if v.blank_pdf_sha256 == validated.sha256 and v.manifest_sha256 == manifest_hash
                ),
                None,
            )
            if twin is not None:
                warn(
                    "duplicate_content",
                    "blank_pdf",
                    f"This PDF and manifest are identical to version {twin.semver}",
                )

        return schemas.VersionValidationReport(
            valid=not any(i.severity == "error" for i in issues),
            pdf=self._inspection(validated) if validated else None,
            manifest_sha256=manifest_hash,
            expected_identity=(
                vars(self._identity(template, data.semver, validated)) if validated else None
            ),
            issues=issues,
        )

    @staticmethod
    def _identity(template: FormTemplate, semver: str, pdf: ValidatedPdf) -> TemplateIdentity:
        return TemplateIdentity(
            slug=template.slug,
            jurisdiction=str(template.jurisdiction),
            category=str(template.category),
            official_form_code=template.official_form_code,
            semver=semver,
            page_count=pdf.page_count,
            blank_pdf_sha256=pdf.sha256,
        )

    @staticmethod
    def _inspection(pdf: ValidatedPdf) -> schemas.PdfInspection:
        fields = []
        for name, info in sorted(pdf.form.fields.items()):
            if info.kind == "other":
                continue
            # Radio groups expose index states ("0", "1") alongside their names.
            options = [s for s in dict.fromkeys(info.states) if not s.isdigit()]
            fields.append(schemas.PdfFieldInspection(name=name, kind=info.kind, options=options))
        return schemas.PdfInspection(
            sha256=pdf.sha256,
            size_bytes=pdf.size_bytes,
            page_count=pdf.page_count,
            pages=[
                schemas.PdfPageInspection(width=p.width, height=p.height, rotation=p.rotation)
                for p in pdf.form.pages
            ],
            fields=fields,
        )

    # ------------------------------------------------------------------ #
    # Metadata editing (before publication)
    # ------------------------------------------------------------------ #
    async def update_metadata(
        self,
        auth: AuthContext,
        template_id: uuid.UUID,
        payload: schemas.AdminTemplateMetadataPayload,
    ) -> FormTemplate:
        template = (
            await self.session.execute(
                select(FormTemplate).where(FormTemplate.id == template_id).with_for_update()
            )
        ).scalar_one_or_none()
        if template is None:
            raise NotFoundError("Template not found")
        versions = await self._versions(template.id)
        ever_public = any(v.status in (S.PUBLISHED, S.DEPRECATED) for v in versions)
        changed: list[str] = []

        if payload.localized_titles is not None or payload.localized_descriptions is not None:
            if ever_public:
                raise ConflictError(
                    "Titles and descriptions are fixed once a version has been published"
                )
            for name in ("localized_titles", "localized_descriptions"):
                texts: dict[str, str] | None = getattr(payload, name)
                if texts is None:
                    continue
                if not any(isinstance(v, str) and v.strip() for v in texts.values()):
                    raise ValidationFailedError(f"{name} needs at least one non-empty entry")
                if texts != getattr(template, name):
                    setattr(template, name, texts)
                    changed.append(name)

        identity = {
            "official_form_code": payload.official_form_code,
            "category": payload.category.value if payload.category else None,
            "government_source_id": payload.government_source_id,
        }
        requested = {k: v for k, v in identity.items() if v is not None}
        if requested:
            if versions:
                raise ConflictError(
                    "The form code, category and source are bound into every version's "
                    "signature and manifest; they can only change before the first upload"
                )
            source_id = requested.get("government_source_id")
            if source_id is not None:
                source = await self.session.get(GovernmentSource, source_id)
                if source is None:
                    raise ValidationFailedError("government_source_id does not exist")
                if source.jurisdiction != template.jurisdiction:
                    raise ValidationFailedError("template jurisdiction must match its source")
            for name, value in requested.items():
                if getattr(template, name) != value:
                    setattr(template, name, value)
                    changed.append(name)

        if not changed:
            return template
        template.updated_at = dt.datetime.now(tz=dt.UTC)
        await self.session.flush()
        await audit.record_event(
            self.session,
            event_type=TemplateEvent.TEMPLATE_METADATA_UPDATED,
            resource_type="form_template",
            resource_id=str(template.id),
            request_id=self.ctx.request_id,
            actor_user_id=auth.user.id,
            actor_role=auth.role,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={"template_id": str(template.id), "changed_fields": ",".join(changed)},
        )
        await self.session.commit()
        return template

    # ------------------------------------------------------------------ #
    # Draft rework after rejection
    # ------------------------------------------------------------------ #
    async def replace_artifacts(
        self,
        auth: AuthContext,
        template_id: uuid.UUID,
        version_id: uuid.UUID,
        data: VersionUploadInput,
    ) -> TemplateVersion:
        """Replace the PDF/manifest of a DRAFT version (e.g. after a rejection).

        Only the version's own maker may do this, so the maker-checker separation
        recorded in ``created_by_admin_id`` keeps describing who authored the content.
        The semver is fixed; every check of a fresh upload runs again.
        """
        template = await self._template(template_id)
        version = (
            await self.session.execute(
                select(TemplateVersion).where(TemplateVersion.id == version_id).with_for_update()
            )
        ).scalar_one_or_none()
        if version is None or version.template_id != template_id:
            raise NotFoundError("Template version not found")
        if version.status != S.DRAFT:
            raise ConflictError(
                f"Only a DRAFT version can be reworked; this one is {version.status}"
            )
        if auth.user.id != version.created_by_admin_id:
            raise ForbiddenError("Only the admin who created this version can replace its files")
        if data.semver != version.semver:
            raise ValidationFailedError("The version number of an existing version cannot change")

        report = await self.validate_upload(template_id, data, replacing=version)
        errors = [i for i in report.issues if i.severity == "error"]
        if errors:
            raise ValidationFailedError(
                "; ".join(f"[{i.code}] {i.path}: {i.message}" for i in errors[:10])
            )
        validated = validate_blank_pdf(data.pdf_bytes)
        assert_valid_manifest(
            data.field_mapping_manifest,
            expected=self._identity(template, data.semver, validated),
            pdf=validated.form,
        )
        manifest_hash = _manifest_sha256(data.field_mapping_manifest)
        bundle_hash = template_bundle_digest_v2(
            template_id=str(template.id),
            slug=template.slug,
            jurisdiction=str(template.jurisdiction),
            category=str(template.category),
            official_form_code=template.official_form_code,
            semver=version.semver,
            published_version_id=str(version.id),
            blank_pdf_sha256=validated.sha256,
            manifest_sha256=manifest_hash,
            page_count=validated.page_count,
        )
        object_key = object_key_for(validated.sha256)
        get_storage().store_blank_pdf(object_key=object_key, data=data.pdf_bytes)

        version.source_pdf_download_url = data.source_pdf_download_url
        version.source_url_matched_domain = data.source_url_matched_domain
        version.source_downloaded_at = data.source_downloaded_at
        version.blank_pdf_object_key = object_key
        version.blank_pdf_sha256 = validated.sha256
        version.blank_pdf_size_bytes = validated.size_bytes
        version.page_count = validated.page_count
        version.field_mapping_manifest = data.field_mapping_manifest
        version.manifest_sha256 = manifest_hash
        version.bundle_sha256 = bundle_hash
        version.signature_format_version = CURRENT_SIGNATURE_FORMAT_VERSION
        self.session.add(
            TemplateApprovalLog(
                id=uuid.uuid4(),
                template_version_id=version.id,
                actor_admin_id=auth.user.id,
                from_status=S.DRAFT,
                to_status=S.DRAFT,
                recorded_bundle_sha256=bundle_hash,
                review_notes="Blank PDF and manifest replaced by the maker",
            )
        )
        await self.session.flush()
        await audit.record_event(
            self.session,
            event_type=TemplateEvent.VERSION_ARTIFACTS_REPLACED,
            resource_type="template_version",
            resource_id=str(version.id),
            request_id=self.ctx.request_id,
            actor_user_id=auth.user.id,
            actor_role=auth.role,
            ip_subnet_hash=self.ctx.ip_subnet_hash,
            metadata={
                "template_id": str(template.id),
                "version_id": str(version.id),
                "semver": version.semver,
            },
        )
        await self.session.commit()
        return version
