"""Government sources & versioned templates (Tables 5-10)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import (
    CHAR,
    TIMESTAMP,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.constants import TemplateVersionStatus
from app.db.base import Base


class ApprovedSourceDomain(Base):
    __tablename__ = "approved_source_domains"

    domain_pattern: Mapped[str] = mapped_column(String(120), primary_key=True)
    domain_type: Mapped[str] = mapped_column(String(32), nullable=False)
    allow_subdomains: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("TRUE")
    )
    rationale: Mapped[str] = mapped_column(String(200), nullable=False)
    approved_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class GovernmentSource(Base):
    __tablename__ = "government_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False)
    ministry_or_department: Mapped[str] = mapped_column(String(160), nullable=False)
    official_portal_url: Mapped[str] = mapped_column(String(512), nullable=False)
    matched_domain_pattern: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("approved_source_domains.domain_pattern"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    allow_automated_checksum_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class FormTemplate(Base):
    __tablename__ = "form_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    government_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("government_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    jurisdiction: Mapped[str] = mapped_column(String(32), nullable=False)
    official_form_code: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    localized_titles: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    localized_descriptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # FK added post-hoc in migration to break the circular dependency with
    # template_versions (see Alembic 0001).
    current_published_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TemplateVersion(Base):
    __tablename__ = "template_versions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("form_templates.id", ondelete="RESTRICT"), nullable=False
    )
    semver: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{TemplateVersionStatus.DRAFT}'")
    )
    source_pdf_download_url: Mapped[str] = mapped_column(String(512), nullable=False)
    source_url_matched_domain: Mapped[str] = mapped_column(
        String(120),
        ForeignKey("approved_source_domains.domain_pattern"),
        nullable=False,
    )
    source_downloaded_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False
    )
    blank_pdf_object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    blank_pdf_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    blank_pdf_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    page_count: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    field_mapping_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    bundle_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    publisher_ed25519_sig: Mapped[str | None] = mapped_column(String(256))
    signing_key_id: Mapped[str | None] = mapped_column(String(32))
    created_by_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    verified_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    approved_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    verified_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    approved_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    published_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    deprecated_at: Mapped[dt.datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class GovernmentSourceCheck(Base):
    __tablename__ = "government_source_checks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    government_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("government_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("template_versions.id", ondelete="SET NULL")
    )
    checked_url: Mapped[str] = mapped_column(String(512), nullable=False)
    check_method: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status_code: Mapped[int | None] = mapped_column(SmallInteger)
    etag_header: Mapped[str | None] = mapped_column(String(128))
    last_modified_header: Mapped[str | None] = mapped_column(String(128))
    observed_pdf_sha256: Mapped[str | None] = mapped_column(CHAR(64))
    check_result: Mapped[str] = mapped_column(String(24), nullable=False)
    checked_by_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    checked_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_gov_source_checks_source_time",
            "government_source_id",
            text("checked_at DESC"),
        ),
    )


class TemplateApprovalLog(Base):
    __tablename__ = "template_approval_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    from_status: Mapped[str] = mapped_column(String(20), nullable=False)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    recorded_bundle_sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    review_notes: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index(
            "idx_template_approval_logs_version",
            "template_version_id",
            text("created_at ASC"),
        ),
    )
