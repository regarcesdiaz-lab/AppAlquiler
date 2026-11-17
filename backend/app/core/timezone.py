"""Time helpers.

Business dates (due dates, periods, contact hours) live in the organization's zone,
America/Santo_Domingo by default. Timestamps are always stored and compared in UTC.
"""

import datetime as dt
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def business_zone(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(name or get_settings().TIMEZONE)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def local_today(zone_name: str | None = None) -> dt.date:
    """Today's date in the business time zone (not the server's)."""
    return utc_now().astimezone(business_zone(zone_name)).date()
