"""WhatsApp keywords. Pure functions, no I/O.

A keyword is the whole message, ignoring case, accents, punctuation and spaces around it:
"baja", "Baja.", " BAJA " and "bajá" are BAJA; "quiero la baja" is not (that is free text for
the owner). STOP is accepted as BAJA because WhatsApp users know it from other services.
"""

import re
import unicodedata
from enum import StrEnum


class Keyword(StrEnum):
    MENU = "menu"
    BAJA = "baja"
    ALTA = "alta"


_WORDS = {
    "MENU": Keyword.MENU,
    "BAJA": Keyword.BAJA,
    "STOP": Keyword.BAJA,
    "ALTA": Keyword.ALTA,
}
_NOT_WORD = re.compile(r"[^A-Z0-9 ]+")


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(_NOT_WORD.sub(" ", without_accents.upper()).split())


def detect_keyword(text: str | None) -> Keyword | None:
    if not text:
        return None
    return _WORDS.get(normalize(text))
