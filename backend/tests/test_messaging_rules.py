"""Send validator: contact window, holidays, frequency, consent and active promises."""

import datetime as dt

import pytest

from app.domain.holidays import HolidayCalendar, load_holidays
from app.domain.messaging_rules import (
    SANTO_DOMINGO,
    BlockReason,
    Consent,
    ContactRules,
    MessageKind,
    SendRequest,
    next_contact_time,
    validate_send,
)
from app.domain.rule_templates import PRESETS, RuleTemplate

RULES = ContactRules.from_preset(PRESETS[RuleTemplate.STANDARD])
HOLIDAYS = load_holidays()
CONSENT = Consent(granted_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))


def local(day: int, hour: int, minute: int = 0, month: int = 11) -> dt.datetime:
    """A moment in Santo Domingo, November 2026 by default (the 20th is a Friday)."""
    return dt.datetime(2026, month, day, hour, minute, tzinfo=SANTO_DOMINGO)


def check(kind: MessageKind = MessageKind.COLLECTION_REMINDER, **kwargs) -> tuple:
    kwargs.setdefault("at", local(20, 10))
    kwargs.setdefault("consent", CONSENT)
    return validate_send(SendRequest(kind=kind, **kwargs), RULES, HOLIDAYS).reasons


def test_reminder_inside_the_window_is_allowed() -> None:
    assert check() == ()


# -- contact window ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("at", "allowed"),
    [
        (local(20, 7, 59), False),
        (local(20, 8, 0), True),
        (local(20, 18, 59), True),
        (local(20, 19, 0), False),  # the window ends at 19:00
        (local(21, 12), True),  # Saturday
    ],
)
def test_contact_hours(at: dt.datetime, allowed: bool) -> None:
    assert (check(at=at) == ()) is allowed


def test_hours_are_checked_in_santo_domingo_not_utc() -> None:
    # 23:30 UTC is 19:30 in Santo Domingo: outside the window.
    at = dt.datetime(2026, 11, 20, 23, 30, tzinfo=dt.UTC)
    assert check(at=at) == (BlockReason.OUTSIDE_HOURS,)
    # 12:30 UTC is 08:30 in Santo Domingo: inside.
    assert check(at=dt.datetime(2026, 11, 20, 12, 30, tzinfo=dt.UTC)) == ()


def test_never_on_sunday_even_if_the_rule_set_allows_it() -> None:
    every_day = ContactRules(contact_days=(1, 2, 3, 4, 5, 6, 7))
    request = SendRequest(MessageKind.COLLECTION_REMINDER, local(22, 10), CONSENT)
    assert validate_send(request, every_day, HOLIDAYS).reasons == (BlockReason.SUNDAY,)


def test_days_off_of_the_rule_set() -> None:
    weekdays = ContactRules(contact_days=(1, 2, 3, 4, 5))
    request = SendRequest(MessageKind.RECEIPT, local(21, 10), CONSENT)
    assert validate_send(request, weekdays, HOLIDAYS).reasons == (BlockReason.DAY_OFF,)


def test_never_on_dominican_holidays() -> None:
    # Monday 9 November 2026: Día de la Constitución (moved).
    assert check(at=local(9, 10)) == (BlockReason.HOLIDAY,)


def test_holidays_come_from_the_calendar_given() -> None:
    custom = HolidayCalendar([])
    request = SendRequest(MessageKind.COLLECTION_REMINDER, local(9, 10), CONSENT)
    assert validate_send(request, RULES, custom).allowed


def test_every_reason_is_reported() -> None:
    reasons = check(at=local(22, 21), consent=None)
    assert set(reasons) == {BlockReason.NO_CONSENT, BlockReason.SUNDAY, BlockReason.OUTSIDE_HOURS}


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(ValueError):
        check(at=dt.datetime(2026, 11, 20, 10))  # noqa: DTZ001


# -- consent -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind", [MessageKind.COLLECTION_REMINDER, MessageKind.WELCOME, MessageKind.RECEIPT]
)
def test_business_initiated_messages_need_consent(kind: MessageKind) -> None:
    assert check(kind, consent=None) == (BlockReason.NO_CONSENT,)


def test_consent_granted_later_does_not_count() -> None:
    future = Consent(granted_at=local(21, 9))
    assert check(consent=future) == (BlockReason.NO_CONSENT,)


