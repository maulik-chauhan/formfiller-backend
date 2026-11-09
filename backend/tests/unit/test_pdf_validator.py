"""Unit tests for the blank-PDF security validator."""

from __future__ import annotations

import pytest

from app.modules.templates.pdf_validator import PdfValidationError, validate_blank_pdf
from tests.pdf_fixtures import make_blank_pdf, make_pdf_with_token, make_prefilled_acroform_pdf


def test_valid_blank_pdf_accepted() -> None:
    result = validate_blank_pdf(make_blank_pdf(2))
    assert result.page_count == 2
    assert len(result.sha256) == 64
    assert result.size_bytes > 0


def test_missing_magic_rejected() -> None:
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(b"not a pdf at all")


def test_empty_rejected() -> None:
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(b"")


@pytest.mark.parametrize(
    "token",
    [b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFiles", b"/OpenAction"],
)
def test_active_content_tokens_rejected(token: bytes) -> None:
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(make_pdf_with_token(token))


def test_malformed_pdf_rejected() -> None:
    # Correct magic prefix but garbage structure.
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(b"%PDF-1.7\nthis is not a real pdf body" + b"\x00" * 50)


def test_prefilled_acroform_rejected() -> None:
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(make_prefilled_acroform_pdf())


def test_oversize_rejected() -> None:
    oversized = b"%PDF-1.7" + b"0" * (10_485_761)
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(oversized)


def test_too_many_pages_rejected() -> None:
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(make_blank_pdf(51))
