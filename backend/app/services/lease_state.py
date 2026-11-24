"""Load a lease's ledger, payments and promise, and run the domain on them.

Everything here is a thin adapter: the rules live in app.domain (ledger FIFO, tenant status,
contact rules). Loading is batched so the Home screen costs a fixed number of queries.
"""

import datetime as dt
import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import business_zone
from app.domain.ledger import (
    ZERO,
    Allocation,
    Credit,
    Debit,
    DebitKind,
    LeaseBalance,
    allocate,
    lease_balance,
    rent_charge,
)
from app.domain.messaging_rules import ContactRules
from app.domain.periods import add_months, period_of
from app.domain.rule_templates import PRESETS, RulePreset, RuleTemplate
from app.domain.status import OpenDebit, StatusInput, TenantStatus, tenant_status
from app.models import Lease, LedgerEntry, Payment, PaymentPromise, RuleSet
from app.models.enums import LedgerEntryType, PaymentStatus, PromiseStatus

# Without any rule set yet (onboarding not finished) the standard preset applies.
FALLBACK_PRESET = PRESETS[RuleTemplate.STANDARD]
PUNCTUALITY_MONTHS = 12


class RuleSource:
    """The organization's rule sets, to resolve which one a lease uses."""

    def __init__(self, db: Session, org_id: uuid.UUID) -> None:
        rule_sets = db.scalars(select(RuleSet).where(RuleSet.organization_id == org_id)).all()
        self.by_id = {r.id: r for r in rule_sets}
        self.default = next((r for r in rule_sets if r.is_default), None)

    def for_lease(self, lease: Lease) -> RuleSet | RulePreset:
        if lease.rule_set_id is not None and lease.rule_set_id in self.by_id:
            return self.by_id[lease.rule_set_id]
        return self.default or FALLBACK_PRESET


def contact_rules(rules: RuleSet | RulePreset) -> ContactRules:
    return ContactRules(
        contact_days=tuple(int(d) for d in rules.contact_days),
        contact_start=rules.contact_start,
        contact_end=rules.contact_end,
        max_per_day=rules.max_reminders_per_day,
        max_per_week=rules.max_reminders_per_week,
    )


class Punctuality(StrEnum):
    ON_TIME = "on_time"
    IN_GRACE = "in_grace"
    LATE = "late"
    UNPAID = "unpaid"  # past due and still owed
    PENDING = "pending"  # not due yet
    NO_CHARGE = "no_charge"


@dataclass(frozen=True)
class PeriodPunctuality:
    period: str
    result: Punctuality


@dataclass
class LeaseState:
    lease: Lease
    rules: RuleSet | RulePreset
    allocation: Allocation
    balance: LeaseBalance
    status: TenantStatus
    active_promise: PaymentPromise | None
    has_reported_payment: bool
    today: dt.date

    @property
    def grace_days(self) -> int:
        return self.rules.grace_days

    def rent_debits(self, period: str) -> list:
        return [
            s
            for s in self.allocation.debits
            if s.debit.kind == DebitKind.RENT and s.debit.period == period
        ]

    def expected_and_collected(self, period: str) -> tuple[Decimal, Decimal]:
        debits = self.rent_debits(period)
        return (
            sum((d.debit.amount for d in debits), ZERO),
            sum((d.paid for d in debits), ZERO),
        )

    def next_due_on(self) -> dt.date | None:
        open_debits = self.allocation.open_debits()
        return min((d.debit.due_on for d in open_debits), default=None)

    def punctuality(self) -> list[PeriodPunctuality]:
        last = period_of(self.today)
        periods = [add_months(last, -i) for i in range(PUNCTUALITY_MONTHS - 1, -1, -1)]
        result: list[PeriodPunctuality] = []
        for period in periods:
            debits = self.rent_debits(period)
            if not debits:
                result.append(PeriodPunctuality(period, Punctuality.NO_CHARGE))
                continue
            worst = max((self._classify(d) for d in debits), key=_PUNCTUALITY_RANK.__getitem__)
            result.append(PeriodPunctuality(period, worst))
        return result

    def _classify(self, status) -> Punctuality:
        due = status.debit.due_on
        if status.is_settled and status.settled_on is not None:
            if status.settled_on <= due:
                return Punctuality.ON_TIME
            if status.settled_on <= due + dt.timedelta(days=self.grace_days):
                return Punctuality.IN_GRACE
            return Punctuality.LATE
        return Punctuality.UNPAID if due < self.today else Punctuality.PENDING


