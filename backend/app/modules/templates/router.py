"""Template routes: public read (``/templates/*``) and admin (``/admin/*``)."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, File, Form, Path, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import Jurisdiction, TemplateCategory, TemplateVersionStatus, UserRole
from app.core.dependencies import (
    AuthContext,
    RequestContext,
    get_request_context,
    require_authenticated_user,
    require_roles,
)
from app.core.errors import ValidationFailedError
from app.core.signing import CURRENT_SIGNATURE_FORMAT_VERSION, publisher_key_set
from app.db.session import get_session
from app.modules.templates import schemas
from app.modules.templates.admin_service import (
    ADMIN_ROLES,
    MAKER_ROLES,
    TemplateConsoleService,
    TemplateListFilters,
)
from app.modules.templates.service import (
    TemplateAdminService,
    TemplateReadService,
    VersionUploadInput,
)
from app.modules.templates.storage import get_storage

router = APIRouter(prefix="/templates", tags=["templates"])
admin_router = APIRouter(prefix="/admin", tags=["admin-templates"])


def _admin_service(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
) -> TemplateAdminService:
    return TemplateAdminService(session, ctx=ctx)


def _read_service(session: AsyncSession = Depends(get_session)) -> TemplateReadService:
    return TemplateReadService(session)


def _console_service(
    session: AsyncSession = Depends(get_session),
    ctx: RequestContext = Depends(get_request_context),
) -> TemplateConsoleService:
    return TemplateConsoleService(session, ctx=ctx)


#: Any template-administration role. Citizens are refused (403) on every /admin route.
_any_admin = require_roles(*ADMIN_ROLES)
_maker = require_roles(*MAKER_ROLES)


def _upload_input(
    *,
    semver: str,
    source_pdf_download_url: str,
    source_url_matched_domain: str,
    source_downloaded_at: str,
    government_source_id: uuid.UUID,
    manifest: object,
    pdf_bytes: bytes,
) -> VersionUploadInput:
    import datetime as dt

    try:
        downloaded_at = dt.datetime.fromisoformat(source_downloaded_at)
    except ValueError as exc:
        raise ValidationFailedError("source_downloaded_at must be ISO-8601") from exc
    return VersionUploadInput(
        semver=semver,
        source_pdf_download_url=source_pdf_download_url,
        source_url_matched_domain=source_url_matched_domain,
        source_downloaded_at=downloaded_at,
        field_mapping_manifest=manifest,  # type: ignore[arg-type]
        government_source_id=government_source_id,
        pdf_bytes=pdf_bytes,
    )


def _parse_manifest(manifest_json: str) -> dict[str, object]:
    try:
        manifest = json.loads(manifest_json)
    except ValueError as exc:
        raise ValidationFailedError("manifest_json must be valid JSON") from exc
    if not isinstance(manifest, dict):
        raise ValidationFailedError("manifest_json must be a JSON object")
    return manifest


# --------------------------------------------------------------------------- #
# Public / citizen read endpoints (PUBLISHED only)
# --------------------------------------------------------------------------- #
@router.get("/sources", response_model=schemas.GovernmentSourceListResponse)
async def list_sources(
    jurisdiction: Jurisdiction | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: TemplateReadService = Depends(_read_service),
) -> schemas.GovernmentSourceListResponse:
    rows, total = await service.list_sources(
        jurisdiction=jurisdiction.value if jurisdiction else None, page=page, page_size=page_size
    )
    return schemas.GovernmentSourceListResponse(
        sources=[schemas.GovernmentSourceResponse.model_validate(r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


_TRUST_NOTE = (
    "Distribution/rotation only. Pin a trusted publisher key in the client build; "
    "do not treat a key fetched here as a trust anchor."
)


@router.get("/publisher-keys", response_model=schemas.PublisherKeySetResponse)
async def list_publisher_keys() -> schemas.PublisherKeySetResponse:
    """Template-publisher verification keys (distinct from the ELT key set)."""
    return schemas.PublisherKeySetResponse(
        keys=[
            schemas.PublisherKeyEntry(
                kid=key.kid,
                public_key_b64=key.public_key_b64,
                status="active" if key.status == "active" else "previous",
                current_signature_format_version=CURRENT_SIGNATURE_FORMAT_VERSION,
            )
            for key in publisher_key_set()
        ],
        trust_note=_TRUST_NOTE,
    )


@router.get("", response_model=schemas.PublishedTemplateListResponse)
async def list_templates(
    jurisdiction: Jurisdiction | None = Query(default=None),
    category: TemplateCategory | None = Query(default=None),
    search_code: str | None = Query(default=None, max_length=64),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: TemplateReadService = Depends(_read_service),
) -> schemas.PublishedTemplateListResponse:
    rows, total = await service.list_templates(
        jurisdiction=jurisdiction.value if jurisdiction else None,
        category=category.value if category else None,
        search_code=search_code,
        page=page,
        page_size=page_size,
    )
    summaries = [
        schemas.PublishedTemplateSummary(
            slug=t.slug,
            jurisdiction=Jurisdiction(t.jurisdiction),
            category=TemplateCategory(t.category),
            official_form_code=t.official_form_code,
            localized_titles=t.localized_titles,
            published_semver=v.semver,
            published_version_id=v.id,
        )
        for t, v in rows
    ]
    return schemas.PublishedTemplateListResponse(
        templates=summaries, page=page, page_size=page_size, total=total
    )


@router.get("/{slug}", response_model=schemas.PublishedTemplateDetailResponse)
async def get_template(
    slug: str,
    service: TemplateReadService = Depends(_read_service),
) -> schemas.PublishedTemplateDetailResponse:
    template, version, source = await service.get_published_template(slug)
    return schemas.PublishedTemplateDetailResponse(
        template_id=template.id,
        slug=template.slug,
        jurisdiction=Jurisdiction(template.jurisdiction),
        category=TemplateCategory(template.category),
        official_form_code=template.official_form_code,
        localized_titles=template.localized_titles,
        localized_descriptions=template.localized_descriptions,
        government_source_code=source.source_code,
        published_semver=version.semver,
        published_version_id=version.id,
        blank_pdf_sha256=version.blank_pdf_sha256,
        page_count=version.page_count,
    )


@router.get(
    "/{slug}/versions/{semver}/status",
    response_model=schemas.TemplateVersionStatusResponse,
)
async def get_template_version_status(
    slug: str = Path(max_length=80),
    semver: str = Path(max_length=20),
    service: TemplateReadService = Depends(_read_service),
) -> schemas.TemplateVersionStatusResponse:
    return await service.get_version_status(slug, semver)


@router.get(
    "/{slug}/versions/{semver}/bundle",
    response_model=schemas.TemplateBundleManifestResponse,
)
async def get_template_bundle(
    slug: str,
    semver: str,
    _auth: AuthContext = Depends(require_authenticated_user),
    service: TemplateReadService = Depends(_read_service),
) -> schemas.TemplateBundleManifestResponse:
    template, version, verified = await service.get_published_bundle(slug, semver)
    storage = get_storage()
    return schemas.TemplateBundleManifestResponse(
        template_id=template.id,
        slug=template.slug,
        jurisdiction=Jurisdiction(template.jurisdiction),
        category=TemplateCategory(template.category),
        official_form_code=template.official_form_code,
        semver=version.semver,
        template_version_id=version.id,
        blank_pdf_sha256=version.blank_pdf_sha256,
        blank_pdf_download_url=storage.build_download_url(version.blank_pdf_object_key),
        blank_pdf_download_url_expires=storage.supports_expiring_urls,
        page_count=version.page_count,
        field_mapping_manifest=version.field_mapping_manifest,
        manifest_sha256=version.manifest_sha256,
        bundle_sha256=version.bundle_sha256,
        signature_format_version=version.signature_format_version,
        publisher_ed25519_sig=version.publisher_ed25519_sig or "",
        signing_key_id=version.signing_key_id or "",
        signature_verified=verified,
    )


# --------------------------------------------------------------------------- #
# Admin endpoints
# --------------------------------------------------------------------------- #
@admin_router.post(
    "/domains",
    response_model=schemas.ApprovedDomainResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_domain(
    payload: schemas.AdminCreateApprovedDomainPayload,
    auth: AuthContext = Depends(require_roles(UserRole.SUPER_ADMIN)),
    service: TemplateAdminService = Depends(_admin_service),
) -> schemas.ApprovedDomainResponse:
    domain = await service.create_domain(auth, payload)
    return schemas.ApprovedDomainResponse.model_validate(domain)


@admin_router.post(
    "/sources",
    response_model=schemas.GovernmentSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_source(
    payload: schemas.AdminCreateGovSourcePayload,
    auth: AuthContext = Depends(require_roles(UserRole.TEMPLATE_CURATOR, UserRole.SUPER_ADMIN)),
    service: TemplateAdminService = Depends(_admin_service),
) -> schemas.GovernmentSourceResponse:
    source = await service.create_source(auth, payload)
    return schemas.GovernmentSourceResponse.model_validate(source)


@admin_router.post(
    "/sources/{source_id}/checks",
    response_model=schemas.GovernmentSourceCheckResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_source_check(
    source_id: uuid.UUID,
    payload: schemas.AdminRecordSourceCheckPayload,
    auth: AuthContext = Depends(require_roles(UserRole.TEMPLATE_CURATOR, UserRole.SUPER_ADMIN)),
    service: TemplateAdminService = Depends(_admin_service),
) -> schemas.GovernmentSourceCheckResponse:
    check = await service.record_source_check(auth, source_id, payload)
    return schemas.GovernmentSourceCheckResponse.model_validate(check)


@admin_router.post(
    "/templates",
    response_model=schemas.AdminTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    payload: schemas.AdminCreateTemplatePayload,
    auth: AuthContext = Depends(require_roles(UserRole.TEMPLATE_CURATOR, UserRole.SUPER_ADMIN)),
    service: TemplateAdminService = Depends(_admin_service),
) -> schemas.AdminTemplateResponse:
    template = await service.create_template(auth, payload)
    return schemas.AdminTemplateResponse.model_validate(template)


@admin_router.post(
    "/templates/{template_id}/versions",
    response_model=schemas.AdminTemplateVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_template_version(
    template_id: uuid.UUID,
    blank_pdf: UploadFile = File(...),
    semver: str = Form(...),
    source_pdf_download_url: str = Form(...),
    source_url_matched_domain: str = Form(...),
    source_downloaded_at: str = Form(...),
    government_source_id: uuid.UUID = Form(...),
    manifest_json: str = Form(...),
    auth: AuthContext = Depends(require_roles(UserRole.TEMPLATE_CURATOR, UserRole.SUPER_ADMIN)),
    service: TemplateAdminService = Depends(_admin_service),
) -> schemas.AdminTemplateVersionResponse:
    import datetime as dt

    try:
        manifest = json.loads(manifest_json)
        if not isinstance(manifest, dict):
            raise ValueError
    except ValueError as exc:
        raise ValidationFailedError("manifest_json must be a JSON object") from exc
    try:
        downloaded_at = dt.datetime.fromisoformat(source_downloaded_at)
    except ValueError as exc:
        raise ValidationFailedError("source_downloaded_at must be ISO-8601") from exc

    pdf_bytes = await blank_pdf.read()
    version = await service.create_version(
        auth,
        template_id,
        VersionUploadInput(
            semver=semver,
            source_pdf_download_url=source_pdf_download_url,
            source_url_matched_domain=source_url_matched_domain,
            source_downloaded_at=downloaded_at,
            field_mapping_manifest=manifest,
            government_source_id=government_source_id,
            pdf_bytes=pdf_bytes,
        ),
    )
    return schemas.AdminTemplateVersionResponse.model_validate(version)


@admin_router.post(
    "/templates/{template_id}/versions/{version_id}/transitions",
    response_model=schemas.AdminTemplateVersionResponse,
)
async def transition_version(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    payload: schemas.AdminStateTransitionPayload,
    auth: AuthContext = Depends(
        require_roles(
            UserRole.TEMPLATE_CURATOR,
            UserRole.TEMPLATE_VERIFIER,
            UserRole.TEMPLATE_APPROVER,
            UserRole.SUPER_ADMIN,
        )
    ),
    service: TemplateAdminService = Depends(_admin_service),
) -> schemas.AdminTemplateVersionResponse:
    version = await service.transition_version(auth, template_id, version_id, payload)
    return schemas.AdminTemplateVersionResponse.model_validate(version)


# --------------------------------------------------------------------------- #
# Admin console (F5): read models, dry-run validation, draft rework
# --------------------------------------------------------------------------- #
@admin_router.get("/me", response_model=schemas.AdminPrincipalResponse)
async def admin_me(auth: AuthContext = Depends(_any_admin)) -> schemas.AdminPrincipalResponse:
    """The caller's admin role and what it allows. 403 for non-admin accounts."""
    return TemplateConsoleService.principal(auth)


