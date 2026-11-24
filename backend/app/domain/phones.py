"""Phone numbers: normalize to E.164 and warn about numbers that don't look Dominican.

Pure functions, no I/O. Owners type numbers in many shapes ("809-555-0101", "(829) 555 0101",
"18495550101", "+1 809 555 0101"). Ten digits are taken as a North American number (+1), which
is how Dominican numbers are written locally.
"""

import re
from dataclasses import dataclass

E164_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
DO_AREA_CODES = frozenset({"809", "829", "849"})
_SEPARATORS = re.compile(r"[\s\-().]")

PHONE_NOT_DO_AREA_CODE = "phone_not_do_area_code"


class InvalidPhoneError(ValueError):
    pass


def normalize_phone(raw: str) -> str:
    text = _SEPARATORS.sub("", str(raw).strip())
    if text.startswith("00"):
        text = "+" + text[2:]
    if text.startswith("+"):
        digits = text[1:]
        if not digits.isdigit():
            raise InvalidPhoneError(raw)
        candidate = "+" + digits
    else:
        if not text.isdigit():
            raise InvalidPhoneError(raw)
        if len(text) == 10:
            candidate = "+1" + text
        elif len(text) == 11 and text.startswith("1"):
            candidate = "+" + text
        else:
            raise InvalidPhoneError(raw)
    if not E164_PATTERN.match(candidate):
        raise InvalidPhoneError(raw)
    return candidate


@dataclass(frozen=True)
class PhoneWarning:
    code: str
    message: str


def phone_warning(e164: str) -> PhoneWarning | None:
    """A +1 number outside 809/829/849 is probably a typo (or a US/Canada number): warn only."""
    if not e164.startswith("+1") or e164[2:5] in DO_AREA_CODES:
        return None
    return PhoneWarning(
        PHONE_NOT_DO_AREA_CODE,
        f"El número {e164} no tiene código de área de República Dominicana (809, 829 o 849). "
        "Revisa que esté bien escrito.",
    )
