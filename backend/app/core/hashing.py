"""Keyed hashing primitives: blind indexes, OTP HMAC, IP-subnet hashing.

No plaintext PII is ever persisted. Mobile numbers and emails are stored only as
keyed HMAC-SHA256 "blind indexes" so equality lookups work without reversibility.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets

from app.config import get_settings

#: Indian mobile in E.164: +91 followed by a 6-9 leading digit and 9 digits.
INDIAN_MOBILE_E164 = re.compile(r"^\+91[6-9]\d{9}$")


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


def verify_otp_hmac(challenge_id: str, code: str, stored_hmac: str) -> bool:
    """Constant-time comparison of a submitted OTP against the stored HMAC."""
    return hmac.compare_digest(otp_code_hmac(challenge_id, code), stored_hmac)


def generate_otp_code() -> str:
    """Return a cryptographically secure 6-digit OTP (``secrets``), zero-padded."""
    return f"{secrets.randbelow(1_000_000):06d}"


def normalize_indian_mobile(raw: str) -> str | None:
    """Normalise a mobile number to ``+91XXXXXXXXXX`` E.164, or ``None`` if invalid.

    Accepts inputs with spaces/hyphens, an optional ``+91``/``91``/``0`` prefix,
    then validates the final form against the ``+91[6-9]\\d{9}`` contract.
    """
    digits = re.sub(r"[\s\-()]", "", raw)
    if digits.startswith("+91"):
        candidate = digits
    elif digits.startswith("91") and len(digits) == 12:
        candidate = f"+{digits}"
    elif digits.startswith("0") and len(digits) == 11:
        candidate = f"+91{digits[1:]}"
    elif len(digits) == 10:
        candidate = f"+91{digits}"
    else:
        candidate = digits
    return candidate if INDIAN_MOBILE_E164.match(candidate) else None


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