@admin_router.get("/domains", response_model=schemas.AdminDomainListResponse)
async def list_domains(
    _auth: AuthContext = Depends(_any_admin),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.AdminDomainListResponse:
    rows = await service.list_domains()
    return schemas.AdminDomainListResponse(
        domains=[schemas.ApprovedDomainResponse.model_validate(r) for r in rows]
    )


@admin_router.get("/templates", response_model=schemas.AdminTemplateListResponse)
async def admin_list_templates(
    q: str | None = Query(default=None, max_length=80),
    status_filter: TemplateVersionStatus | None = Query(default=None, alias="status"),
    jurisdiction: Jurisdiction | None = Query(default=None),
    category: TemplateCategory | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _auth: AuthContext = Depends(_any_admin),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.AdminTemplateListResponse:
    """All templates in every lifecycle state (the public catalogue shows PUBLISHED only)."""
    items, total = await service.list_templates(
        TemplateListFilters(
            query=q or None,
            status=status_filter,
            jurisdiction=jurisdiction.value if jurisdiction else None,
            category=category.value if category else None,
            page=page,
            page_size=page_size,
        )
    )
    return schemas.AdminTemplateListResponse(
        templates=items, page=page, page_size=page_size, total=total
    )


@admin_router.get("/templates/{template_id}", response_model=schemas.AdminTemplateDetailResponse)
async def admin_get_template(
    template_id: uuid.UUID,
    _auth: AuthContext = Depends(_any_admin),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.AdminTemplateDetailResponse:
    return await service.get_template(template_id)


@admin_router.post(
    "/templates/{template_id}/metadata", response_model=schemas.AdminTemplateResponse
)
async def admin_update_metadata(
    template_id: uuid.UUID,
    payload: schemas.AdminTemplateMetadataPayload,
    auth: AuthContext = Depends(_maker),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.AdminTemplateResponse:
    template = await service.update_metadata(auth, template_id, payload)
    return schemas.AdminTemplateResponse.model_validate(template)


@admin_router.post(
    "/templates/{template_id}/versions/validate",
    response_model=schemas.VersionValidationReport,
)
async def admin_validate_version(
    template_id: uuid.UUID,
    blank_pdf: UploadFile = File(...),
    semver: str = Form(...),
    source_pdf_download_url: str = Form(...),
    source_url_matched_domain: str = Form(...),
    source_downloaded_at: str = Form(...),
    government_source_id: uuid.UUID = Form(...),
    manifest_json: str = Form(...),
    replace_version_id: uuid.UUID | None = Form(default=None),
    _auth: AuthContext = Depends(_maker),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.VersionValidationReport:
    """Run every upload check and report all findings. Nothing is stored.

    Pass ``replace_version_id`` to check a rework of an existing DRAFT version: its
    own version number is then not reported as a duplicate.
    """
    json_error: str | None = None
    manifest: object = {}
    try:
        manifest = _parse_manifest(manifest_json)
    except ValidationFailedError as exc:
        json_error = exc.detail
    data = _upload_input(
        semver=semver,
        source_pdf_download_url=source_pdf_download_url,
        source_url_matched_domain=source_url_matched_domain,
        source_downloaded_at=source_downloaded_at,
        government_source_id=government_source_id,
        manifest=manifest,
        pdf_bytes=await blank_pdf.read(),
    )
    return await service.validate_upload(
        template_id,
        data,
        manifest_json_error=json_error,
        replacing=(
            await service.version_for_rework(template_id, replace_version_id)
            if replace_version_id
            else None
        ),
    )


@admin_router.get(
    "/templates/{template_id}/versions/{version_id}",
    response_model=schemas.AdminVersionDetailResponse,
)
async def admin_get_version(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    auth: AuthContext = Depends(_any_admin),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.AdminVersionDetailResponse:
    return await service.get_version(auth, template_id, version_id)


@admin_router.get(
    "/templates/{template_id}/versions/{version_id}/blank-pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def admin_get_blank_pdf(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    _auth: AuthContext = Depends(_any_admin),
    service: TemplateConsoleService = Depends(_console_service),
) -> Response:
    """The version's blank government PDF for reviewer preview (any lifecycle state)."""
    data = await service.read_blank_pdf(template_id, version_id)
    return Response(
        content=data,
        media_type="application/pdf",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@admin_router.post(
    "/templates/{template_id}/versions/{version_id}/artifacts",
    response_model=schemas.AdminTemplateVersionResponse,
)
async def admin_replace_artifacts(
    template_id: uuid.UUID,
    version_id: uuid.UUID,
    blank_pdf: UploadFile = File(...),
    semver: str = Form(...),
    source_pdf_download_url: str = Form(...),
    source_url_matched_domain: str = Form(...),
    source_downloaded_at: str = Form(...),
    government_source_id: uuid.UUID = Form(...),
    manifest_json: str = Form(...),
    auth: AuthContext = Depends(_maker),
    service: TemplateConsoleService = Depends(_console_service),
) -> schemas.AdminTemplateVersionResponse:
    """Replace a DRAFT version's PDF and manifest (its maker only), e.g. after rejection."""
    data = _upload_input(
        semver=semver,
        source_pdf_download_url=source_pdf_download_url,
        source_url_matched_domain=source_url_matched_domain,
        source_downloaded_at=source_downloaded_at,
        government_source_id=government_source_id,
        manifest=_parse_manifest(manifest_json),
        pdf_bytes=await blank_pdf.read(),
    )
    version = await service.replace_artifacts(auth, template_id, version_id, data)
    return schemas.AdminTemplateVersionResponse.model_validate(version)
