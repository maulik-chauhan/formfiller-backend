"""Unit tests for the blank-PDF security validator."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NullObject,
    NumberObject,
    PdfObject,
    TextStringObject,
)

from app.modules.templates.pdf_validator import PdfValidationError, validate_blank_pdf
from tests.pdf_fixtures import (
    make_blank_pdf,
    make_pdf_with_object_stream,
    make_pdf_with_open_action,
    make_pdf_with_token,
    make_prefilled_acroform_pdf,
)


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


# --------------------------------------------------------------------------- #
# DEC-9: /OpenAction is allowed only as a view destination in the catalog.
# --------------------------------------------------------------------------- #


Builder = Callable[[PdfWriter], PdfObject]


def _dest(*params: PdfObject, page: int = 0, fit: str = "/FitH") -> Builder:
    def build(writer: PdfWriter) -> PdfObject:
        return ArrayObject([writer.pages[page].indirect_reference, NameObject(fit), *params])

    return build


@pytest.mark.parametrize(
    "build",
    [
        _dest(NullObject()),  # the shape used by the official Karnataka Transport PDFs
        _dest(NumberObject(800)),
        _dest(fit="/Fit"),
        _dest(NumberObject(0), NumberObject(842), NullObject(), fit="/XYZ"),
        _dest(NullObject(), page=1),
    ],
)
def test_view_destination_open_action_accepted(build: Builder) -> None:
    result = validate_blank_pdf(make_pdf_with_open_action(build, pages=2))
    assert result.page_count == 2


def _action(entries: dict[str, PdfObject]) -> Builder:
    def build(_writer: PdfWriter) -> PdfObject:
        return DictionaryObject({NameObject(k): v for k, v in entries.items()})

    return build


@pytest.mark.parametrize(
    "build",
    [
        # Action dictionaries are never allowed, whatever their type.
        _action({"/S": NameObject("/URI"), "/URI": TextStringObject("https://example.test")}),
        _action({"/S": NameObject("/Named"), "/N": NameObject("/Print")}),
        _action({"/S": NameObject("/GoTo"), "/D": TextStringObject("page1")}),
        # Destinations that are not a plain local page view.
        lambda w: ArrayObject([NumberObject(0), NameObject("/FitH"), NullObject()]),
        lambda w: TextStringObject("named-destination"),
        lambda w: ArrayObject([w.pages[0].indirect_reference]),
        _dest(NullObject(), fit="/Print"),
        _dest(),  # /FitH without its parameter
        _dest(NullObject(), NullObject()),  # too many parameters
        _dest(TextStringObject("x")),  # non-numeric parameter
    ],
)
def test_non_destination_open_action_rejected(build: Builder) -> None:
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(make_pdf_with_open_action(build))


def test_open_action_token_outside_catalog_rejected() -> None:
    # A valid catalog destination does not excuse a second /OpenAction elsewhere.
    pdf = make_pdf_with_open_action(_dest(NullObject()))
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(pdf[:20] + b"/OpenAction" + pdf[20:])


def test_destination_open_action_does_not_relax_other_tokens() -> None:
    pdf = make_pdf_with_open_action(_dest(NullObject()))
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(pdf[:20] + b"/JavaScript" + pdf[20:])


# --------------------------------------------------------------------------- #
# Markers hidden inside compressed object streams (invisible to the byte scan).
# --------------------------------------------------------------------------- #


def test_clean_object_stream_pdf_accepted() -> None:
    assert validate_blank_pdf(make_pdf_with_object_stream()).page_count == 1


def test_object_stream_destination_open_action_accepted() -> None:
    pdf = make_pdf_with_object_stream(b"/OpenAction [3 0 R /FitH null]")
    assert b"/OpenAction" not in pdf  # compressed: only the structural check sees it
    assert validate_blank_pdf(pdf).page_count == 1


@pytest.mark.parametrize(
    ("catalog_extra", "extra_objects"),
    [
        (b"/OpenAction 4 0 R", (b"<< /S /JavaScript /JS (app.alert(1)) >>",)),
        (b"/OpenAction 4 0 R", (b"<< /S /Launch /F (calc.exe) >>",)),
        (b"/OpenAction << /S /URI /URI (https://example.test) >>", ()),
        (b"/AA << /WC 4 0 R >>", (b"<< /S /GoTo /D [3 0 R /Fit] >>",)),
        (b"/Names << /JavaScript 4 0 R >>", (b"<< /Names [(a) 5 0 R] >>", b"<< /S /GoTo >>")),
        (b"/Names << /EmbeddedFiles 4 0 R >>", (b"<< /Names [] >>",)),
        (b"", (b"<< /Type /EmbeddedFile /Length 0 >>",)),
        # an /OpenAction key anywhere but the catalog
        (b"", (b"<< /OpenAction [3 0 R /Fit] >>",)),
    ],
)
def test_markers_inside_object_streams_rejected(
    catalog_extra: bytes, extra_objects: tuple[bytes, ...]
) -> None:
    pdf = make_pdf_with_object_stream(catalog_extra, extra_objects)
    for token in (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/AA", b"/OpenAction"):
        assert token not in pdf  # the raw-byte scan alone would accept this file
    with pytest.raises(PdfValidationError):
        validate_blank_pdf(pdf)
