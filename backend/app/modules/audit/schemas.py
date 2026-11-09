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
