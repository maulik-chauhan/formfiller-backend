"""M5 entitlement enum alignment to authoritative spec v2.1.0.

Rationale (schema-defect reconciliation):
The Milestone-2 ``0001`` migration was generated from an earlier spec variant and
used provisional enum literals (``FREE_UPGRADED_SIGNATURE``, ``STANDARD_BASE``,
``SIGNATURE_BUNDLE``, …) and the column ``is_upgraded_with_sig``. The authoritative
``architecture_review_and_final_backend_spec(1).md`` (v2.1.0) mandates
``FREE_UPGRADED_SIG_50`` / ``PAID_UPGRADED_SIG_50`` tiers, the
``FREE_LIFETIME`` / ``STANDARD_20`` / ``SIGNATURE_50`` / ``SIGNATURE_UPGRADE_30`` /
``ESIGN`` credit buckets, and the column ``is_upgraded_with_sig_30`` (§2.1, §3,
§8.2, DDL). The old CHECK constraints would reject the authoritative values, so
M5 cannot be implemented without this alignment.

This migration is the SMALLEST change that unblocks M5: it renames one column and
rewrites five CHECK constraints. The 19-table inventory is unchanged; no table is
added, dropped, or restructured.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UPGRADE = """
ALTER TABLE entitlement_leases RENAME COLUMN is_upgraded_with_sig TO is_upgraded_with_sig_30;

ALTER TABLE entitlement_leases DROP CONSTRAINT chk_leases_granted_tier;
ALTER TABLE entitlement_leases ADD CONSTRAINT chk_leases_granted_tier CHECK (
    granted_tier IN (
        'FREE_STANDARD', 'PAID_STANDARD_20', 'PAID_SIGNATURE_50',
        'FREE_UPGRADED_SIG_50', 'PAID_UPGRADED_SIG_50', 'PAID_ESIGN'
    )
);

ALTER TABLE entitlement_leases DROP CONSTRAINT chk_leases_base_bucket;
ALTER TABLE entitlement_leases ADD CONSTRAINT chk_leases_base_bucket CHECK (
    base_credit_bucket IN ('FREE_LIFETIME', 'STANDARD_20', 'SIGNATURE_50', 'ESIGN')
);

ALTER TABLE entitlement_leases DROP CONSTRAINT chk_leases_upgrade_consistency;
ALTER TABLE entitlement_leases ADD CONSTRAINT chk_leases_upgrade_consistency CHECK (
    (is_upgraded_with_sig_30 = FALSE AND granted_tier IN ('FREE_STANDARD', 'PAID_STANDARD_20', 'PAID_SIGNATURE_50', 'PAID_ESIGN'))
    OR (is_upgraded_with_sig_30 = TRUE AND granted_tier IN ('FREE_UPGRADED_SIG_50', 'PAID_UPGRADED_SIG_50'))
);

ALTER TABLE entitlement_ledger_entries DROP CONSTRAINT chk_ledger_credit_bucket;
ALTER TABLE entitlement_ledger_entries ADD CONSTRAINT chk_ledger_credit_bucket CHECK (
    credit_bucket IN ('FREE_LIFETIME', 'STANDARD_20', 'SIGNATURE_50', 'SIGNATURE_UPGRADE_30', 'ESIGN')
);

ALTER TABLE pricing_catalog DROP CONSTRAINT chk_pricing_credit_bucket;
ALTER TABLE pricing_catalog ADD CONSTRAINT chk_pricing_credit_bucket CHECK (
    credit_bucket IN ('STANDARD_20', 'SIGNATURE_50', 'SIGNATURE_UPGRADE_30', 'ESIGN')
);
"""

_DOWNGRADE = """
ALTER TABLE pricing_catalog DROP CONSTRAINT chk_pricing_credit_bucket;
ALTER TABLE pricing_catalog ADD CONSTRAINT chk_pricing_credit_bucket CHECK (
    credit_bucket IN ('STANDARD_BASE', 'SIGNATURE_BUNDLE', 'SIGNATURE_UPGRADE', 'ESIGN_ADDON')
);

ALTER TABLE entitlement_ledger_entries DROP CONSTRAINT chk_ledger_credit_bucket;
ALTER TABLE entitlement_ledger_entries ADD CONSTRAINT chk_ledger_credit_bucket CHECK (
    credit_bucket IN ('FREE_QUOTA', 'STANDARD_BASE', 'SIGNATURE_BUNDLE', 'SIGNATURE_UPGRADE', 'ESIGN_ADDON')
);

ALTER TABLE entitlement_leases DROP CONSTRAINT chk_leases_upgrade_consistency;
ALTER TABLE entitlement_leases ADD CONSTRAINT chk_leases_upgrade_consistency CHECK (
    (is_upgraded_with_sig_30 = FALSE AND granted_tier IN ('FREE_STANDARD', 'PAID_STANDARD', 'PAID_SIGNATURE', 'PAID_ESIGN'))
    OR (is_upgraded_with_sig_30 = TRUE AND granted_tier IN ('FREE_UPGRADED_SIGNATURE', 'PAID_UPGRADED_SIGNATURE'))
);

ALTER TABLE entitlement_leases DROP CONSTRAINT chk_leases_base_bucket;
ALTER TABLE entitlement_leases ADD CONSTRAINT chk_leases_base_bucket CHECK (
    base_credit_bucket IN ('FREE_QUOTA', 'STANDARD_BASE', 'SIGNATURE_BUNDLE', 'ESIGN_ADDON')
);

ALTER TABLE entitlement_leases DROP CONSTRAINT chk_leases_granted_tier;
ALTER TABLE entitlement_leases ADD CONSTRAINT chk_leases_granted_tier CHECK (
    granted_tier IN (
        'FREE_STANDARD', 'PAID_STANDARD', 'PAID_SIGNATURE',
        'FREE_UPGRADED_SIGNATURE', 'PAID_UPGRADED_SIGNATURE', 'PAID_ESIGN'
    )
);

ALTER TABLE entitlement_leases RENAME COLUMN is_upgraded_with_sig_30 TO is_upgraded_with_sig;
"""


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
