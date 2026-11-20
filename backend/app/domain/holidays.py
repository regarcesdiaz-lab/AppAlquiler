"""Dominican public holidays, read from a data file (never hard-coded in the code).

The list lives in ``app/domain/data/holidays_do.toml`` with the observed dates of each year.
Reading that file is the only I/O here; everything else works on a ``HolidayCalendar``.
"""

import datetime as dt
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_HOLIDAYS_FILE = Path(__file__).parent / "data" / "holidays_do.toml"


@dataclass(frozen=True)
class Holiday:
    date: dt.date
    name: str


class HolidayCalendar:
    def __init__(self, holidays: list[Holiday]) -> None:
        self._by_date = {h.date: h for h in holidays}
        if len(self._by_date) != len(holidays):
            raise ValueError("Duplicate holiday dates")

    def is_holiday(self, day: dt.date) -> bool:
        return day in self._by_date

    def name_of(self, day: dt.date) -> str | None:
        holiday = self._by_date.get(day)
        return holiday.name if holiday else None

    def years(self) -> set[int]:
        return {day.year for day in self._by_date}

    def holidays_in(self, year: int) -> list[Holiday]:
        return sorted(
            (h for h in self._by_date.values() if h.date.year == year), key=lambda h: h.date
        )


def parse_holidays(text: str) -> HolidayCalendar:
    data = tomllib.loads(text)
    holidays: list[Holiday] = []
    for entry in data.get("holiday", []):
        day = entry["date"]
        if not isinstance(day, dt.date) or isinstance(day, dt.datetime):
            raise ValueError(f"Holiday date must be a plain date, got {day!r}")
        name = str(entry["name"]).strip()
        if not name:
            raise ValueError(f"Holiday {day} has no name")
        holidays.append(Holiday(day, name))
    return HolidayCalendar(holidays)


@lru_cache
def load_holidays(path: Path = DEFAULT_HOLIDAYS_FILE) -> HolidayCalendar:
    return parse_holidays(path.read_text(encoding="utf-8"))
