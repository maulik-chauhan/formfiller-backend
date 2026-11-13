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
from typing import Any

from pypdf import PdfReader
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NullObject,
    NumberObject,
)

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
    b"/AA",
)

#: ``/OpenAction`` is allowed only as a view destination in the document catalog
#: (DEC-9): official government PDFs often open "at page 1, fit width". An action
#: dictionary (JavaScript, URI, Launch, Named, GoToR, ...) is always rejected.
_OPEN_ACTION = b"/OpenAction"

#: The same markers, checked on the *parsed* objects. The raw-byte scan above cannot
#: see inside compressed object streams (PDF 1.5+), so every object the file defines
#: is also walked after parsing: these names may appear neither as a dictionary key
#: nor as a name value (e.g. ``/S /JavaScript`` or ``/Type /EmbeddedFile``).
_FORBIDDEN_NAMES: frozenset[str] = frozenset(token.decode("ascii") for token in _FORBIDDEN_TOKENS)
#: Upper bounds that keep the structural walk cheap on hostile input.
_MAX_PDF_OBJECTS = 200_000
_MAX_NESTED_ITEMS = 2_000_000
_DESTINATION_FITS: dict[str, int] = {
    # destination type -> number of trailing parameters (PDF 32000-1, 12.3.2.2)
    "/XYZ": 3,
    "/Fit": 0,
    "/FitH": 1,
    "/FitV": 1,
    "/FitR": 4,
    "/FitB": 0,
    "/FitBH": 1,
    "/FitBV": 1,
}


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
    open_action_tokens = data.count(_OPEN_ACTION)

    # Structural parse — reject anything pypdf cannot read.
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        page_count = len(reader.pages)
    except Exception as exc:
        raise PdfValidationError("Malformed or unreadable PDF") from exc

    if page_count < 1 or page_count > MAX_TEMPLATE_PAGE_COUNT:
        raise PdfValidationError("PDF page count outside the permitted range")

    _reject_forbidden_objects(reader)
    _check_open_action(reader, open_action_tokens)
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


def _object_ids(reader: PdfReader) -> list[tuple[int, int]]:
    """Every (object number, generation) the file defines, compressed ones included."""
    ids: set[tuple[int, int]] = set()
    for generation, entries in reader.xref.items():
        ids.update((idnum, generation) for idnum in entries)
    ids.update((idnum, 0) for idnum in reader.xref_objStm)
    return sorted(ids)


def _reject_forbidden_objects(reader: PdfReader) -> None:
    """Walk every defined object (including ones inside object streams) for markers.

    ``/OpenAction`` is allowed only as the document catalog's own entry; its value is
    checked by :func:`_check_open_action`.
    """
    rejected = PdfValidationError("PDF contains forbidden active-content markers")
    try:
        root_ref = reader.trailer.raw_get("/Root")
        catalog_id = root_ref.idnum if isinstance(root_ref, IndirectObject) else None
        ids = _object_ids(reader)
        if len(ids) > _MAX_PDF_OBJECTS:
            raise PdfValidationError("PDF has too many objects")
        budget = _MAX_NESTED_ITEMS
        for idnum, generation in ids:
            obj = reader.get_object(IndirectObject(idnum, generation, reader))
            stack: list[Any] = [(obj, idnum == catalog_id)]
            while stack:
                budget -= 1
                if budget < 0:
                    raise PdfValidationError("PDF structure is too large")
                item, is_catalog = stack.pop()
                if isinstance(item, DictionaryObject):  # stream dictionaries included
                    for key in item:
                        if str(key) in _FORBIDDEN_NAMES:
                            raise rejected
                        if key == "/OpenAction" and not is_catalog:
                            raise rejected
                        value = item.raw_get(key)
                        if not isinstance(value, IndirectObject):
                            stack.append((value, False))
                elif isinstance(item, ArrayObject):
                    stack.extend((value, False) for value in item)
                elif isinstance(item, NameObject) and str(item) in _FORBIDDEN_NAMES:
                    raise rejected
    except PdfValidationError:
        raise
    except Exception:
        raise PdfValidationError("PDF has an unreadable object structure") from None


def _check_open_action(reader: PdfReader, raw_token_count: int) -> None:
    """Allow ``/OpenAction`` only as a view destination to a page of this document.

    The catalog is inspected structurally, so an action hidden in a compressed object
    stream is caught too. A raw ``/OpenAction`` anywhere other than the catalog's
    single entry is rejected.
    """
    rejected = PdfValidationError("PDF contains forbidden active-content markers")
    try:
        root = reader.trailer["/Root"].get_object()
        if not isinstance(root, DictionaryObject):
            raise rejected
        raw_value = root.get("/OpenAction")
        if raw_value is None:
            if raw_token_count:
                raise rejected
            return
        if raw_token_count > 1:
            raise rejected
        value = raw_value.get_object()
        if not isinstance(value, ArrayObject) or len(value) < 2:
            raise rejected
        target, fit = value[0], value[1]
        page_ids = {
            page.indirect_reference.idnum
            for page in reader.pages
            if page.indirect_reference is not None
        }
        if not isinstance(target, IndirectObject) or target.idnum not in page_ids:
            raise rejected
        if not isinstance(fit, NameObject) or str(fit) not in _DESTINATION_FITS:
            raise rejected
        params = list(value[2:])
        if len(params) != _DESTINATION_FITS[str(fit)]:
            raise rejected
        for param in params:
            if not isinstance(param, NumberObject | FloatObject | NullObject):
                raise rejected
    except PdfValidationError:
        raise
    except Exception:
        raise rejected from None


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
