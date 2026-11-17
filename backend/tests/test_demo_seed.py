"""The demo data loads on any date and shows each tenant state it promises."""

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.demo import DemoAlreadySeededError, Scenario, seed_demo
from app.domain.periods import due_date
from app.domain.rule_templates import PRESETS, RuleTemplate
from app.models import (
    Building,
    InboxItem,
    Lease,
    LedgerEntry,
    Organization,
    Payment,
    PaymentPromise,
    Receipt,
    Tenant,
    Unit,
)
from app.models.enums import LedgerEntryType, PaymentStatus, PromiseStatus

GRACE_DAYS = PRESETS[RuleTemplate.STANDARD].grace_days


def count(db: Session, model, org_id) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.organization_id == org_id))


def balance(db: Session, lease_id) -> Decimal:
    return db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.lease_id == lease_id
        )
    )


def unpaid_due_date(db: Session, lease: Lease) -> dt.date:
    """Due date of the most recent rent charge (the one still owed in these scenarios)."""
    period = db.scalar(
        select(func.max(LedgerEntry.period)).where(
            LedgerEntry.lease_id == lease.id, LedgerEntry.type == LedgerEntryType.CHARGE
        )
    )
    return due_date(period, lease.due_day)


# Mid-month, first and last days of a month, end of February and a year change.
DATES = [
    dt.date(2026, 11, 17),
    dt.date(2026, 3, 1),
    dt.date(2026, 1, 31),
    dt.date(2028, 2, 29),
    dt.date(2026, 12, 30),
]


@pytest.mark.parametrize("today", DATES, ids=str)
def test_demo_has_the_promised_shape(db_session: Session, today: dt.date) -> None:
    result = seed_demo(db_session, today)
    org_id = result.organization_id

    assert count(db_session, Building, org_id) == 2
    assert count(db_session, Unit, org_id) == 7
    assert count(db_session, Tenant, org_id) == 6
    active = db_session.scalars(
        select(Lease).where(Lease.organization_id == org_id, Lease.is_active)
    ).all()
    assert len(active) == 6
    assert result.vacant_unit_id not in {lease.unit_id for lease in active}
    assert sorted(result.leases.values()) == sorted(
        [
            Scenario.PAID,
            Scenario.PAID,
            Scenario.PENDING_CONFIRMATION,
            Scenario.PROMISE,
            Scenario.UPCOMING,
            Scenario.OVERDUE,
        ]
    )


@pytest.mark.parametrize("today", DATES, ids=str)
def test_each_tenant_is_in_its_scenario(db_session: Session, today: dt.date) -> None:
    result = seed_demo(db_session, today)

    for lease_id, scenario in result.leases.items():
        lease = db_session.get(Lease, lease_id)
        owed = balance(db_session, lease_id)
        reported = db_session.scalars(
            select(Payment).where(
                Payment.lease_id == lease_id, Payment.status == PaymentStatus.REPORTED
            )
        ).all()
        promise = db_session.scalar(
            select(PaymentPromise).where(
                PaymentPromise.lease_id == lease_id,
                PaymentPromise.status == PromiseStatus.ACTIVE,
            )
        )

        if scenario == Scenario.PAID:
            assert owed == 0 and not reported and promise is None
        elif scenario == Scenario.PENDING_CONFIRMATION:
            assert len(reported) == 1 and reported[0].amount == owed > 0
            assert reported[0].ai_extracted["amount"] == str(owed)
            assert promise is None
        elif scenario == Scenario.PROMISE:
            assert owed > 0 and not reported
            assert promise is not None and promise.promised_date > today
            assert unpaid_due_date(db_session, lease) < today
        elif scenario == Scenario.UPCOMING:
            assert owed == lease.rent_amount and not reported and promise is None
            assert unpaid_due_date(db_session, lease) > today
        elif scenario == Scenario.OVERDUE:
            assert owed > lease.rent_amount  # rent plus late fee
            assert not reported and promise is None
            assert unpaid_due_date(db_session, lease) + dt.timedelta(days=GRACE_DAYS) < today


def test_receipts_are_numbered_without_gaps(db_session: Session) -> None:
    result = seed_demo(db_session, dt.date(2026, 11, 17))
    org = db_session.get(Organization, result.organization_id)

    numbers = db_session.scalars(
        select(Receipt.number).where(Receipt.organization_id == org.id).order_by(Receipt.number)
    ).all()
    confirmed = db_session.scalar(
        select(func.count()).where(
            Payment.organization_id == org.id, Payment.status == PaymentStatus.CONFIRMED
        )
    )

    assert numbers == list(range(1, confirmed + 1))
    assert org.next_receipt_number == confirmed + 1


def test_demo_inbox_has_pending_decisions(db_session: Session) -> None:
    result = seed_demo(db_session, dt.date(2026, 11, 17))
    items = db_session.scalars(
        select(InboxItem)
        .where(InboxItem.organization_id == result.organization_id)
        .order_by(InboxItem.priority)
    ).all()

    assert [item.priority for item in items] == [1, 2, 3]
    assert items[0].payment_id is not None
    assert items[2].issue_id is not None
    assert "RD$28,000" in items[0].summary


def test_demo_cannot_be_loaded_twice(db_session: Session) -> None:
    seed_demo(db_session, dt.date(2026, 11, 17))
    with pytest.raises(DemoAlreadySeededError):
        seed_demo(db_session, dt.date(2026, 11, 17))
