"""Canonical technical enumerations (``app/core/constants.py``).

Per v2.1.0 Section 2: this module defines ONLY stable technical enums and
structural constants. Specific commercial SKU prices (e.g. 20/50/30) and quota
counts (e.g. 2 lifetime free forms) are driven by ``pricing_catalog`` rows and
environment-validated ``Settings`` in ``app/config.py`` — never hardcoded here.

Every value is a plain string (``StrEnum``) so a single source of truth is
shared across PostgreSQL CHECK constraints, SQLAlchemy columns, Pydantic v2
schemas, and JWT claims.
"""

from __future__ import annotations

from enum import StrEnum

# ---------------------------------------------------------------------------
# 2.1 Entitlement, Billing & Lifecycle Enumerations
# ---------------------------------------------------------------------------


class CreditBucket(StrEnum):
    """Logical wallet balance bucket (no rupee prices embedded)."""

    FREE_QUOTA = "FREE_QUOTA"
    STANDARD_BASE = "STANDARD_BASE"
    SIGNATURE_BUNDLE = "SIGNATURE_BUNDLE"
    SIGNATURE_UPGRADE = "SIGNATURE_UPGRADE"
    ESIGN_ADDON = "ESIGN_ADDON"


class RequestedLeaseTier(StrEnum):
    """Accepted values in ``CreateLeasePayload.requested_tier``."""

    FREE_STANDARD = "FREE_STANDARD"
    PAID_STANDARD = "PAID_STANDARD"
    PAID_SIGNATURE = "PAID_SIGNATURE"
    PAID_ESIGN = "PAID_ESIGN"  # disabled until M7


class GrantedEntitlementTier(StrEnum):
    """Stored in ``entitlement_leases.granted_tier`` and the lease JWT ``tier``."""

    FREE_STANDARD = "FREE_STANDARD"
    PAID_STANDARD = "PAID_STANDARD"
    PAID_SIGNATURE = "PAID_SIGNATURE"
    FREE_UPGRADED_SIGNATURE = "FREE_UPGRADED_SIGNATURE"
    PAID_UPGRADED_SIGNATURE = "PAID_UPGRADED_SIGNATURE"
    PAID_ESIGN = "PAID_ESIGN"  # disabled until M7


class LeaseStatus(StrEnum):
    """Stored in ``entitlement_leases.lease_status``."""

    ISSUED = "ISSUED"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED_RELEASED = "EXPIRED_RELEASED"
    REVOKED_REFUND = "REVOKED_REFUND"


class LedgerEntryType(StrEnum):
    """Stored in ``entitlement_ledger_entries.entry_type``."""

    FREE_GRANT = "FREE_GRANT"
    FREE_RESTORE_DEDUCT = "FREE_RESTORE_DEDUCT"
    LEASE_RESERVE = "LEASE_RESERVE"
    LEASE_COMMIT = "LEASE_COMMIT"
    LEASE_LATE_RECONCILE_COMMIT = "LEASE_LATE_RECONCILE_COMMIT"
    LEASE_RELEASE = "LEASE_RELEASE"
    LEASE_EXPIRED_RELEASE = "LEASE_EXPIRED_RELEASE"
    LEASE_UPGRADE_RESERVE = "LEASE_UPGRADE_RESERVE"
    LEASE_UPGRADE_COMMIT = "LEASE_UPGRADE_COMMIT"
    PAID_CREDIT = "PAID_CREDIT"
    REFUND_DEBIT_AVAILABLE = "REFUND_DEBIT_AVAILABLE"
    REFUND_REVOKE_RESERVED = "REFUND_REVOKE_RESERVED"
    REFUND_POST_CONSUMPTION = "REFUND_POST_CONSUMPTION"
    REFUND_PARTIAL_SURCHARGE_ONLY = "REFUND_PARTIAL_SURCHARGE_ONLY"


class PaymentOrderStatus(StrEnum):
    """Stored in ``payment_orders.payment_status``."""

    CREATED = "CREATED"
    AUTHORIZED = "AUTHORIZED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"
    REFUNDED_POST_CONSUMPTION = "REFUNDED_POST_CONSUMPTION"


class RefundRecordStatus(StrEnum):
    """Stored in ``payment_refunds.refund_status``."""

    INITIATED = "INITIATED"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class RefundReconciliationAction(StrEnum):
    """Stored in ``payment_refunds.wallet_reconciliation_action``."""

    NONE = "NONE"
    PARTIAL_SURCHARGE_ADJUSTMENT = "PARTIAL_SURCHARGE_ADJUSTMENT"
    DEBITED_AVAILABLE_CREDIT = "DEBITED_AVAILABLE_CREDIT"
    REVOKED_ISSUED_LEASE = "REVOKED_ISSUED_LEASE"
    FLAGGED_POST_CONSUMPTION = "FLAGGED_POST_CONSUMPTION"


# ---------------------------------------------------------------------------
# 2.2 Auth, Template & Governance Enumerations
# ---------------------------------------------------------------------------


