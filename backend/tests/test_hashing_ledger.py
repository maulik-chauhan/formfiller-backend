"""Hashing primitives and ledger hash-chain determinism."""

from __future__ import annotations

from app.core.hashing import (
    email_blind_index,
    ip_subnet_hash,
    mobile_blind_index,
    otp_code_hmac,
    sha256_hex,
)
from app.core.ledger import GENESIS_HASH, compute_entry_hash

HEX64 = 64


def test_blind_indexes_are_hex64_and_deterministic() -> None:
    a = mobile_blind_index("+919812345678")
    b = mobile_blind_index("+919812345678")
    assert a == b
    assert len(a) == HEX64
    assert a != mobile_blind_index("+919800000000")


def test_email_blind_index_case_insensitive() -> None:
    assert email_blind_index("User@Example.com") == email_blind_index("user@example.com")


def test_ip_subnet_hash_groups_by_subnet() -> None:
    # Same /24 → same hash; different /24 → different hash.
    assert ip_subnet_hash("203.0.113.5") == ip_subnet_hash("203.0.113.200")
    assert ip_subnet_hash("203.0.113.5") != ip_subnet_hash("203.0.114.5")


def test_otp_hmac_bound_to_challenge() -> None:
    assert otp_code_hmac("chal-1", "123456") != otp_code_hmac("chal-2", "123456")


def test_sha256_hex() -> None:
    assert len(sha256_hex(b"hello")) == HEX64


def test_ledger_chain_changes_with_prev_hash() -> None:
    common = {
        "user_id": "u1",
        "sequence_no": 1,
        "entry_type": "LEASE_RESERVE",
        "credit_bucket": "FREE_QUOTA",
        "delta_available": -1,
        "delta_reserved": 1,
        "available_balance_after": 1,
        "reserved_balance_after": 1,
        "idempotency_key": "idem-1",
    }
    h1 = compute_entry_hash(prev_entry_hash=GENESIS_HASH, **common)
    h2 = compute_entry_hash(prev_entry_hash=h1, **common)
    assert len(h1) == HEX64
    assert h1 != h2  # same fields, different predecessor → different hash
