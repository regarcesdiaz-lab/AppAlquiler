"""Tenant status (traffic light) and urgency order for Home."""

import datetime as dt
from decimal import Decimal

import pytest

from app.domain.status import (
    URGENCY_ORDER,
    HomeRow,
    OpenDebit,
    StatusInput,
    TenantState,
    TenantStatus,
    sort_by_urgency,
    tenant_status,
)

D = Decimal
TODAY = dt.date(2026, 11, 20)


def debit(days_from_today: int, amount: str = "25000") -> OpenDebit:
    return OpenDebit(TODAY + dt.timedelta(days=days_from_today), D(amount))


def status(**kwargs) -> TenantStatus:
    kwargs.setdefault("has_active_lease", True)
    return tenant_status(StatusInput(**kwargs), TODAY)


def test_urgency_order_matches_claude_md() -> None:
    assert [s.value for s in URGENCY_ORDER] == [
        "overdue",
        "pending_confirmation",
        "promise",
        "upcoming",
        "paid",
        "vacant",
    ]


def test_vacant_without_active_lease() -> None:
    assert status(has_active_lease=False, open_debits=[debit(-5)]).state == TenantState.VACANT


def test_paid_when_nothing_is_owed() -> None:
    result = status(open_debits=[debit(-5, "0")])
    assert result == TenantStatus(TenantState.PAID, D("0"), 0, False)


def test_upcoming_when_the_charge_is_not_due_yet() -> None:
    result = status(open_debits=[debit(3)])
    assert (result.state, result.amount_due, result.days_late) == (
        TenantState.UPCOMING,
        D("25000"),
        0,
    )


def test_due_today_is_still_upcoming() -> None:
    assert status(open_debits=[debit(0)]).state == TenantState.UPCOMING


def test_overdue_from_the_day_after_the_due_date_marked_in_grace() -> None:
    result = status(open_debits=[debit(-1)], grace_days=3)
    assert (result.state, result.days_late, result.in_grace) == (TenantState.OVERDUE, 1, True)


def test_last_day_of_grace_is_still_in_grace() -> None:
    assert status(open_debits=[debit(-3)], grace_days=3).in_grace is True
    after = status(open_debits=[debit(-4)], grace_days=3)
    assert (after.state, after.in_grace) == (TenantState.OVERDUE, False)


def test_no_grace_days_means_never_in_grace() -> None:
    assert status(open_debits=[debit(-1)], grace_days=0).in_grace is False


def test_overdue_counts_from_the_oldest_open_debit_and_adds_all_debt() -> None:
    result = status(open_debits=[debit(-35), debit(-5), debit(10)], grace_days=3)
    assert (result.state, result.days_late, result.amount_due) == (
        TenantState.OVERDUE,
        35,
        D("75000"),
    )


def test_reported_payment_wins_over_overdue_and_promise() -> None:
    result = status(
        open_debits=[debit(-10)],
        has_reported_payment=True,
        active_promise_date=TODAY + dt.timedelta(days=2),
    )
    assert result.state == TenantState.PENDING_CONFIRMATION
    assert result.days_late == 10  # still shown, for ordering


def test_active_promise_wins_over_overdue() -> None:
    result = status(open_debits=[debit(-10)], active_promise_date=TODAY + dt.timedelta(days=2))
    assert result.state == TenantState.PROMISE


def test_promise_due_today_is_still_a_promise() -> None:
    assert status(open_debits=[debit(-10)], active_promise_date=TODAY).state == TenantState.PROMISE


def test_promise_date_passed_falls_back_to_overdue() -> None:
    result = status(open_debits=[debit(-10)], active_promise_date=TODAY - dt.timedelta(days=1))
    assert result.state == TenantState.OVERDUE


def test_reported_payment_with_nothing_owed_is_still_pending_confirmation() -> None:
    assert status(has_reported_payment=True).state == TenantState.PENDING_CONFIRMATION


# -- urgency order -----------------------------------------------------------------------------


def row(name: str, state: TenantState, amount: str = "0", days: int = 0) -> HomeRow[str]:
    return HomeRow(TenantStatus(state, D(amount), days), name, tiebreak=name)


def test_sort_by_state_then_amount_then_days_late() -> None:
    rows = [
        row("vacío", TenantState.VACANT),
        row("al día", TenantState.PAID),
        row("pronto", TenantState.UPCOMING, "25000"),
        row("promesa", TenantState.PROMISE, "20000", 5),
        row("comprobante", TenantState.PENDING_CONFIRMATION, "25000", 2),
        row("atraso chico", TenantState.OVERDUE, "10000", 40),
        row("atraso grande", TenantState.OVERDUE, "50000", 3),
        row("atraso grande y viejo", TenantState.OVERDUE, "50000", 20),
    ]
    ordered = [r.item for r in sort_by_urgency(reversed(rows))]
    assert ordered == [
        "atraso grande y viejo",
        "atraso grande",
        "atraso chico",
        "comprobante",
        "promesa",
        "pronto",
        "al día",
        "vacío",
    ]


def test_ties_keep_a_stable_order_by_tiebreak() -> None:
    rows = [row("Pedro", TenantState.PAID), row("Ana", TenantState.PAID)]
    assert [r.item for r in sort_by_urgency(rows)] == ["Ana", "Pedro"]


@pytest.mark.parametrize("state", list(TenantState))
def test_every_state_has_a_place_in_the_order(state: TenantState) -> None:
    assert state in URGENCY_ORDER
