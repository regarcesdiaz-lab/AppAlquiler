"""Spanish texts for users: money and dates as docs/BRAND.md writes them."""

import datetime as dt
from decimal import Decimal

MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def format_dop(amount: Decimal) -> str:
    """RD$25,000 (no decimals when they are ,00), as in docs/BRAND.md."""
    if amount == amount.to_integral_value():
        return f"RD${amount:,.0f}"
    return f"RD${amount:,.2f}"


WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def format_day_month(day: dt.date) -> str:
    """'25 de noviembre'."""
    return f"{day.day} de {MONTHS[day.month - 1]}"


def format_long_date(day: dt.date) -> str:
    """'viernes 1 de noviembre' (long date in docs/BRAND.md)."""
    return f"{WEEKDAYS[day.weekday()]} {format_day_month(day)}"


def format_days(days: int) -> str:
    return "1 día" if days == 1 else f"{days} días"


def format_month_year(period: str) -> str:
    """'2026-11' → 'noviembre 2026'."""
    year, month = period.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"
