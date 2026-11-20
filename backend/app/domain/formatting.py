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


def format_day_month(day: dt.date) -> str:
    """'25 de noviembre'."""
    return f"{day.day} de {MONTHS[day.month - 1]}"
