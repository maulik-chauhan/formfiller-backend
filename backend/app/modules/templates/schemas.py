"""Template & government-source schemas (admin write + public read).

Request models inherit ``StrictModel`` (``extra='forbid'``). Public responses
never expose draft/in-review artifacts or any citizen data (there is none).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import Field

from app.core.constants import (
    DomainPatternType,
    Jurisdiction,
    SourceCheckMethod,
    SourceCheckResult,
    SourceType,
    TemplateCategory,
    TemplateVersionStatus,
)
from app.core.schemas import ResponseModel, StrictModel

_HEX64 = r"^[a-f0-9]{64}$"
_HTTPS = r"^https://[a-z0-9.-]+(/.*)?$"
_SEMVER = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
_SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"


# --------------------------------------------------------------------------- #
# Approved domains
# --------------------------------------------------------------------------- #
class AdminCreateApprovedDomainPayload(StrictModel):
    domain_pattern: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9.-]+$")
    domain_type: DomainPatternType
    allow_subdomains: bool = True
    rationale: str = Field(min_length=1, max_length=200)


class ApprovedDomainResponse(ResponseModel):
    domain_pattern: str
    domain_type: DomainPatternType
    allow_subdomains: bool
    rationale: str
    is_active: bool
    created_at: dt.datetime


# --------------------------------------------------------------------------- #
# Government sources
# --------------------------------------------------------------------------- #
class AdminCreateGovSourcePayload(StrictModel):
    source_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z0-9_]+$")
    jurisdiction: Jurisdiction
    ministry_or_department: str = Field(min_length=2, max_length=160)
    official_portal_url: str = Field(pattern=_HTTPS, max_length=512)
    matched_domain_pattern: str = Field(min_length=3, max_length=120)
    source_type: SourceType
    allow_automated_checksum_check: bool = False


class GovernmentSourceResponse(ResponseModel):
    id: uuid.UUID
    source_code: str
    jurisdiction: Jurisdiction
    ministry_or_department: str
    official_portal_url: str
    matched_domain_pattern: str
    source_type: SourceType
    allow_automated_checksum_check: bool
    is_active: bool
    last_verified_at: dt.datetime | None
    created_at: dt.datetime


class GovernmentSourceListResponse(ResponseModel):
    sources: list[GovernmentSourceResponse]
    page: int
    page_size: int
    total: int


class AdminRecordSourceCheckPayload(StrictModel):
    checked_url: str = Field(pattern=_HTTPS, max_length=512)
    check_method: SourceCheckMethod
    check_result: SourceCheckResult
    http_status_code: int | None = Field(default=None, ge=100, le=599)
    etag_header: str | None = Field(default=None, max_length=128)
    last_modified_header: str | None = Field(default=None, max_length=128)
    observed_pdf_sha256: str | None = Field(default=None, pattern=_HEX64)
    template_version_id: uuid.UUID | None = None


class GovernmentSourceCheckResponse(ResponseModel):
    id: uuid.UUID
    government_source_id: uuid.UUID
    template_version_id: uuid.UUID | None
    checked_url: str
    check_method: SourceCheckMethod
    http_status_code: int | None
    observed_pdf_sha256: str | None
    check_result: SourceCheckResult
    checked_at: dt.datetime


# --------------------------------------------------------------------------- #
# Form templates
# --------------------------------------------------------------------------- #
class AdminCreateTemplatePayload(StrictModel):
    government_source_id: uuid.UUID
    slug: str = Field(min_length=2, max_length=80, pattern=_SLUG)
    jurisdiction: Jurisdiction
    official_form_code: str = Field(min_length=1, max_length=64)
    category: TemplateCategory
    localized_titles: dict[str, str] = Field(min_length=1)
    localized_descriptions: dict[str, str] = Field(min_length=1)


class AdminTemplateResponse(ResponseModel):
    id: uuid.UUID
    government_source_id: uuid.UUID
    slug: str
    jurisdiction: Jurisdiction
    official_form_code: str
    category: TemplateCategory
    localized_titles: dict[str, str]
    localized_descriptions: dict[str, str]
    current_published_version_id: uuid.UUID | None
    is_active: bool
    created_at: dt.datetime


# --------------------------------------------------------------------------- #
# Template versions
# --------------------------------------------------------------------------- #
class AdminTemplateVersionResponse(ResponseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    semver: str
    status: TemplateVersionStatus
    source_pdf_download_url: str
    source_url_matched_domain: str
    blank_pdf_sha256: str
    blank_pdf_size_bytes: int
    page_count: int
    manifest_sha256: str
    bundle_sha256: str
    signing_key_id: str | None
    publisher_ed25519_sig: str | None
    signature_format_version: int
    deprecation_reason: str | None
    replaced_by_template_slug: str | None
    created_by_admin_id: uuid.UUID
    verified_by_admin_id: uuid.UUID | None
    approved_by_admin_id: uuid.UUID | None
    published_at: dt.datetime | None
    created_at: dt.datetime


class AdminTemplateMetadataPayload(StrictModel):
    """Edit template metadata before publication (all fields optional).

    Titles and descriptions are editable until the template's first publication.
    Identity fields (form code, category, source) are bound into every version's
    signed digest and manifest, so they are editable only while no version exists.
    The slug and jurisdiction are permanent.
    """

    localized_titles: dict[str, str] | None = Field(default=None, min_length=1)
    localized_descriptions: dict[str, str] | None = Field(default=None, min_length=1)
    official_form_code: str | None = Field(default=None, min_length=1, max_length=64)
    category: TemplateCategory | None = None
    government_source_id: uuid.UUID | None = None


class AdminStateTransitionPayload(StrictModel):
    target_status: TemplateVersionStatus
    expected_bundle_sha256: str = Field(pattern=_HEX64)
    review_notes: str = Field(min_length=1, max_length=2000)
    # Withdrawal provenance; accepted only on PUBLISHED -> DEPRECATED.
    deprecation_reason: str | None = Field(default=None, min_length=1, max_length=200)
    replaced_by_template_slug: str | None = Field(default=None, max_length=80, pattern=_SLUG)


# --------------------------------------------------------------------------- #
# Public read models
# --------------------------------------------------------------------------- #
class PublishedTemplateSummary(ResponseModel):
    slug: str
    jurisdiction: Jurisdiction
    category: TemplateCategory
    official_form_code: str
    localized_titles: dict[str, str]
    published_semver: str
    published_version_id: uuid.UUID


class PublishedTemplateListResponse(ResponseModel):
    templates: list[PublishedTemplateSummary]
    page: int
    page_size: int
    total: int


class PublishedTemplateDetailResponse(ResponseModel):
    template_id: uuid.UUID
    slug: str
    jurisdiction: Jurisdiction
    category: TemplateCategory
    official_form_code: str
    localized_titles: dict[str, str]
    localized_descriptions: dict[str, str]
    government_source_code: str
    published_semver: str
    published_version_id: uuid.UUID
    blank_pdf_sha256: str
    page_count: int


class TemplateBundleManifestResponse(ResponseModel):
    """Everything a client needs to verify a published template independently.

    ``signature_verified`` is the *server* checking its own signature. It is not
    client-side proof: clients must recompute the digest and verify the Ed25519
    signature against a publisher key pinned in their own build.
    """

    template_id: uuid.UUID
    slug: str
    jurisdiction: Jurisdiction
    category: TemplateCategory
    official_form_code: str
    semver: str
    template_version_id: uuid.UUID
    blank_pdf_sha256: str
    blank_pdf_download_url: str
    blank_pdf_download_url_expires: bool
    page_count: int
    field_mapping_manifest: dict[str, Any]
    manifest_sha256: str
    bundle_sha256: str
    signature_format_version: int
    signature_alg: Literal["EdDSA"] = "EdDSA"
    publisher_ed25519_sig: str
    signing_key_id: str
    signature_verified: bool


TemplateLifecycleStatus = Literal[
    "PUBLISHED", "DEPRECATED", "NEVER_PUBLISHED", "UNKNOWN_VERSION", "UNKNOWN_TEMPLATE"
]


class TemplateVersionStatusResponse(ResponseModel):
    """Lifecycle of one ``slug@semver``. Always 200 — never 404-masks a withdrawal."""

    slug: str
    semver: str
    status: TemplateLifecycleStatus
    published_version_id: uuid.UUID | None = None
    deprecated_at: dt.datetime | None = None
    deprecation_reason: str | None = None
    replaced_by_template_slug: str | None = None


class PublisherKeyEntry(ResponseModel):
    kid: str
    alg: Literal["EdDSA"] = "EdDSA"
    crv: Literal["Ed25519"] = "Ed25519"
    public_key_b64: str
    status: Literal["active", "previous"]
    purpose: Literal["template-publisher"] = "template-publisher"
    current_signature_format_version: int


class PublisherKeySetResponse(ResponseModel):
    keys: list[PublisherKeyEntry]
    trust_note: str


# --------------------------------------------------------------------------- #
# Admin console read models (F5)
# --------------------------------------------------------------------------- #
AdminRole = Literal["TEMPLATE_CURATOR", "TEMPLATE_VERIFIER", "TEMPLATE_APPROVER", "SUPER_ADMIN"]


class AdminPrincipalResponse(ResponseModel):
    user_id: uuid.UUID
    role: AdminRole
    can_create: bool
    can_verify: bool
    can_approve: bool
    can_manage_domains: bool


class AdminDomainListResponse(ResponseModel):
    domains: list[ApprovedDomainResponse]


class AdminVersionSummary(ResponseModel):
    id: uuid.UUID
    semver: str
    status: TemplateVersionStatus
    page_count: int
    blank_pdf_sha256: str
    signature_format_version: int
    created_by_admin_id: uuid.UUID
    verified_by_admin_id: uuid.UUID | None
    approved_by_admin_id: uuid.UUID | None
    created_at: dt.datetime
    published_at: dt.datetime | None
    deprecated_at: dt.datetime | None


class AdminTemplateListItem(ResponseModel):
    id: uuid.UUID
    slug: str
    jurisdiction: Jurisdiction
    category: TemplateCategory
    official_form_code: str
    localized_titles: dict[str, str]
    is_active: bool
    version_count: int
    latest_version: AdminVersionSummary | None
    published_semver: str | None
    created_at: dt.datetime


class AdminTemplateListResponse(ResponseModel):
    templates: list[AdminTemplateListItem]
    page: int
    page_size: int
    total: int


class AdminTemplateDetailResponse(AdminTemplateResponse):
    government_source: GovernmentSourceResponse
    published_semver: str | None
    titles_editable: bool
    identity_editable: bool
    versions: list[AdminVersionSummary]


class AdminApprovalLogEntry(ResponseModel):
    id: uuid.UUID
    from_status: TemplateVersionStatus
    to_status: TemplateVersionStatus
    actor_admin_id: uuid.UUID
    actor_role: str | None
    review_notes: str
    recorded_bundle_sha256: str
    created_at: dt.datetime


class AdminAuditEntry(ResponseModel):
    event_type: str
    actor_user_id: uuid.UUID | None
    actor_role: str
    occurred_at: dt.datetime
    metadata: dict[str, Any]


class AdminAllowedAction(ResponseModel):
    target_status: TemplateVersionStatus
    allowed: bool
    reason: str | None = None


class AdminVersionDetailResponse(AdminTemplateVersionResponse):
    source_downloaded_at: dt.datetime
    verified_at: dt.datetime | None
    approved_at: dt.datetime | None
    deprecated_at: dt.datetime | None
    field_mapping_manifest: dict[str, Any]
    signature_verified: bool | None
    can_replace_artifacts: bool
    actions: list[AdminAllowedAction]
    approval_history: list[AdminApprovalLogEntry]
    audit_events: list[AdminAuditEntry]


class PdfPageInspection(ResponseModel):
    width: float
    height: float
    rotation: int


class PdfFieldInspection(ResponseModel):
    name: str
    kind: str
    options: list[str]


class PdfInspection(ResponseModel):
    sha256: str
    size_bytes: int
    page_count: int
    pages: list[PdfPageInspection]
    fields: list[PdfFieldInspection]


class ValidationIssue(ResponseModel):
    severity: Literal["error", "warning"]
    code: str
    path: str
    message: str


class VersionValidationReport(ResponseModel):
    """Dry-run result of a version upload. Nothing is stored."""

    valid: bool
    pdf: PdfInspection | None
    manifest_sha256: str | None
    expected_identity: dict[str, Any] | None
    issues: list[ValidationIssue]
