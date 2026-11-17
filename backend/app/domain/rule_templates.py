"""Preset collection rules (flexible / standard / strict). Pure data, no I/O.

These are the starting values offered during onboarding; the owner can adjust every field.
Reminder offsets are days relative to the due date (negative = before it).
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class RuleTemplate(StrEnum):
    FLEXIBLE = "flexible"
    STANDARD = "standard"
    STRICT = "strict"


class LateFeeType(StrEnum):
    NONE = "none"
    FIXED = "fixed"
    PERCENT = "percent"


# Monday to Saturday (ISO weekdays). Never Sundays (CLAUDE.md, rule 7).
DEFAULT_CONTACT_DAYS: tuple[int, ...] = (1, 2, 3, 4, 5, 6)
DEFAULT_CONTACT_START = dt.time(8, 0)
DEFAULT_CONTACT_END = dt.time(19, 0)


@dataclass(frozen=True)
class ReminderStep:
    offset_days: int
    template: str


@dataclass(frozen=True)
class RulePreset:
    template: RuleTemplate
    grace_days: int
    late_fee_type: LateFeeType
    late_fee_value: Decimal
    max_extension_days: int
    max_extensions_per_year: int
    reminder_sequence: tuple[ReminderStep, ...]
    contact_days: tuple[int, ...] = DEFAULT_CONTACT_DAYS
    contact_start: dt.time = DEFAULT_CONTACT_START
    contact_end: dt.time = DEFAULT_CONTACT_END

    def reminder_sequence_json(self) -> list[dict[str, object]]:
        return [
            {"offset_days": s.offset_days, "template": s.template} for s in self.reminder_sequence
        ]


PRESETS: dict[RuleTemplate, RulePreset] = {
    RuleTemplate.FLEXIBLE: RulePreset(
        template=RuleTemplate.FLEXIBLE,
        grace_days=5,
        late_fee_type=LateFeeType.NONE,
        late_fee_value=Decimal("0.00"),
        max_extension_days=10,
        max_extensions_per_year=3,
        reminder_sequence=(
            ReminderStep(-3, "reminder_upcoming"),
            ReminderStep(0, "reminder_due_today"),
            ReminderStep(6, "reminder_overdue"),
        ),
    ),
    RuleTemplate.STANDARD: RulePreset(
        template=RuleTemplate.STANDARD,
        grace_days=3,
        late_fee_type=LateFeeType.PERCENT,
        late_fee_value=Decimal("5.00"),
        max_extension_days=7,
        max_extensions_per_year=2,
        reminder_sequence=(
            ReminderStep(-3, "reminder_upcoming"),
            ReminderStep(0, "reminder_due_today"),
            ReminderStep(4, "reminder_overdue"),
            ReminderStep(8, "reminder_overdue_final"),
        ),
    ),
    RuleTemplate.STRICT: RulePreset(
        template=RuleTemplate.STRICT,
        grace_days=1,
        late_fee_type=LateFeeType.PERCENT,
        late_fee_value=Decimal("10.00"),
        max_extension_days=3,
        max_extensions_per_year=1,
        reminder_sequence=(
            ReminderStep(-5, "reminder_upcoming"),
            ReminderStep(-1, "reminder_upcoming"),
            ReminderStep(0, "reminder_due_today"),
            ReminderStep(2, "reminder_overdue"),
            ReminderStep(5, "reminder_overdue_final"),
        ),
    ),
}
