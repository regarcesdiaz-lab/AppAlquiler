"""The presets are internally consistent and match what the owner approved."""

from decimal import Decimal

import pytest

from app.domain.messaging_rules import reminder_offsets_within_limits
from app.domain.rule_templates import (
    PLATFORM_LATE_FEE_CAP_PERCENT,
    PRESETS,
    LateFeeFrequency,
    LateFeeType,
    RuleTemplate,
)


def test_there_is_one_preset_per_template() -> None:
    assert set(PRESETS) == set(RuleTemplate)
    for template, preset in PRESETS.items():
        assert preset.template == template


@pytest.mark.parametrize("template", list(RuleTemplate))
def test_no_preset_breaks_its_own_reminder_limits(template: RuleTemplate) -> None:
    preset = PRESETS[template]
    offsets = [step.offset_days for step in preset.reminder_sequence]
    assert reminder_offsets_within_limits(
        offsets, preset.max_reminders_per_day, preset.max_reminders_per_week
    ), f"{template}: {offsets}"


@pytest.mark.parametrize("template", list(RuleTemplate))
def test_escalation_comes_after_the_last_reminder(template: RuleTemplate) -> None:
    preset = PRESETS[template]
    last = max(step.offset_days for step in preset.reminder_sequence)
    assert preset.escalation_days > last


@pytest.mark.parametrize("template", list(RuleTemplate))
def test_preset_cap_never_exceeds_the_platform_cap(template: RuleTemplate) -> None:
    cap = PRESETS[template].late_fee_cap_percent
    assert cap is None or Decimal(0) < cap <= PLATFORM_LATE_FEE_CAP_PERCENT


@pytest.mark.parametrize("template", list(RuleTemplate))
def test_reminders_are_never_on_sundays_by_default(template: RuleTemplate) -> None:
    assert 7 not in PRESETS[template].contact_days


def test_approved_values() -> None:
    flexible, standard, strict = (PRESETS[t] for t in RuleTemplate)
    assert (flexible.grace_days, standard.grace_days, strict.grace_days) == (5, 3, 1)
    assert flexible.late_fee_type == LateFeeType.NONE
    assert (standard.late_fee_type, standard.late_fee_value, standard.late_fee_frequency) == (
        LateFeeType.PERCENT,
        Decimal("5.00"),
        LateFeeFrequency.ONCE,
    )
    assert (strict.late_fee_value, strict.late_fee_frequency, strict.late_fee_cap_percent) == (
        Decimal("0.50"),
        LateFeeFrequency.DAILY,
        Decimal("10.00"),
    )
    assert [s.offset_days for s in flexible.reminder_sequence] == [-3, 0, 6]
    assert [s.offset_days for s in standard.reminder_sequence] == [-3, 0, 4, 8]
    assert [s.offset_days for s in strict.reminder_sequence] == [-3, 0, 2, 5]
    assert (flexible.escalation_days, standard.escalation_days, strict.escalation_days) == (
        10,
        10,
        6,
    )
    assert (flexible.max_extension_days, standard.max_extension_days) == (10, 7)
    assert strict.max_extension_days == 3
    assert [PRESETS[t].max_extensions_per_year for t in RuleTemplate] == [3, 2, 1]


@pytest.mark.parametrize(
    ("offsets", "ok"),
    [
        ([-3, 0, 2, 5], True),
        ([-5, -1, 0, 2, 5], False),  # 4 reminders between day -1 and day 5
        ([0, 0], False),  # two on the same day
        ([-3, 0, 4, 8], True),
        ([0, 7, 14], True),
        ([0, 2, 4, 6], False),
        ([], True),
    ],
)
def test_reminder_offsets_within_limits(offsets: list[int], ok: bool) -> None:
    assert reminder_offsets_within_limits(offsets, max_per_day=1, max_per_week=3) is ok