class UserRole(StrEnum):
    """Stored in ``users.role`` and the Access JWT ``role`` claim."""

    CITIZEN = "CITIZEN"
    TEMPLATE_CURATOR = "TEMPLATE_CURATOR"
    TEMPLATE_VERIFIER = "TEMPLATE_VERIFIER"
    TEMPLATE_APPROVER = "TEMPLATE_APPROVER"
    SUPER_ADMIN = "SUPER_ADMIN"


class AccountStatus(StrEnum):
    """Stored in ``users.account_status``."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class OtpChannel(StrEnum):
    """Stored in ``otp_challenges.channel``."""

    MOBILE_SMS = "MOBILE_SMS"
    EMAIL = "EMAIL"


class OtpPurpose(StrEnum):
    """Stored in ``otp_challenges.purpose``."""

    LOGIN_OR_REGISTER = "LOGIN_OR_REGISTER"
    LINK_EMAIL = "LINK_EMAIL"
    ACCOUNT_DELETE = "ACCOUNT_DELETE"


class OtpChallengeStatus(StrEnum):
    """Stored in ``otp_challenges.status``."""

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    EXPIRED = "EXPIRED"
    LOCKED = "LOCKED"


class ClientPlatform(StrEnum):
    """Stored in ``auth_sessions.platform``."""

    WEB = "WEB"
    ANDROID = "ANDROID"
    IOS = "IOS"


class SessionRevocationReason(StrEnum):
    """Stored in ``auth_sessions.revocation_reason``."""

    USER_LOGOUT = "USER_LOGOUT"
    TOKEN_ROTATED = "TOKEN_ROTATED"
    TOKEN_REUSE_DETECTED = "TOKEN_REUSE_DETECTED"
    ADMIN_REVOKE = "ADMIN_REVOKE"
    ACCOUNT_DELETED = "ACCOUNT_DELETED"


class Jurisdiction(StrEnum):
    """Stored in ``government_sources.jurisdiction`` and ``form_templates.jurisdiction``."""

    CENTRAL_INDIA = "CENTRAL_INDIA"
    KARNATAKA_STATE = "KARNATAKA_STATE"


class SourceType(StrEnum):
    """Stored in ``government_sources.source_type``."""

    OFFICIAL_GOV_PORTAL = "OFFICIAL_GOV_PORTAL"
    STATUTORY_BODY = "STATUTORY_BODY"


class DomainPatternType(StrEnum):
    """Stored in ``approved_source_domains.domain_type``."""

    GOV_TLD_SUFFIX = "GOV_TLD_SUFFIX"
    STATUTORY_EXACT_HOST = "STATUTORY_EXACT_HOST"


class SourceCheckMethod(StrEnum):
    """Stored in ``government_source_checks.check_method``."""

    ADMIN_MANUAL_UPLOAD = "ADMIN_MANUAL_UPLOAD"
    ADMIN_ASSISTED_HEAD = "ADMIN_ASSISTED_HEAD"
    SCHEDULED_STATIC_PDF_CHECK = "SCHEDULED_STATIC_PDF_CHECK"


class SourceCheckResult(StrEnum):
    """Stored in ``government_source_checks.check_result``."""

    INITIAL_INGEST = "INITIAL_INGEST"
    UNCHANGED = "UNCHANGED"
    CHECKSUM_CHANGED = "CHECKSUM_CHANGED"
    HTTP_ERROR = "HTTP_ERROR"


class TemplateCategory(StrEnum):
    """Stored in ``form_templates.category``."""

    REVENUE = "REVENUE"
    IDENTITY = "IDENTITY"
    PENSION = "PENSION"
    TRANSPORT = "TRANSPORT"
    WELFARE = "WELFARE"
    EMPLOYMENT = "EMPLOYMENT"
    RTI = "RTI"


class TemplateVersionStatus(StrEnum):
    """Stored in ``template_versions.status`` and approval-log status columns."""

    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"


class EsignSessionStatus(StrEnum):
    """Stored in ``esign_hash_sessions.status`` (Milestone 7, gated off)."""

    INITIATED = "INITIATED"
    ESP_REDIRECTED = "ESP_REDIRECTED"
    SIGNED = "SIGNED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class WebhookProcessingOutcome(StrEnum):
    """Stored in ``payment_webhook_events.processing_outcome``."""

    PROCESSED = "PROCESSED"
    IGNORED_DUPLICATE = "IGNORED_DUPLICATE"
    IGNORED_UNTRACKED_EVENT = "IGNORED_UNTRACKED_EVENT"


# ---------------------------------------------------------------------------
# Structural constants (non-commercial, stable)
# ---------------------------------------------------------------------------

#: 64-char lowercase hex (SHA-256 / HMAC-SHA256 digests, blind indexes).
HEX_SHA256_PATTERN = r"^[a-f0-9]{64}$"

#: Semantic-version string pattern (no leading zeroes).
SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"

#: Lowercase hyphenated slug.
SLUG_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"

#: Structural PDF upper bound shared with the admin upload policy.
MAX_BLANK_PDF_BYTES = 10_485_760
MAX_TEMPLATE_PAGE_COUNT = 50

#: Roles permitted to upload admin template versions.
ADMIN_TEMPLATE_UPLOAD_ROLES = frozenset({UserRole.TEMPLATE_CURATOR, UserRole.SUPER_ADMIN})
