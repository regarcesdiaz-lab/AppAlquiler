"""What kind of file a tenant really sent. Pure functions, no I/O.

The MIME type declared by the sender (or by WhatsApp) is not proof: a renamed executable can
arrive as "image/jpeg". A receipt is accepted only when the content's signature (magic bytes) is
JPEG, PNG or PDF **and** matches the declared type.
"""

from dataclasses import dataclass
from enum import StrEnum

JPEG = "image/jpeg"
PNG = "image/png"
PDF = "application/pdf"
ACCEPTED_TYPES = frozenset({JPEG, PNG, PDF})
MAX_RECEIPT_BYTES = 10 * 1024 * 1024

EXTENSIONS = {JPEG: "jpg", PNG: "png", PDF: "pdf"}
_ALIASES = {"image/jpg": JPEG, "image/pjpeg": JPEG}
_SIGNATURES = (
    (b"\xff\xd8\xff", JPEG),
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"%PDF-", PDF),
)


class RejectReason(StrEnum):
    EMPTY = "empty"
    TOO_LARGE = "too_large"
    UNSUPPORTED_TYPE = "unsupported_type"  # not JPEG, PNG or PDF by content
    TYPE_MISMATCH = "type_mismatch"  # content and declared type disagree


@dataclass(frozen=True)
class MediaCheck:
    detected: str | None
    reason: RejectReason | None

    @property
    def accepted(self) -> bool:
        return self.reason is None


def normalize_mime(mime: str | None) -> str:
    base = (mime or "").split(";", 1)[0].strip().lower()
    return _ALIASES.get(base, base)


def sniff(content: bytes) -> str | None:
    """JPEG, PNG or PDF by signature; None for anything else."""
    for signature, mime in _SIGNATURES:
        if content.startswith(signature):
            return mime
    return None


def check_receipt_file(content: bytes, declared_mime: str | None) -> MediaCheck:
    if not content:
        return MediaCheck(None, RejectReason.EMPTY)
    if len(content) > MAX_RECEIPT_BYTES:
        return MediaCheck(None, RejectReason.TOO_LARGE)
    detected = sniff(content)
    if detected is None:
        return MediaCheck(None, RejectReason.UNSUPPORTED_TYPE)
    if normalize_mime(declared_mime) != detected:
        return MediaCheck(detected, RejectReason.TYPE_MISMATCH)
    return MediaCheck(detected, None)
