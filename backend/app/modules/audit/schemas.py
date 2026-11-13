"""Allowlisted audit metadata.

Audit rows must never carry PII or secrets. Rather than blacklisting sensitive
keys, we allowlist the small, explicit set of non-sensitive diagnostic keys that
may be stored in ``audit_logs.metadata_jsonb``. Anything else is dropped.
"""

from __future__ import annotations

from typing import Any

#: The ONLY keys permitted in audit metadata. No mobile/email/OTP/token keys.
ALLOWED_AUDIT_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "session_id",
        "previous_session_id",
        "token_family_id",
        "platform",
        "purpose",
        "channel",
        "reason",
        "outcome",
        "revoked_session_count",
        "attempt_count",
        "is_new_user",
        "quota_restored",
        "lease_id",
        "order_id",
        "refund_id",
        # Template / source administration (M4) — identifiers and states only.
        "domain_pattern",
        "source_id",
        "source_code",
        "template_id",
        "version_id",
        "slug",
        "semver",
        "from_status",
        "to_status",
        "target_status",
        "check_result",
        # Admin console (F5) — field names and identifiers only, never values.
        "changed_fields",
        "superseded_by_version_id",
        "role",
        # Entitlements (M5) — identifiers, buckets and tiers only (no PII).
        "template_version_id",
        "credit_bucket",
        "granted_tier",
        "requested_tier",
        "upgrade_sku",
        "lease_status",
        "commit_count",
        "entry_type",
        # Payments (M6) — identifiers, SKUs and non-sensitive amounts only.
        "sku_code",
        "quantity",
        "gateway_event_id",
        "event_type",
        "payment_status",
        "refund_status",
        "reconciliation_action",
        "amount_paise",
        "total_amount_paise",
        "refund_amount_paise",
    }
)

#: Scalar JSON types allowed as metadata values.
_ALLOWED_VALUE_TYPES = (str, int, float, bool)


def sanitize_audit_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return only allowlisted, scalar, non-null metadata entries."""
    return {
        key: value
        for key, value in metadata.items()
        if key in ALLOWED_AUDIT_METADATA_KEYS
        and value is not None
        and isinstance(value, _ALLOWED_VALUE_TYPES)
    }