@pytest.mark.parametrize("kind", list(MessageKind))
def test_baja_stops_every_message_immediately(kind: MessageKind) -> None:
    revoked = Consent(granted_at=CONSENT.granted_at, revoked_at=local(20, 9, 59))
    assert BlockReason.CONSENT_REVOKED in check(kind, consent=revoked)


def test_baja_after_the_send_time_does_not_block_it() -> None:
    revoked_later = Consent(granted_at=CONSENT.granted_at, revoked_at=local(20, 11))
    assert check(consent=revoked_later) == ()


@pytest.mark.parametrize("kind", [MessageKind.REPLY, MessageKind.PROMISE_CONFIRMATION])
def test_responses_ignore_window_and_need_no_consent_record(kind: MessageKind) -> None:
    # Sunday night, no consent record: the tenant wrote first.
    assert check(kind, at=local(22, 22), consent=None) == ()


# -- frequency ---------------------------------------------------------------------------------


def test_one_collection_reminder_per_day() -> None:
    assert check(reminders_sent=[local(20, 8, 30)]) == (BlockReason.DAILY_LIMIT,)


def test_three_collection_reminders_in_any_seven_days() -> None:
    sent = [local(14, 10), local(16, 10), local(18, 10)]
    assert check(reminders_sent=sent) == (BlockReason.WEEKLY_LIMIT,)
    # The one from the 13th is outside the window that ends today (14th to 20th).
    assert check(reminders_sent=[local(13, 10), local(16, 10), local(18, 10)]) == ()


def test_daily_limit_uses_the_local_day() -> None:
    # 01:00 UTC on the 20th is 21:00 on the 19th in Santo Domingo: another day.
    yesterday_night = dt.datetime(2026, 11, 20, 1, tzinfo=dt.UTC)
    assert check(reminders_sent=[yesterday_night]) == ()


@pytest.mark.parametrize(
    "kind",
    [
        MessageKind.WELCOME,
        MessageKind.RECEIPT,
        MessageKind.REPLY,
        MessageKind.PROMISE_CONFIRMATION,
    ],
)
def test_other_messages_are_not_limited_by_frequency(kind: MessageKind) -> None:
    sent = [local(18, 10), local(19, 10), local(20, 9)]
    assert check(kind, reminders_sent=sent) == ()


def test_custom_limits() -> None:
    relaxed = ContactRules(max_per_day=2, max_per_week=5)
    request = SendRequest(
        MessageKind.COLLECTION_REMINDER, local(20, 15), CONSENT, reminders_sent=[local(20, 9)]
    )
    assert validate_send(request, relaxed, HOLIDAYS).allowed


def test_invalid_limits_are_refused() -> None:
    with pytest.raises(ValueError):
        ContactRules(max_per_day=0)
    with pytest.raises(ValueError):
        ContactRules(max_per_day=3, max_per_week=2)
    with pytest.raises(ValueError):
        ContactRules(contact_start=dt.time(19), contact_end=dt.time(8))


# -- active promise ----------------------------------------------------------------------------


def test_no_collection_reminder_before_the_promised_date() -> None:
    promised = dt.date(2026, 11, 24)
    assert check(active_promise_date=promised) == (BlockReason.ACTIVE_PROMISE,)


def test_reminder_allowed_on_the_promised_date() -> None:
    assert check(active_promise_date=dt.date(2026, 11, 20)) == ()


def test_promise_does_not_block_receipts_or_replies() -> None:
    promised = dt.date(2026, 11, 24)
    assert check(MessageKind.RECEIPT, active_promise_date=promised) == ()
    assert check(MessageKind.REPLY, active_promise_date=promised) == ()


# -- next contact time -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("after", "expected"),
    [
        (local(20, 10), local(20, 10)),  # already inside
        (local(20, 6), local(20, 8)),  # before opening
        (local(21, 20), local(23, 8)),  # Saturday night -> Monday (skips Sunday)
        (local(7, 20), local(10, 8)),  # Saturday 7th -> Tuesday 10th (Monday 9th is a holiday)
        (local(24, 19, 30, month=12), local(26, 8, month=12)),  # Christmas, then Saturday
    ],
)
def test_next_contact_time(after: dt.datetime, expected: dt.datetime) -> None:
    assert next_contact_time(after, RULES, HOLIDAYS) == expected
