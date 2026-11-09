"""Admin-only blank-PDF security validator.

Blank government form PDFs are the only binary admitted by the platform, and
only through the authenticated admin upload route. This validator enforces that
an uploaded file is a structurally valid *blank* PDF with no active content and
no pre-filled form values, so that neither executable payloads nor citizen
personal data can ever enter the system.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader

from app.config import get_settings
from app.core.constants import MAX_BLANK_PDF_BYTES, MAX_TEMPLATE_PAGE_COUNT
from app.core.errors import AppError
from app.core.hashing import sha256_hex

#: Magic prefix mandated by the spec (PDF 1.x).
_PDF_MAGIC = b"%PDF-1."

#: Dangerous tokens that must never appear in a blank form PDF.
_FORBIDDEN_TOKENS: tuple[bytes, ...] = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFiles",
    b"/EmbeddedFile",
    b"/OpenAction",
    b"/AA",
)


class PdfValidationError(AppError):
    status_code = 422
    code = "PDF_VALIDATION_FAILED"
    title = "Uploaded PDF failed security validation"


@dataclass(frozen=True)
class ValidatedPdf:
    sha256: str
    size_bytes: int
    page_count: int


def validate_blank_pdf(data: bytes) -> ValidatedPdf:
    """Validate ``data`` as a safe blank PDF or raise :class:`PdfValidationError`."""
    size = len(data)
    if size == 0:
        raise PdfValidationError("Empty upload")
    if size > min(MAX_BLANK_PDF_BYTES, get_settings().admin_upload_max_body_bytes):
        raise PdfValidationError("PDF exceeds the maximum allowed size")
    if not data.startswith(_PDF_MAGIC):
        raise PdfValidationError("Missing %PDF-1. magic bytes")

    # Raw-byte scan for active-content / embedded-file markers.
    for token in _FORBIDDEN_TOKENS:
        if token in data:
            raise PdfValidationError("PDF contains forbidden active-content markers")

    # Structural parse — reject anything pypdf cannot read.
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        page_count = len(reader.pages)
    except Exception as exc:
        raise PdfValidationError("Malformed or unreadable PDF") from exc

    if page_count < 1 or page_count > MAX_TEMPLATE_PAGE_COUNT:
        raise PdfValidationError("PDF page count outside the permitted range")

    _reject_populated_acroform(reader)

    return ValidatedPdf(sha256=sha256_hex(data), size_bytes=size, page_count=page_count)


def _reject_populated_acroform(reader: PdfReader) -> None:
    """Reject any AcroForm field carrying a non-empty ``/V`` value."""
    try:
        root = reader.trailer["/Root"]
        acroform = root.get("/AcroForm") if hasattr(root, "get") else None
    except Exception:
        return
    if not acroform:
        return
    try:
        acroform = acroform.get_object()
        fields = acroform.get("/Fields")
        if not fields:
            return
        for field_ref in fields:
            field = field_ref.get_object()
            value = field.get("/V")
            if value not in (None, "", b""):
                raise PdfValidationError("PDF contains a pre-filled AcroForm value")
    except PdfValidationError:
        raise
    except Exception:
        raise PdfValidationError("PDF has an unreadable form structure") from None
