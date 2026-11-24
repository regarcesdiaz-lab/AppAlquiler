import pytest

from app.domain.phones import (
    PHONE_NOT_DO_AREA_CODE,
    InvalidPhoneError,
    normalize_phone,
    phone_warning,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("809-555-0101", "+18095550101"),
        ("(829) 555 0101", "+18295550101"),
        ("18495550101", "+18495550101"),
        ("+1 809 555 0101", "+18095550101"),
        ("0018095550101", "+18095550101"),
        ("+34 612 345 678", "+34612345678"),
    ],
)
def test_normalizes_to_e164(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["", "555-0101", "abc", "+1-809-555-01x1", "+0123456789"])
def test_rejects_invalid_numbers(raw: str) -> None:
    with pytest.raises(InvalidPhoneError):
        normalize_phone(raw)


@pytest.mark.parametrize("phone", ["+18095550101", "+18295550101", "+18495550101", "+34612345678"])
def test_no_warning_for_dominican_or_non_nanp_numbers(phone: str) -> None:
    assert phone_warning(phone) is None


def test_warns_for_plus_one_outside_dominican_area_codes() -> None:
    warning = phone_warning("+13055550101")
    assert warning is not None
    assert warning.code == PHONE_NOT_DO_AREA_CODE
    assert "809, 829 o 849" in warning.message
