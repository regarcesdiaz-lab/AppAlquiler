"""Extension validator, escalation to the owner and the Ley 85-25 deposit warning."""

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.deposit import deposit_warning
from app.domain.escalation import (
    INBOX_TYPE,
    EscalationReason,
    consent_revoked_task,
    extension_task,
    overdue_needs_escalation,
    overdue_task,
    promise_broken_task,
)
from app.domain.extensions import ExtensionIssue, ExtensionOutcome, review_extension
from app.domain.rule_templates import PRESETS, RuleTemplate
from app.models.enums import InboxItemType

D = Decimal
DUE = dt.date(2026, 11, 1)
TODAY = dt.date(2026, 11, 3)
STANDARD = PRESETS[RuleTemplate.STANDARD]  # 7 days, 2 per year


def review(requested: dt.date, granted_on=(), today: dt.date = TODAY):
    return review_extension(
        due_on=DUE,
        requested_date=requested,
        today=today,
        granted_on=granted_on,
        max_days=STANDARD.max_extension_days,
        max_per_year=STANDARD.max_extensions_per_year,
    )


# -- extensions --------------------------------------------------------------------------------


def test_extension_within_limit() -> None:
    decision = review(dt.date(2026, 11, 8))
    assert (decision.outcome, decision.days, decision.issues) == (
        ExtensionOutcome.WITHIN_LIMIT,
        7,
        (),
    )


def test_one_day_too_many_escalates() -> None:
    decision = review(dt.date(2026, 11, 9))
    assert decision.outcome == ExtensionOutcome.ESCALATE
    assert decision.issues == (ExtensionIssue.TOO_MANY_DAYS,)


def test_yearly_limit_escalates_and_only_counts_this_year() -> None:
    used = [dt.date(2026, 3, 2), dt.date(2026, 7, 1)]
    decision = review(dt.date(2026, 11, 5), granted_on=used)
    assert decision.issues == (ExtensionIssue.YEARLY_LIMIT_REACHED,)
    assert decision.used_this_year == 2
    last_year = [dt.date(2025, 3, 2), dt.date(2025, 7, 1)]
    assert review(dt.date(2026, 11, 5), granted_on=last_year).outcome == (
        ExtensionOutcome.WITHIN_LIMIT
    )


def test_paying_by_the_due_date_is_not_an_extension() -> None:
    decision = review(DUE, granted_on=[dt.date(2026, 1, 5)] * 5, today=dt.date(2026, 10, 28))
    assert decision.outcome == ExtensionOutcome.NOT_NEEDED


def test_date_in_the_past_escalates() -> None:
    decision = review(dt.date(2026, 11, 2), today=dt.date(2026, 11, 4))
    assert decision.issues == (ExtensionIssue.DATE_IN_PAST,)


def test_all_issues_are_listed() -> None:
    decision = review(dt.date(2026, 11, 30), granted_on=[dt.date(2026, 1, 1)] * 2)
    assert set(decision.issues) == {
        ExtensionIssue.TOO_MANY_DAYS,
        ExtensionIssue.YEARLY_LIMIT_REACHED,
    }


# -- escalation --------------------------------------------------------------------------------


@pytest.mark.parametrize(("days_after_due", "expected"), [(9, False), (10, True), (25, True)])
def test_overdue_escalates_on_the_rule_sets_day(days_after_due: int, expected: bool) -> None:
    today = DUE + dt.timedelta(days=days_after_due)
    assert (
        overdue_needs_escalation(
            oldest_due_on=DUE, today=today, escalation_days=STANDARD.escalation_days
        )
        is expected
    )


def test_active_promise_holds_the_escalation() -> None:
    today = DUE + dt.timedelta(days=15)
    kwargs = {"oldest_due_on": DUE, "today": today, "escalation_days": 10}
    assert not overdue_needs_escalation(**kwargs, active_promise_date=today)
    assert overdue_needs_escalation(**kwargs, active_promise_date=today - dt.timedelta(days=1))
    assert not overdue_needs_escalation(oldest_due_on=None, today=today, escalation_days=10)


def test_inbox_types_exist() -> None:
    assert set(INBOX_TYPE) == set(EscalationReason)
    for value in INBOX_TYPE.values():
        assert InboxItemType(value)


def test_baja_tells_the_owner_to_reach_the_tenant_directly() -> None:
    task = consent_revoked_task("Luisa Martínez")
    assert task.inbox_type == InboxItemType.CONSENT_REVOKED
    assert "ya no recibirá mensajes" in task.summary
    assert "Comunícate directamente con Luisa" in task.summary
    assert task.summary == (
        "Luisa Martínez respondió BAJA y ya no recibirá mensajes de la plataforma. "
        "Comunícate directamente con Luisa."
    )


def test_overdue_task_text() -> None:
    task = overdue_task("Pedro Gómez", D("26250.00"), 12)
    assert task.inbox_type == InboxItemType.OVERDUE_ESCALATION
    assert task.priority == 1
    assert task.summary.startswith("Pedro Gómez lleva 12 días de atraso y debe RD$26,250.")
    assert "1 día de atraso" in overdue_task("Ana", D("100"), 1).summary


def test_promise_broken_task_text() -> None:
    task = promise_broken_task("Ana Pérez", dt.date(2026, 11, 24), D("25000"))
    assert task.summary == "Ana Pérez no pagó el 24 de noviembre como prometió. Debe RD$25,000."


def test_extension_task_explains_why_it_needs_the_owner() -> None:
    decision = review(dt.date(2026, 11, 20), granted_on=[dt.date(2026, 2, 1)] * 2)
    task = extension_task("Ana Pérez", dt.date(2026, 11, 20), decision, max_days=7, max_per_year=2)
    assert task.inbox_type == InboxItemType.PROMISE_TO_APPROVE
    assert task.summary == (
        "Ana Pérez pide pagar el 20 de noviembre. Son 19 días de prórroga y tu límite es 7. "
        "Ya usó 2 de 2 prórrogas este año. ¿La apruebas?"
    )


# -- deposit (Ley 85-25) -----------------------------------------------------------------------


@pytest.mark.parametrize("deposit", ["0", "25000", "50000"])
def test_no_warning_up_to_two_months(deposit: str) -> None:
    assert deposit_warning(D(deposit), D("25000")) is None


def test_warning_above_two_months_does_not_block() -> None:
    warning = deposit_warning(D("75000"), D("25000"))
    assert warning is not None
    assert warning.months == D("3.00")
    assert warning.max_allowed == D("50000")
    assert "3 meses" in warning.message and "Ley 85-25" in warning.message
    assert "RD$50,000" in warning.message


def test_warning_with_fractional_months() -> None:
    warning = deposit_warning(D("50000.01"), D("25000"))
    assert warning is not None and "2 meses de renta" in warning.message  # 2.0000004 -> 2,00
    assert deposit_warning(D("62500"), D("25000")).message.startswith(  # type: ignore[union-attr]
        "El depósito equivale a 2,5 meses"
    )


def test_no_warning_outside_housing() -> None:
    assert deposit_warning(D("100000"), D("25000"), is_housing=False) is None


def test_deposit_needs_a_positive_rent() -> None:
    with pytest.raises(ValueError):
        deposit_warning(D("1"), D("0"))
