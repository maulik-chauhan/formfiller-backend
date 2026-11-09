"""Initial 19-table schema, constraints, indexes, functions & triggers.

Executes the canonical DDL in ``0001_initial_schema.sql`` verbatim so the
PostgreSQL CHECK constraints, plpgsql validation functions, and append-only /
immutability triggers from spec Section 5.3 are created exactly as specified.

Revision ID: 0001
Revises:
Create Date: 2026-11-09
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL_PATH = Path(__file__).with_suffix(".sql")

#: Objects to tear down on downgrade, in dependency-safe order.
_DROP_ORDER: tuple[str, ...] = (
    "esign_hash_sessions",
    "entitlement_ledger_entries",
    "payment_webhook_events",
    "payment_refunds",
    "entitlement_leases",
    "entitlement_wallets",
    "payment_orders",
    "pricing_catalog",
    "template_approval_logs",
    "government_source_checks",
    "template_versions",
    "form_templates",
    "government_sources",
    "approved_source_domains",
    "deleted_account_quotas",
    "auth_sessions",
    "otp_challenges",
    "audit_logs",
    "users",
)

_DROP_FUNCTIONS: tuple[str, ...] = (
    "fn_enforce_append_only_table()",
    "fn_prevent_published_template_mutation()",
    "fn_trg_validate_template_version_source_url()",
    "fn_trg_validate_gov_source_url()",
    "fn_validate_url_against_approved_domains(VARCHAR, VARCHAR)",
)


def upgrade() -> None:
    sql = _DDL_PATH.read_text(encoding="utf-8")
    op.execute(sql)


def downgrade() -> None:
    for table in _DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for fn in _DROP_FUNCTIONS:
        op.execute(f"DROP FUNCTION IF EXISTS {fn} CASCADE")
