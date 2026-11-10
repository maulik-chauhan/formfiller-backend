"""F3 template signature format + withdrawal provenance.

Additive and reversible. Adds to ``template_versions``:

* ``signature_format_version`` (1 = legacy pipe-joined digest, 2 = domain-separated
  length-prefixed digest). Existing rows default to 1 and keep verifying through the
  legacy path; no published row is rewritten or re-signed.
* ``deprecation_reason`` / ``replaced_by_template_slug`` — withdrawal provenance
  recorded once on the PUBLISHED -> DEPRECATED transition.

The published-immutability trigger is extended to freeze the signature format and
to make withdrawal provenance write-once.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UPGRADE = """
ALTER TABLE template_versions
    ADD COLUMN signature_format_version SMALLINT NOT NULL DEFAULT 1,
    ADD COLUMN deprecation_reason VARCHAR(200),
    ADD COLUMN replaced_by_template_slug VARCHAR(80);

ALTER TABLE template_versions ADD CONSTRAINT chk_template_versions_sig_format
    CHECK (signature_format_version IN (1, 2));
ALTER TABLE template_versions ADD CONSTRAINT chk_template_versions_replaced_by_slug
    CHECK (replaced_by_template_slug IS NULL
           OR replaced_by_template_slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$');

CREATE OR REPLACE FUNCTION fn_prevent_published_template_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IN ('PUBLISHED', 'DEPRECATED') THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Cannot delete a PUBLISHED or DEPRECATED template version (id=%)', OLD.id;
        END IF;
        IF NEW.template_id != OLD.template_id
           OR NEW.semver != OLD.semver
           OR NEW.source_pdf_download_url != OLD.source_pdf_download_url
           OR NEW.source_url_matched_domain != OLD.source_url_matched_domain
           OR NEW.blank_pdf_object_key != OLD.blank_pdf_object_key
           OR NEW.blank_pdf_sha256 != OLD.blank_pdf_sha256
           OR NEW.blank_pdf_size_bytes != OLD.blank_pdf_size_bytes
           OR NEW.page_count != OLD.page_count
           OR NEW.field_mapping_manifest != OLD.field_mapping_manifest
           OR NEW.manifest_sha256 != OLD.manifest_sha256
           OR NEW.bundle_sha256 != OLD.bundle_sha256
           OR NEW.publisher_ed25519_sig IS DISTINCT FROM OLD.publisher_ed25519_sig
           OR NEW.signing_key_id IS DISTINCT FROM OLD.signing_key_id
           OR NEW.signature_format_version != OLD.signature_format_version
           OR NEW.created_by_admin_id != OLD.created_by_admin_id
           OR NEW.verified_by_admin_id != OLD.verified_by_admin_id
           OR NEW.approved_by_admin_id != OLD.approved_by_admin_id THEN
            RAISE EXCEPTION 'Published template version artifacts and provenance are immutable (id=%)', OLD.id;
        END IF;
        IF OLD.status = 'PUBLISHED' AND NEW.status NOT IN ('PUBLISHED', 'DEPRECATED') THEN
            RAISE EXCEPTION 'Published template version can only transition to DEPRECATED';
        END IF;
        IF OLD.status = 'DEPRECATED' AND NEW.status != 'DEPRECATED' THEN
            RAISE EXCEPTION 'Deprecated template version cannot be reactivated';
        END IF;
        -- Withdrawal provenance is written exactly once, on PUBLISHED -> DEPRECATED.
        IF NOT (OLD.status = 'PUBLISHED' AND NEW.status = 'DEPRECATED')
           AND (NEW.deprecation_reason IS DISTINCT FROM OLD.deprecation_reason
                OR NEW.replaced_by_template_slug IS DISTINCT FROM OLD.replaced_by_template_slug) THEN
            RAISE EXCEPTION 'Withdrawal provenance is immutable once recorded (id=%)', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_DOWNGRADE = """
CREATE OR REPLACE FUNCTION fn_prevent_published_template_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IN ('PUBLISHED', 'DEPRECATED') THEN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Cannot delete a PUBLISHED or DEPRECATED template version (id=%)', OLD.id;
        END IF;
        IF NEW.template_id != OLD.template_id
           OR NEW.semver != OLD.semver
           OR NEW.source_pdf_download_url != OLD.source_pdf_download_url
           OR NEW.source_url_matched_domain != OLD.source_url_matched_domain
           OR NEW.blank_pdf_object_key != OLD.blank_pdf_object_key
           OR NEW.blank_pdf_sha256 != OLD.blank_pdf_sha256
           OR NEW.blank_pdf_size_bytes != OLD.blank_pdf_size_bytes
           OR NEW.page_count != OLD.page_count
           OR NEW.field_mapping_manifest != OLD.field_mapping_manifest
           OR NEW.manifest_sha256 != OLD.manifest_sha256
           OR NEW.bundle_sha256 != OLD.bundle_sha256
           OR NEW.publisher_ed25519_sig IS DISTINCT FROM OLD.publisher_ed25519_sig
           OR NEW.signing_key_id IS DISTINCT FROM OLD.signing_key_id
           OR NEW.created_by_admin_id != OLD.created_by_admin_id
           OR NEW.verified_by_admin_id != OLD.verified_by_admin_id
           OR NEW.approved_by_admin_id != OLD.approved_by_admin_id THEN
            RAISE EXCEPTION 'Published template version artifacts and provenance are immutable (id=%)', OLD.id;
        END IF;
        IF OLD.status = 'PUBLISHED' AND NEW.status NOT IN ('PUBLISHED', 'DEPRECATED') THEN
            RAISE EXCEPTION 'Published template version can only transition to DEPRECATED';
        END IF;
        IF OLD.status = 'DEPRECATED' AND NEW.status != 'DEPRECATED' THEN
            RAISE EXCEPTION 'Deprecated template version cannot be reactivated';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

ALTER TABLE template_versions DROP CONSTRAINT chk_template_versions_replaced_by_slug;
ALTER TABLE template_versions DROP CONSTRAINT chk_template_versions_sig_format;
ALTER TABLE template_versions
    DROP COLUMN replaced_by_template_slug,
    DROP COLUMN deprecation_reason,
    DROP COLUMN signature_format_version;
"""


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
