"""Billing periods ("YYYY-MM") and due dates. Pure functions, no I/O."""

import calendar
import datetime as dt
import re

PERIOD_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def period_of(day: dt.date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def parse_period(period: str) -> tuple[int, int]:
    if not PERIOD_PATTERN.match(period):
        raise ValueError(f"Invalid period {period!r}; expected YYYY-MM")
    year, month = period.split("-")
    return int(year), int(month)


def add_months(period: str, months: int) -> str:
    year, month = parse_period(period)
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def due_date(period: str, due_day: int) -> dt.date:
    """Due date of a period; a due day past the end of the month falls on its last day."""
    if not 1 <= due_day <= 31:
        raise ValueError(f"Invalid due day {due_day}; expected 1-31")
    year, month = parse_period(period)
    last_day = calendar.monthrange(year, month)[1]
    return dt.date(year, month, min(due_day, last_day))
