"""Unit tests for OTP generation, HMAC verification and mobile normalization."""

from __future__ import annotations

from app.core.hashing import (
    generate_otp_code,
    normalize_indian_mobile,
    otp_code_hmac,
    verify_otp_hmac,
)


def test_generated_otp_is_six_digits() -> None:
    for _ in range(200):
        code = generate_otp_code()
        assert len(code) == 6
        assert code.isdigit()


def test_otp_hmac_is_constant_time_correct() -> None:
    challenge_id = "11111111-1111-1111-1111-111111111111"
    stored = otp_code_hmac(challenge_id, "123456")
    assert verify_otp_hmac(challenge_id, "123456", stored) is True
    assert verify_otp_hmac(challenge_id, "654321", stored) is False
    # Same code, different challenge → different HMAC (binding).
    assert verify_otp_hmac("22222222-2222-2222-2222-222222222222", "123456", stored) is False


def test_mobile_normalization_valid_forms() -> None:
    assert normalize_indian_mobile("+919812345678") == "+919812345678"
    assert normalize_indian_mobile("9812345678") == "+919812345678"
    assert normalize_indian_mobile("919812345678") == "+919812345678"
    assert normalize_indian_mobile("09812345678") == "+919812345678"
    assert normalize_indian_mobile("+91 98123-45678") == "+919812345678"


def test_mobile_normalization_rejects_invalid() -> None:
    # Leading digit outside 6-9.
    assert normalize_indian_mobile("+915812345678") is None
    # Wrong length.
    assert normalize_indian_mobile("+9198123") is None
    # Non-Indian country code.
    assert normalize_indian_mobile("+15551234567") is None
    assert normalize_indian_mobile("notaphone") is None