_PUNCTUALITY_RANK = {
    Punctuality.NO_CHARGE: 0,
    Punctuality.ON_TIME: 1,
    Punctuality.PENDING: 2,
    Punctuality.IN_GRACE: 3,
    Punctuality.LATE: 4,
    Punctuality.UNPAID: 5,
}


def on_time_percent(months: Sequence[PeriodPunctuality]) -> int | None:
    counted = [
        m
        for m in months
        if m.result
        in {Punctuality.ON_TIME, Punctuality.IN_GRACE, Punctuality.LATE, Punctuality.UNPAID}
    ]
    if not counted:
        return None
    on_time = sum(1 for m in counted if m.result == Punctuality.ON_TIME)
    return round(100 * on_time / len(counted))


def _debits_and_credits(
    lease: Lease, rows: Sequence[tuple[LedgerEntry, dt.date | None]], zone_name: str
) -> tuple[list[Debit], list[Credit]]:
    zone = business_zone(zone_name)
    debits: list[Debit] = []
    credits: list[Credit] = []
    for entry, paid_on in rows:
        due = rent_charge(entry.period, lease.rent_amount, lease.due_day, lease.start_date).due_on
        key = str(entry.id)
        if entry.type == LedgerEntryType.CHARGE:
            debits.append(Debit(key, DebitKind.RENT, entry.period, due, entry.amount))
        elif entry.type == LedgerEntryType.LATE_FEE:
            debits.append(Debit(key, DebitKind.LATE_FEE, entry.period, due, entry.amount))
        elif entry.type == LedgerEntryType.ADJUSTMENT and entry.amount > 0:
            debits.append(Debit(key, DebitKind.ADJUSTMENT, entry.period, due, entry.amount))
        else:  # payment, or an adjustment in the tenant's favour
            on = paid_on or entry.created_at.astimezone(zone).date()
            credits.append(Credit(key, on, -entry.amount))
    return debits, credits


def load_lease_states(
    db: Session,
    org_id: uuid.UUID,
    leases: Sequence[Lease],
    today: dt.date,
    zone_name: str,
    rule_source: RuleSource | None = None,
) -> dict[uuid.UUID, LeaseState]:
    if not leases:
        return {}
    rules = rule_source or RuleSource(db, org_id)
    ids = [lease.id for lease in leases]

    ledger_rows = db.execute(
        select(LedgerEntry, Payment.paid_on)
        .outerjoin(Payment, Payment.id == LedgerEntry.payment_id)
        .where(LedgerEntry.organization_id == org_id, LedgerEntry.lease_id.in_(ids))
        .order_by(LedgerEntry.created_at, LedgerEntry.id)
    ).all()
    by_lease: dict[uuid.UUID, list[tuple[LedgerEntry, dt.date | None]]] = defaultdict(list)
    for entry, paid_on in ledger_rows:
        by_lease[entry.lease_id].append((entry, paid_on))

    reported = set(
        db.scalars(
            select(Payment.lease_id).where(
                Payment.organization_id == org_id,
                Payment.lease_id.in_(ids),
                Payment.status == PaymentStatus.REPORTED,
            )
        ).all()
    )
    promises = {
        p.lease_id: p
        for p in db.scalars(
            select(PaymentPromise).where(
                PaymentPromise.organization_id == org_id,
                PaymentPromise.lease_id.in_(ids),
                PaymentPromise.status == PromiseStatus.ACTIVE,
            )
        ).all()
    }

    states: dict[uuid.UUID, LeaseState] = {}
    for lease in leases:
        lease_rules = rules.for_lease(lease)
        debits, credits = _debits_and_credits(lease, by_lease.get(lease.id, []), zone_name)
        allocation = allocate(debits, credits)
        promise = promises.get(lease.id)
        status = tenant_status(
            StatusInput(
                has_active_lease=lease.is_active,
                open_debits=[
                    OpenDebit(d.debit.due_on, d.outstanding) for d in allocation.open_debits()
                ],
                grace_days=lease_rules.grace_days,
                has_reported_payment=lease.id in reported,
                active_promise_date=promise.promised_date if promise else None,
            ),
            today,
        )
        states[lease.id] = LeaseState(
            lease=lease,
            rules=lease_rules,
            allocation=allocation,
            balance=lease_balance(debits, credits),
            status=status,
            active_promise=promise,
            has_reported_payment=lease.id in reported,
            today=today,
        )
    return states


def load_lease_state(db: Session, lease: Lease, today: dt.date, zone_name: str) -> LeaseState:
    return load_lease_states(db, lease.organization_id, [lease], today, zone_name)[lease.id]
