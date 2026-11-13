"""Helpers that build in-memory PDFs for validator tests (not collected)."""

from __future__ import annotations

import io
import zlib
from collections.abc import Callable

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    PdfObject,
    TextStringObject,
)


def make_blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_prefilled_acroform_pdf() -> bytes:
    """A structurally valid PDF whose AcroForm field carries a populated /V."""
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    field = DictionaryObject(
        {
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject("applicant_name"),
            NameObject("/V"): TextStringObject("PREFILLED-CITIZEN-DATA"),
        }
    )
    ref = writer._add_object(field)
    writer._root_object[NameObject("/AcroForm")] = DictionaryObject(
        {NameObject("/Fields"): ArrayObject([ref])}
    )
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_pdf_with_token(token: bytes) -> bytes:
    """A blank PDF with a forbidden active-content token spliced in."""
    base = make_blank_pdf(1)
    # Insert the token after the header so the raw scan finds it.
    return base[:20] + token + base[20:]


def make_pdf_with_open_action(
    build_value: Callable[[PdfWriter], PdfObject], *, pages: int = 1
) -> bytes:
    """A blank PDF whose catalog ``/OpenAction`` is ``build_value(writer)``.

    ``build_value`` receives the writer (so it can reference the pages it created)
    and returns the PDF object to store as the catalog's ``/OpenAction``.
    """
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    value = build_value(writer)
    writer._root_object[NameObject("/OpenAction")] = value
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_pdf_with_object_stream(
    catalog_extra: bytes = b"", extra_objects: tuple[bytes, ...] = ()
) -> bytes:
    """A 1-page PDF 1.5 whose objects all live in a Flate-compressed object stream.

    ``catalog_extra`` is spliced into the catalog dictionary and ``extra_objects``
    become objects 4, 5, ... inside the same stream, so any marker they contain is
    invisible to a raw-byte scan of the file.
    """
    bodies = [
        b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b" >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>",
        *extra_objects,
    ]
    header_parts, data, offset = [], b"", 0
    for number, body in enumerate(bodies, start=1):
        header_parts.append(f"{number} {offset}".encode())
        data += body + b"\n"
        offset = len(data)
    header = b" ".join(header_parts) + b"\n"
    raw = header + data
    packed = zlib.compress(raw)
    count = len(bodies)
    objstm_num, xref_num = count + 1, count + 2

    out = b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n"
    objstm_offset = len(out)
    out += (
        (
            f"{objstm_num} 0 obj\n<< /Type /ObjStm /N {count} /First {len(header)} "
            f"/Filter /FlateDecode /Length {len(packed)} >>\nstream\n"
        ).encode()
        + packed
        + b"\nendstream\nendobj\n"
    )
    xref_offset = len(out)
    rows = [bytes([0]) + (0).to_bytes(4, "big") + (65535).to_bytes(2, "big")]
    rows += [
        bytes([2]) + objstm_num.to_bytes(4, "big") + i.to_bytes(2, "big") for i in range(count)
    ]
    rows += [bytes([1]) + objstm_offset.to_bytes(4, "big") + (0).to_bytes(2, "big")]
    rows += [bytes([1]) + xref_offset.to_bytes(4, "big") + (0).to_bytes(2, "big")]
    xref_data = b"".join(rows)
    out += (
        (
            f"{xref_num} 0 obj\n<< /Type /XRef /Size {xref_num + 1} /W [1 4 2] /Root 1 0 R "
            f"/Length {len(xref_data)} >>\nstream\n"
        ).encode()
        + xref_data
        + b"\nendstream\nendobj\n"
    )
    out += f"startxref\n{xref_offset}\n%%EOF\n".encode()
    return out
