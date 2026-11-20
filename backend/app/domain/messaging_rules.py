"""Send validator: may this message go to the tenant now? Pure functions, no I/O.

The AI proposes, the rules decide (CLAUDE.md, rule 4). Every outbound message to a tenant is
checked here first. The decision lists every reason that blocks it, not only the first one.

Rules
-----
* **Business-initiated** messages (collection reminders, welcome, receipts) need a current
  ``ConsentRecord`` and are only sent inside the contact window: the rule set's days and hours,
  never on Sundays and never on Dominican holidays (rules 6 and 7).
* **Responses** (a reply to the tenant, a promise confirmation) answer something the tenant
  wrote, so they don't need the window or a consent record; they still stop after BAJA.
* **Frequency** limits count collection reminders only: at most ``max_per_day`` per local day
  and ``max_per_week`` in any rolling 7 days. Welcome, receipts, replies and promise
  confirmations don't count and are never limited.
* **Active promise**: no collection reminder before the promised date. On the promised date
  itself a reminder is allowed ("te escribimos ese día").
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.domain.holidays import HolidayCalendar
from app.domain.rule_templates import (
    DEFAULT_CONTACT_DAYS,
    DEFAULT_CONTACT_END,
    DEFAULT_CONTACT_START,
    DEFAULT_MAX_REMINDERS_PER_DAY,
    DEFAULT_MAX_REMINDERS_PER_WEEK,
    RulePreset,
)

SANTO_DOMINGO = ZoneInfo("America/Santo_Domingo")
SUNDAY = 7
WEEK_DAYS = 7


class MessageKind(StrEnum):
    COLLECTION_REMINDER = "collection_reminder"
    WELCOME = "welcome"
    RECEIPT = "receipt"
    REPLY = "reply"
    PROMISE_CONFIRMATION = "promise_confirmation"


BUSINESS_INITIATED = frozenset(
    {MessageKind.COLLECTION_REMINDER, MessageKind.WELCOME, MessageKind.RECEIPT}
)
FREQUENCY_LIMITED = frozenset({MessageKind.COLLECTION_REMINDER})


class BlockReason(StrEnum):
    NO_CONSENT = "no_consent"
    CONSENT_REVOKED = "consent_revoked"
    SUNDAY = "sunday"
    HOLIDAY = "holiday"
    DAY_OFF = "day_off"  # not one of the rule set's contact days
    OUTSIDE_HOURS = "outside_hours"
    DAILY_LIMIT = "daily_limit"
    WEEKLY_LIMIT = "weekly_limit"
    ACTIVE_PROMISE = "active_promise"


@dataclass(frozen=True)
class ContactRules:
    contact_days: tuple[int, ...] = DEFAULT_CONTACT_DAYS  # ISO weekdays
    contact_start: dt.time = DEFAULT_CONTACT_START
    contact_end: dt.time = DEFAULT_CONTACT_END
    max_per_day: int = DEFAULT_MAX_REMINDERS_PER_DAY
    max_per_week: int = DEFAULT_MAX_REMINDERS_PER_WEEK

    def __post_init__(self) -> None:
        if self.contact_start >= self.contact_end:
            raise ValueError("contact_start must be before contact_end")
        if self.max_per_day < 1 or self.max_per_week < self.max_per_day:
            raise ValueError("Invalid reminder limits")

    @classmethod
    def from_preset(cls, preset: RulePreset) -> "ContactRules":
        return cls(
            contact_days=preset.contact_days,
            contact_start=preset.contact_start,
            contact_end=preset.contact_end,
            max_per_day=preset.max_reminders_per_day,
            max_per_week=preset.max_reminders_per_week,
        )


@dataclass(frozen=True)
class Consent:
    granted_at: dt.datetime
    revoked_at: dt.datetime | None = None

    def is_revoked(self, at: dt.datetime) -> bool:
        return self.revoked_at is not None and self.revoked_at <= at

    def is_current(self, at: dt.datetime) -> bool:
        return self.granted_at <= at and not self.is_revoked(at)


@dataclass(frozen=True)
class SendRequest:
    kind: MessageKind
    at: dt.datetime  # timezone-aware
    consent: Consent | None
    # Times of the collection reminders already sent for this lease.
    reminders_sent: Sequence[dt.datetime] = ()
    active_promise_date: dt.date | None = None


@dataclass(frozen=True)
class SendDecision:
    reasons: tuple[BlockReason, ...]

    @property
    def allowed(self) -> bool:
        return not self.reasons


def _require_aware(at: dt.datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("Datetimes must be timezone-aware")


def window_blocks(
    local: dt.datetime, rules: ContactRules, holidays: HolidayCalendar
) -> list[BlockReason]:
    """Why a local date-time is outside the contact window (empty when it is inside)."""
    reasons: list[BlockReason] = []
    day = local.date()
    if day.isoweekday() == SUNDAY:
        reasons.append(BlockReason.SUNDAY)
    elif day.isoweekday() not in rules.contact_days:
        reasons.append(BlockReason.DAY_OFF)
    if holidays.is_holiday(day):
        reasons.append(BlockReason.HOLIDAY)
    if not rules.contact_start <= local.time() < rules.contact_end:
        reasons.append(BlockReason.OUTSIDE_HOURS)
    return reasons


def frequency_blocks(
    local_day: dt.date,
    reminders_sent_on: Iterable[dt.date],
    rules: ContactRules,
) -> list[BlockReason]:
    """Limits for one more collection reminder on ``local_day``."""
    days = list(reminders_sent_on)
    week_start = local_day - dt.timedelta(days=WEEK_DAYS - 1)
    reasons: list[BlockReason] = []
    if sum(1 for d in days if d == local_day) >= rules.max_per_day:
        reasons.append(BlockReason.DAILY_LIMIT)
    if sum(1 for d in days if week_start <= d <= local_day) >= rules.max_per_week:
        reasons.append(BlockReason.WEEKLY_LIMIT)
    return reasons


def validate_send(
    request: SendRequest,
    rules: ContactRules,
    holidays: HolidayCalendar,
    zone: ZoneInfo = SANTO_DOMINGO,
) -> SendDecision:
    _require_aware(request.at)
    local = request.at.astimezone(zone)
    reasons: list[BlockReason] = []

    consent = request.consent
    if consent is not None and consent.is_revoked(request.at):
        reasons.append(BlockReason.CONSENT_REVOKED)
    elif request.kind in BUSINESS_INITIATED and (
        consent is None or not consent.is_current(request.at)
    ):
        reasons.append(BlockReason.NO_CONSENT)

    if request.kind in BUSINESS_INITIATED:
        reasons.extend(window_blocks(local, rules, holidays))

    if request.kind in FREQUENCY_LIMITED:
        for sent in request.reminders_sent:
            _require_aware(sent)
        sent_days = [s.astimezone(zone).date() for s in request.reminders_sent if s <= request.at]
        reasons.extend(frequency_blocks(local.date(), sent_days, rules))
        if request.active_promise_date is not None and local.date() < request.active_promise_date:
            reasons.append(BlockReason.ACTIVE_PROMISE)

    return SendDecision(tuple(reasons))


def next_contact_time(
    after: dt.datetime,
    rules: ContactRules,
    holidays: HolidayCalendar,
    zone: ZoneInfo = SANTO_DOMINGO,
    max_days: int = 31,
) -> dt.datetime:
    """Earliest moment at or after ``after`` inside the contact window (ignores frequency)."""
    _require_aware(after)
    local = after.astimezone(zone)
    for offset in range(max_days + 1):
        day = local.date() + dt.timedelta(days=offset)
        start = dt.datetime.combine(day, rules.contact_start, tzinfo=zone)
        end = dt.datetime.combine(day, rules.contact_end, tzinfo=zone)
        candidate = max(start, local)
        if candidate < end and not window_blocks(candidate, rules, holidays):
            return candidate
    raise ValueError(f"No contact window in the next {max_days} days")


def reminder_offsets_within_limits(
    offsets: Iterable[int], max_per_day: int, max_per_week: int
) -> bool:
    """True when a reminder sequence (days relative to the due date) never breaks the limits."""
    days = sorted(offsets)
    for day in days:
        if days.count(day) > max_per_day:
            return False
        if sum(1 for d in days if day - WEEK_DAYS < d <= day) > max_per_week:
            return False
    return True
