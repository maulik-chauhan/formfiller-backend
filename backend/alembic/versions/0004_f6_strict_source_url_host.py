"""F6 hardening: strict host parsing for approved-domain URL validation.

``fn_validate_url_against_approved_domains`` extracted the host with
``^https://([^/:]+)``, which stops only at ``/`` or ``:``. Characters such as ``#``,
``?``, ``@`` and ``\\`` were therefore read as part of the "host", so with
``allow_subdomains`` on, ``https://evil.example#.transport.karnataka.gov.in`` matched
``transport.karnataka.gov.in`` although the real host is ``evil.example``.

The host must now be a plain DNS name (``[A-Za-z0-9.-]+``) followed by ``/`` or the end
of the URL: user-info, ports, queries or fragments straight after the host are refused.
This matches the API's own ``https://`` pattern for portal and checked URLs.

No approved domain, source or version row is changed. The upgrade refuses to run if any
existing source or version URL would stop validating under the stricter rule, because the
validation triggers also fire on UPDATE and would otherwise block later status changes.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _function(host_regex: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION fn_validate_url_against_approved_domains(
    p_url VARCHAR,
    p_domain_pattern VARCHAR
) RETURNS BOOLEAN AS $$
DECLARE
    v_host TEXT;
    v_domain RECORD;
BEGIN
    v_host := LOWER(SUBSTRING(p_url FROM '{host_regex}'));
    IF v_host IS NULL OR v_host = '' THEN
        RETURN FALSE;
    END IF;

    SELECT domain_pattern, allow_subdomains, is_active
    INTO v_domain
    FROM approved_source_domains
    WHERE domain_pattern = p_domain_pattern;

    IF NOT FOUND OR NOT v_domain.is_active THEN
        RETURN FALSE;
    END IF;

    IF v_host = v_domain.domain_pattern THEN
        RETURN TRUE;
    END IF;

    IF v_domain.allow_subdomains AND v_host LIKE ('%.' || v_domain.domain_pattern) THEN
        RETURN TRUE;
    END IF;

    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;
"""


_STRICT = r"^https://([A-Za-z0-9.-]+)(/|$)"
_LEGACY = r"^https://([^/:]+)"

_GUARD = """
DO $$
DECLARE
    v_bad INTEGER;
BEGIN
    SELECT
        (SELECT COUNT(*) FROM government_sources s
           JOIN approved_source_domains d ON d.domain_pattern = s.matched_domain_pattern
          WHERE d.is_active
            AND NOT fn_validate_url_against_approved_domains(s.official_portal_url,
                                                             s.matched_domain_pattern))
      + (SELECT COUNT(*) FROM template_versions v
           JOIN approved_source_domains d ON d.domain_pattern = v.source_url_matched_domain
          WHERE d.is_active
            AND NOT fn_validate_url_against_approved_domains(v.source_pdf_download_url,
                                                             v.source_url_matched_domain))
    INTO v_bad;
    IF v_bad > 0 THEN
        RAISE EXCEPTION
            '% existing source/version URL(s) fail strict host validation; review them first',
            v_bad;
    END IF;
END;
$$;
"""


def upgrade() -> None:
    op.execute(_function(_STRICT))
    op.execute(_GUARD)


def downgrade() -> None:
    op.execute(_function(_LEGACY))
