"""Dominican holidays come from the data file, and next year's list is always loaded."""

import datetime as dt

import pytest

from app.core.timezone import local_today
from app.domain.holidays import load_holidays, parse_holidays


def test_next_years_holidays_are_loaded() -> None:
    """Fails when the ministry's calendar for next year is missing from holidays_do.toml.

    Add it to backend/app/domain/data/holidays_do.toml (published every November at mt.gob.do).
    """
    next_year = local_today("America/Santo_Domingo").year + 1
    assert next_year in load_holidays().years(), (
        f"Missing Dominican holidays for {next_year} in app/domain/data/holidays_do.toml"
    )


@pytest.mark.parametrize("year", sorted(load_holidays().years()))
def test_every_year_has_the_twelve_official_holidays(year: int) -> None:
    holidays = load_holidays().holidays_in(year)
    assert len(holidays) == 12
    fixed = {(h.date.month, h.date.day) for h in holidays}
    # Never moved by Ley 139-97.
    assert {(1, 1), (1, 21), (2, 27), (8, 16), (9, 24), (12, 25)} <= fixed


@pytest.mark.parametrize(
    "day",
    [
        dt.date(2026, 1, 5),  # Reyes, moved from Tuesday the 6th
        dt.date(2026, 5, 4),  # Día del Trabajo, moved from Friday the 1st
        dt.date(2026, 11, 9),  # Constitución, moved from Friday the 6th
        dt.date(2026, 12, 25),
        dt.date(2027, 1, 25),  # Duarte, moved from Tuesday the 26th
        dt.date(2027, 3, 26),  # Viernes Santo
    ],
)
def test_known_observed_dates(day: dt.date) -> None:
    assert load_holidays().is_holiday(day)


def test_moved_holidays_are_not_holidays_on_the_original_date() -> None:
    calendar = load_holidays()
    assert not calendar.is_holiday(dt.date(2026, 1, 6))
    assert not calendar.is_holiday(dt.date(2026, 11, 6))
    assert calendar.name_of(dt.date(2026, 11, 20)) is None


def test_parse_rejects_duplicates_and_bad_entries() -> None:
    with pytest.raises(ValueError):
        parse_holidays(
            '[[holiday]]\ndate = 2026-01-01\nname = "A"\n'
            '[[holiday]]\ndate = 2026-01-01\nname = "B"\n'
        )
    with pytest.raises(ValueError):
        parse_holidays('[[holiday]]\ndate = 2026-01-01T10:00:00\nname = "A"\n')
    with pytest.raises(ValueError):
        parse_holidays('[[holiday]]\ndate = 2026-01-01\nname = " "\n')
