"""Ledger math: monthly rent charges, FIFO payment allocation, late fees and balances.

Pure functions over plain values (no database, no I/O). Money is always ``Decimal`` rounded
to cents; floats are rejected.

Rules
-----
* **Rent charges.** One charge per calendar month ("YYYY-MM") of the lease, due on the lease's
  ``due_day`` (clamped to the last day of short months, see ``periods.due_date``). The first
  period is the month the lease starts; if its due day falls before the start date, that first
  charge is due on the start date. Periods after the lease's end date are not charged. There is
  no proration in v1: every charge is the full monthly rent.
* **FIFO.** Credits (payments and negative adjustments) pay the oldest debit first, ordered by
  due date, then rent before late fee on the same date, then the debit's key. Credits are
  applied in date order, so each debit knows the date it was fully settled. Whatever is left
  after every debit is paid is a credit balance in the tenant's favour (excedente).
* **Late fees.** Only when the lease says so (``late_fee_agreed``) and the rule set has one.
  The fee starts the day after the grace period ends (``due + grace_days + 1``) and stops on
  the day the charge is fully paid (that day still counts). "once" posts the whole fee; "daily"
  adds the fee for each day. The total per charge is capped by the rule set's cap and, always,
  by the platform cap (10 % of the rent).
"""

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from app.domain.periods import add_months, due_date, period_of
from app.domain.rule_templates import (
    PLATFORM_LATE_FEE_CAP_PERCENT,
    LateFeeFrequency,
    LateFeeType,
)

CENT = Decimal("0.01")
ZERO = Decimal("0.00")
HUNDRED = Decimal(100)


def money(value: Decimal | int | str) -> Decimal:
    """Round to cents (half up). Floats are refused: they cannot hold money exactly."""
    if isinstance(value, float):
        raise TypeError("Money must be Decimal, int or str, never float")
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def percent_of(amount: Decimal, percent: Decimal) -> Decimal:
    return money(amount * percent / HUNDRED)


# -- rent charges ------------------------------------------------------------------------------


@dataclass(frozen=True)
class RentCharge:
    period: str
    due_on: dt.date
    amount: Decimal


def rent_charge(
    period: str, rent_amount: Decimal, due_day: int, lease_start: dt.date | None = None
) -> RentCharge:
    """The rent charge of one period. On the lease's first month, never due before it starts."""
    if rent_amount <= 0:
        raise ValueError("Rent must be positive")
    due = due_date(period, due_day)
    if lease_start is not None and period == period_of(lease_start):
        due = max(due, lease_start)
    return RentCharge(period=period, due_on=due, amount=money(rent_amount))


def rent_charges_through(
    *,
    rent_amount: Decimal,
    due_day: int,
    lease_start: dt.date,
    lease_end: dt.date | None,
    through: dt.date,
) -> list[RentCharge]:
    """Every rent charge from the lease's first month to the month of ``through`` (inclusive)."""
    last = period_of(through)
    if lease_end is not None:
        last = min(last, period_of(lease_end))
    charges: list[RentCharge] = []
    period = period_of(lease_start)
    while period <= last:
        charges.append(rent_charge(period, rent_amount, due_day, lease_start))
        period = add_months(period, 1)
    return charges


# -- FIFO allocation ---------------------------------------------------------------------------


class DebitKind(StrEnum):
    RENT = "rent"
    LATE_FEE = "late_fee"
    ADJUSTMENT = "adjustment"


_KIND_ORDER = {DebitKind.RENT: 0, DebitKind.LATE_FEE: 1, DebitKind.ADJUSTMENT: 2}


@dataclass(frozen=True)
class Debit:
    """Something the tenant owes: a rent charge, a late fee or a positive adjustment."""

    key: str
    kind: DebitKind
    period: str
    due_on: dt.date
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError(f"Debit {self.key} must be positive")


@dataclass(frozen=True)
class Credit:
    """Money in the tenant's favour: a confirmed payment or a negative adjustment."""

    key: str
    on: dt.date
    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise ValueError(f"Credit {self.key} must be positive")


@dataclass(frozen=True)
class Application:
    credit_key: str
    debit_key: str
    amount: Decimal


@dataclass(frozen=True)
class DebitStatus:
    debit: Debit
    paid: Decimal
    settled_on: dt.date | None  # date of the credit that paid it in full

    @property
    def outstanding(self) -> Decimal:
        return self.debit.amount - self.paid

    @property
    def is_settled(self) -> bool:
        return self.outstanding == ZERO


@dataclass(frozen=True)
class Allocation:
    debits: list[DebitStatus]
    applications: list[Application]
    unapplied_credit: Decimal  # excedente: credit balance in the tenant's favour

    @property
    def outstanding(self) -> Decimal:
        return sum((d.outstanding for d in self.debits), ZERO)

    def open_debits(self) -> list[DebitStatus]:
        return [d for d in self.debits if not d.is_settled]

    def status_of(self, debit_key: str) -> DebitStatus:
        for status in self.debits:
            if status.debit.key == debit_key:
                return status
        raise KeyError(debit_key)


