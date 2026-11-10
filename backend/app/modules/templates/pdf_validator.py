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
from app.modules.templates.manifest_contract import PdfFieldInfo, PdfFormInfo, PdfPageInfo

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
    #: Page geometry and AcroForm field shapes, used to validate the manifest's
    #: PDF mappings against the real document. Contains no field values.
    form: PdfFormInfo


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

    return ValidatedPdf(
        sha256=sha256_hex(data),
        size_bytes=size,
        page_count=page_count,
        form=_inspect_form(reader),
    )


_FF_RADIO = 1 << 15
_FF_PUSHBUTTON = 1 << 16
_FF_COMBO = 1 << 17


def _inspect_form(reader: PdfReader) -> PdfFormInfo:
    """Describe page geometry and fillable fields (names, kinds, option states)."""
    try:
        pages = tuple(
            PdfPageInfo(
                width=float(page.mediabox.width),
                height=float(page.mediabox.height),
                rotation=int(page.rotation or 0),
            )
            for page in reader.pages
        )
        raw_fields = reader.get_fields() or {}
        fields: dict[str, PdfFieldInfo] = {}
        for name, raw in raw_fields.items():
            field_type = raw.get("/FT")
            flags = int(raw.get("/Ff", 0) or 0)
            states = tuple(
                str(state).lstrip("/") for state in raw.get("/_States_", []) if state != "/Off"
            )
            if field_type == "/Btn" and raw.get("/Opt"):
                # Radio groups may export index states with the names in /Opt.
                states = (*states, *(str(option) for option in raw["/Opt"]))
            if field_type == "/Tx":
                kind = "text"
            elif field_type == "/Btn":
                if flags & _FF_PUSHBUTTON:
                    kind = "other"
                else:
                    kind = "radio" if flags & _FF_RADIO else "checkbox"
            elif field_type == "/Ch":
                kind = "dropdown" if flags & _FF_COMBO else "other"
                options = raw.get("/Opt", [])
                states = tuple(
                    str(option[0] if isinstance(option, list | tuple) else option)
                    for option in options
                )
            else:
                kind = "other"
            fields[str(name)] = PdfFieldInfo(kind=kind, states=states)
    except PdfValidationError:
        raise
    except Exception:
        raise PdfValidationError("PDF has an unreadable page or form structure") from None
    return PdfFormInfo(pages=pages, fields=fields)


def _reject_populated_acroform(reader: PdfReader) -> None:
    """Reject any AcroForm field (at any nesting depth) carrying a real value.

    An unchecked button's ``/V /Off`` is the blank state, not citizen data.
    """
    try:
        root = reader.trailer["/Root"]
        acroform = root.get("/AcroForm") if hasattr(root, "get") else None
    except Exception:
        return
    if not acroform:
        return
    try:
        acroform = acroform.get_object()
        pending = list(acroform.get("/Fields") or [])
        seen = 0
        while pending:
            seen += 1
            if seen > 20_000:
                raise PdfValidationError("PDF form structure is too large")
            field = pending.pop().get_object()
            value = field.get("/V")
            if value not in (None, "", b"", "/Off") and value != []:
                raise PdfValidationError("PDF contains a pre-filled AcroForm value")
            for kid in field.get("/Kids") or []:
                kid_object = kid.get_object()
                # Widget annotations have no /T; only descend into real sub-fields.
                if "/T" in kid_object or "/Kids" in kid_object:
                    pending.append(kid)
    except PdfValidationError:
        raise
    except Exception:
        raise PdfValidationError("PDF has an unreadable form structure") from None
