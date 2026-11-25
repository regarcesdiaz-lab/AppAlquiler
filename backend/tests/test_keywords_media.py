"""Keywords (whole message) and the real type of a receipt file (magic bytes). Pure functions."""

import pytest

from app.domain.keywords import Keyword, detect_keyword
from app.domain.media import (
    MAX_RECEIPT_BYTES,
    PDF,
    PNG,
    RejectReason,
    check_receipt_file,
    sniff,
)
from tests.whatsapp_utils import EXE_BYTES, JPEG_BYTES, PDF_BYTES, PNG_BYTES


@pytest.mark.parametrize(
    ("text", "keyword"),
    [
        ("MENÚ", Keyword.MENU),
        ("menu", Keyword.MENU),
        (" Menú. ", Keyword.MENU),
        ("BAJA", Keyword.BAJA),
        ("baja!", Keyword.BAJA),
        ("Bajá", Keyword.BAJA),
        ("stop", Keyword.BAJA),
        ("ALTA", Keyword.ALTA),
        ("alta.", Keyword.ALTA),
        ("quiero la baja", None),
        ("altamente", None),
        ("", None),
        (None, None),
    ],
)
def test_keywords_are_the_whole_message(text, keyword) -> None:
    assert detect_keyword(text) == keyword


@pytest.mark.parametrize(
    ("content", "mime"),
    [(JPEG_BYTES, "image/jpeg"), (PNG_BYTES, PNG), (PDF_BYTES, PDF), (EXE_BYTES, None)],
)
def test_sniff_by_signature(content, mime) -> None:
    assert sniff(content) == mime


@pytest.mark.parametrize(
    ("content", "declared", "reason"),
    [
        (JPEG_BYTES, "image/jpeg", None),
        (JPEG_BYTES, "image/jpg", None),  # common alias
        (PNG_BYTES, "image/png", None),
        (PDF_BYTES, "application/pdf; charset=binary", None),
        (EXE_BYTES, "image/jpeg", RejectReason.UNSUPPORTED_TYPE),
        (b"<html>", "application/pdf", RejectReason.UNSUPPORTED_TYPE),
        (PNG_BYTES, "image/jpeg", RejectReason.TYPE_MISMATCH),
        (PDF_BYTES, "image/png", RejectReason.TYPE_MISMATCH),
        (JPEG_BYTES, None, RejectReason.TYPE_MISMATCH),
        (b"", "image/jpeg", RejectReason.EMPTY),
        (b"\xff\xd8\xff" + b"0" * MAX_RECEIPT_BYTES, "image/jpeg", RejectReason.TOO_LARGE),
    ],
)
def test_receipt_must_be_what_it_claims(content, declared, reason) -> None:
    check = check_receipt_file(content, declared)
    assert check.reason == reason
    assert check.accepted is (reason is None)