def fifo_order(debits: Iterable[Debit]) -> list[Debit]:
    return sorted(debits, key=lambda d: (d.due_on, _KIND_ORDER[d.kind], d.key))


def allocate(debits: Iterable[Debit], credits: Iterable[Credit]) -> Allocation:
    """Apply credits to debits, oldest debit first (FIFO)."""
    ordered = fifo_order(debits)
    paid = {d.key: ZERO for d in ordered}
    settled_on: dict[str, dt.date] = {}
    applications: list[Application] = []
    index = 0
    leftover = ZERO
    for credit in sorted(credits, key=lambda c: (c.on, c.key)):
        remaining = money(credit.amount)
        while remaining > 0 and index < len(ordered):
            debit = ordered[index]
            take = min(remaining, debit.amount - paid[debit.key])
            paid[debit.key] += take
            remaining -= take
            applications.append(Application(credit.key, debit.key, take))
            if paid[debit.key] == debit.amount:
                settled_on[debit.key] = credit.on
                index += 1
        leftover += remaining
    statuses = [DebitStatus(d, paid[d.key], settled_on.get(d.key)) for d in ordered]
    return Allocation(debits=statuses, applications=applications, unapplied_credit=leftover)


# -- late fees ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LateFeeRules:
    grace_days: int
    fee_type: LateFeeType
    value: Decimal
    frequency: LateFeeFrequency = LateFeeFrequency.ONCE
    cap_percent: Decimal | None = None

    def __post_init__(self) -> None:
        if self.grace_days < 0:
            raise ValueError("grace_days must be >= 0")
        if self.fee_type != LateFeeType.NONE and self.value <= 0:
            raise ValueError("A late fee needs a positive value")


def first_late_fee_day(due_on: dt.date, grace_days: int) -> dt.date:
    return due_on + dt.timedelta(days=grace_days + 1)


def late_fee_cap(rent_amount: Decimal, cap_percent: Decimal | None) -> Decimal:
    """Maximum total fee per charge: the rule set's cap, never above the platform cap."""
    percent = PLATFORM_LATE_FEE_CAP_PERCENT
    if cap_percent is not None:
        percent = min(percent, cap_percent)
    return percent_of(rent_amount, percent)


def late_fee_for_charge(
    *,
    rent_amount: Decimal,
    due_on: dt.date,
    rules: LateFeeRules,
    agreed: bool,
    as_of: dt.date,
    settled_on: dt.date | None = None,
) -> Decimal:
    """Total late fee owed for one rent charge as of a date (not what is left to post)."""
    if not agreed or rules.fee_type == LateFeeType.NONE:
        return ZERO
    start = first_late_fee_day(due_on, rules.grace_days)
    end = as_of if settled_on is None else min(as_of, settled_on)
    if end < start:
        return ZERO
    unit = (
        percent_of(rent_amount, rules.value)
        if rules.fee_type == LateFeeType.PERCENT
        else money(rules.value)
    )
    days = 1 if rules.frequency == LateFeeFrequency.ONCE else (end - start).days + 1
    return min(money(unit * days), late_fee_cap(rent_amount, rules.cap_percent))


def late_fee_to_post(total_owed: Decimal, already_posted: Decimal) -> Decimal:
    """What the daily job still has to post for a charge (never negative)."""
    return max(ZERO, money(total_owed - already_posted))


# -- balances ----------------------------------------------------------------------------------


def balance(debits: Iterable[Debit], credits: Iterable[Credit]) -> Decimal:
    """Contract balance: positive means the tenant owes, negative is a credit in their favour."""
    owed = sum((d.amount for d in debits), ZERO)
    paid = sum((c.amount for c in credits), ZERO)
    return money(owed - paid)


@dataclass(frozen=True)
class PeriodBalance:
    period: str
    charged: Decimal
    paid: Decimal

    @property
    def outstanding(self) -> Decimal:
        return self.charged - self.paid


@dataclass(frozen=True)
class LeaseBalance:
    total: Decimal  # same as balance(): owed minus paid
    by_period: list[PeriodBalance] = field(default_factory=list)
    credit_in_favour: Decimal = ZERO

    def period(self, period: str) -> PeriodBalance:
        for item in self.by_period:
            if item.period == period:
                return item
        return PeriodBalance(period, ZERO, ZERO)


def lease_balance(debits: Sequence[Debit], credits: Sequence[Credit]) -> LeaseBalance:
    """Balance per period after FIFO allocation, plus the contract total."""
    allocation = allocate(debits, credits)
    charged: dict[str, Decimal] = {}
    paid: dict[str, Decimal] = {}
    for status in allocation.debits:
        period = status.debit.period
        charged[period] = charged.get(period, ZERO) + status.debit.amount
        paid[period] = paid.get(period, ZERO) + status.paid
    by_period = [PeriodBalance(p, charged[p], paid[p]) for p in sorted(charged)]
    return LeaseBalance(
        total=balance(debits, credits),
        by_period=by_period,
        credit_in_favour=allocation.unapplied_credit,
    )
