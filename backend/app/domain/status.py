"""Tenant status (the Home traffic light) and urgency order. Pure functions, no I/O.

Rules, evaluated in this order; the first that matches wins:

1. ``vacant``: the unit has no active lease.
2. ``pending_confirmation``: a reported payment waits for the owner's confirmation. It comes
   first because only the owner can move it forward (the AI never confirms a payment).
3. ``promise``: there is an active payment promise whose date has not passed. A promise whose
   date already passed counts as broken and no longer protects the tenant.
4. ``overdue``: an open debit's due date has passed (from the day after the due date, even
   during grace). ``in_grace`` is true while today is within the grace days of the oldest open
   debit, so the interface can show "Atrasado · en gracia". Late fees wait for grace to end.
5. ``upcoming``: there is an open debit that is not due yet.
6. ``paid``: nothing is owed.

``amount_due`` is the total of the open debits; ``days_late`` counts from the oldest open debit
that is past due (0 when nothing is late).
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.ledger import ZERO


class TenantState(StrEnum):
    OVERDUE = "overdue"
    PENDING_CONFIRMATION = "pending_confirmation"
    PROMISE = "promise"
    UPCOMING = "upcoming"
    PAID = "paid"
    VACANT = "vacant"


# Home screen order (CLAUDE.md): most urgent first.
URGENCY_ORDER: tuple[TenantState, ...] = (
    TenantState.OVERDUE,
    TenantState.PENDING_CONFIRMATION,
    TenantState.PROMISE,
    TenantState.UPCOMING,
    TenantState.PAID,
    TenantState.VACANT,
)
_RANK = {state: rank for rank, state in enumerate(URGENCY_ORDER)}


@dataclass(frozen=True)
class OpenDebit:
    due_on: dt.date
    outstanding: Decimal


@dataclass(frozen=True)
class StatusInput:
    has_active_lease: bool
    open_debits: Sequence[OpenDebit] = ()
    grace_days: int = 0
    has_reported_payment: bool = False
    # promised_date of the lease's active promise, if any.
    active_promise_date: dt.date | None = None


@dataclass(frozen=True)
class TenantStatus:
    state: TenantState
    amount_due: Decimal = ZERO
    days_late: int = 0
    in_grace: bool = False


def tenant_status(data: StatusInput, today: dt.date) -> TenantStatus:
    if not data.has_active_lease:
        return TenantStatus(TenantState.VACANT)

    open_debits = [d for d in data.open_debits if d.outstanding > 0]
    amount_due = sum((d.outstanding for d in open_debits), ZERO)
    past_due = [d.due_on for d in open_debits if d.due_on < today]
    days_late = (today - min(past_due)).days if past_due else 0

    def status(state: TenantState, in_grace: bool = False) -> TenantStatus:
        return TenantStatus(state, amount_due, days_late, in_grace)

    if data.has_reported_payment:
        return status(TenantState.PENDING_CONFIRMATION)
    if data.active_promise_date is not None and data.active_promise_date >= today:
        return status(TenantState.PROMISE)
    if past_due:
        return status(TenantState.OVERDUE, in_grace=days_late <= data.grace_days)
    if open_debits:
        return status(TenantState.UPCOMING)
    return status(TenantState.PAID)


@dataclass(frozen=True)
class HomeRow[T]:
    """A row of the Home list: its status plus whatever the caller needs to show (``item``)."""

    status: TenantStatus
    item: T
    tiebreak: str = ""  # e.g. the tenant's name, so equal rows keep a stable order


def urgency_key(row: HomeRow[object]) -> tuple[int, Decimal, int, str]:
    """State order first; within a state, larger debt first, then more days late."""
    return (_RANK[row.status.state], -row.status.amount_due, -row.status.days_late, row.tiebreak)


def sort_by_urgency[T](rows: Iterable[HomeRow[T]]) -> list[HomeRow[T]]:
    return sorted(rows, key=urgency_key)
