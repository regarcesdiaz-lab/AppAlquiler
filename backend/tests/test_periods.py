import datetime as dt

import pytest

from app.core import timezone
from app.domain.periods import add_months, due_date, parse_period, period_of


def test_period_of() -> None:
    assert period_of(dt.date(2026, 3, 9)) == "2026-03"


@pytest.mark.parametrize(
    ("period", "months", "expected"),
    [
        ("2026-11", 1, "2026-12"),
        ("2026-12", 1, "2027-01"),
        ("2026-01", -1, "2025-12"),
        ("2026-05", -17, "2024-12"),
        ("2026-05", 0, "2026-05"),
    ],
)
def test_add_months(period: str, months: int, expected: str) -> None:
    assert add_months(period, months) == expected


@pytest.mark.parametrize(
    ("period", "day", "expected"),
    [
        ("2026-11", 15, dt.date(2026, 11, 15)),
        ("2026-02", 31, dt.date(2026, 2, 28)),
        ("2027-02", 29, dt.date(2027, 2, 28)),  # day 29 in a non-leap February
        ("2027-02", 30, dt.date(2027, 2, 28)),
        ("2028-02", 29, dt.date(2028, 2, 29)),  # leap year: the 29th exists
        ("2028-02", 30, dt.date(2028, 2, 29)),
        ("2026-04", 31, dt.date(2026, 4, 30)),
    ],
)
def test_due_date_clamps_to_end_of_month(period: str, day: int, expected: dt.date) -> None:
    assert due_date(period, day) == expected


@pytest.mark.parametrize("bad", ["2026-13", "2026-1", "26-01", "2026/01"])
def test_parse_period_rejects_bad_input(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_period(bad)


def test_local_today_uses_santo_domingo_not_utc(monkeypatch) -> None:
    # 02:00 UTC on the 17th is still 22:00 on the 16th in Santo Domingo (UTC-4).
    monkeypatch.setattr(timezone, "utc_now", lambda: dt.datetime(2026, 11, 17, 2, tzinfo=dt.UTC))
    assert timezone.local_today("America/Santo_Domingo") == dt.date(2026, 11, 16)
