"""Keyed hashing primitives: blind indexes, OTP HMAC, IP-subnet hashing.

No plaintext PII is ever persisted. Mobile numbers and emails are stored only as
keyed HMAC-SHA256 "blind indexes" so equality lookups work without reversibility.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress

from app.config import get_settings


def _hmac_hex(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def mobile_blind_index(e164_mobile: str) -> str:
    """Keyed blind index for an E.164 mobile number (e.g. ``+919812345678``)."""
    settings = get_settings()
    return _hmac_hex(settings.blind_index_hmac_key, f"mobile:{e164_mobile.strip()}")


def email_blind_index(email: str) -> str:
    """Keyed blind index for a lowercased email address."""
    settings = get_settings()
    return _hmac_hex(settings.blind_index_hmac_key, f"email:{email.strip().lower()}")


def otp_code_hmac(challenge_id: str, code: str) -> str:
    """HMAC of the OTP code bound to its challenge id (defeats rainbow tables)."""
    settings = get_settings()
    return _hmac_hex(settings.otp_hmac_key, f"{challenge_id}:{code}")


def sha256_hex(data: bytes) -> str:
    """Plain SHA-256 hex digest (refresh tokens, payloads, PDF checksums)."""
    return hashlib.sha256(data).hexdigest()


def ip_subnet_hash(ip: str) -> str:
    """Hash the /24 (IPv4) or /48 (IPv6) subnet of ``ip`` — never the full address.

    Storing only the coarse subnet hash supports rate-limiting and abuse analysis
    while minimising personal-data retention.
    """
    settings = get_settings()
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return _hmac_hex(settings.ip_subnet_hash_key, "subnet:unknown")

    if isinstance(addr, ipaddress.IPv4Address):
        network = ipaddress.ip_network(f"{ip}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{ip}/48", strict=False)
    return _hmac_hex(settings.ip_subnet_hash_key, f"subnet:{network.network_address}")
