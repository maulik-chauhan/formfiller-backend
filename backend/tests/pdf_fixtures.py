"""Helpers that build in-memory PDFs for validator tests (not collected)."""

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
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
