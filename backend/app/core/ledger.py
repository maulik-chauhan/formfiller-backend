"""Tamper-evident per-user ledger hash-chain helper.

Each ``entitlement_ledger_entries`` row carries ``prev_entry_hash`` and
``entry_hash``. ``entry_hash`` is computed deterministically from the canonical
field tuple plus the previous entry's hash, forming an append-only chain that
makes silent mutation detectable.
"""

from __future__ import annotations

import hashlib

#: Genesis hash for the first entry in a user's chain (64 zeroes).
GENESIS_HASH = "0" * 64


def compute_entry_hash(
    *,
    user_id: str,
    sequence_no: int,
    entry_type: str,
    credit_bucket: str,
    delta_available: int,
    delta_reserved: int,
    available_balance_after: int,
    reserved_balance_after: int,
    idempotency_key: str,
    prev_entry_hash: str,
) -> str:
    """Return the SHA-256 hex digest binding this entry to the prior one."""
    canonical = "|".join(
        [
            user_id,
            str(sequence_no),
            entry_type,
            credit_bucket,
            str(delta_available),
            str(delta_reserved),
            str(available_balance_after),
            str(reserved_balance_after),
            idempotency_key,
            prev_entry_hash,
        ]
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
