"""Aggregates every ORM model so ``Base.metadata`` is fully populated.

Import this module (not the individual model modules) wherever all 19 tables
must be registered — Alembic ``env.py`` and the test fixtures.
"""

from __future__ import annotations

from app.db.base import Base
from app.modules.audit.models import AuditLog
from app.modules.auth.models import (
    AuthSession,
    DeletedAccountQuota,
    OtpChallenge,
    User,
)
from app.modules.entitlements.models import (
    EntitlementLease,
    EntitlementLedgerEntry,
    EntitlementWallet,
)
from app.modules.esign_gateway.models import EsignHashSession
from app.modules.payments.models import (
    PaymentOrder,
    PaymentRefund,
    PaymentWebhookEvent,
    PricingCatalog,
)
from app.modules.templates.models import (
    ApprovedSourceDomain,
    FormTemplate,
    GovernmentSource,
    GovernmentSourceCheck,
    TemplateApprovalLog,
    TemplateVersion,
)

__all__ = [
    "ApprovedSourceDomain",
    "AuditLog",
    "AuthSession",
    "Base",
    "DeletedAccountQuota",
    "EntitlementLease",
    "EntitlementLedgerEntry",
    "EntitlementWallet",
    "EsignHashSession",
    "FormTemplate",
    "GovernmentSource",
    "GovernmentSourceCheck",
    "OtpChallenge",
    "PaymentOrder",
    "PaymentRefund",
    "PaymentWebhookEvent",
    "PricingCatalog",
    "TemplateApprovalLog",
    "TemplateVersion",
    "User",
]

#: The 19 canonical table names, in spec order.
CANONICAL_TABLES: tuple[str, ...] = (
    "users",
    "otp_challenges",
    "auth_sessions",
    "deleted_account_quotas",
    "approved_source_domains",
    "government_sources",
    "government_source_checks",
    "form_templates",
    "template_versions",
    "template_approval_logs",
    "pricing_catalog",
    "payment_orders",
    "payment_refunds",
    "payment_webhook_events",
    "entitlement_wallets",
    "entitlement_leases",
    "entitlement_ledger_entries",
    "esign_hash_sessions",
    "audit_logs",
)
