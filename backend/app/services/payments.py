"""Owner actions on payments: confirm, reject, record cash. Only a user confirms (rule 3)."""

import datetime as dt
import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.timezone import local_today, utc_now
from app.domain.ledger import ZERO
from app.domain.periods import period_of
from app.models import InboxItem, Lease, LedgerEntry, Organization, Payment, PaymentPromise
from app.models.enums import (
    InboxItemStatus,
    LedgerEntryType,
    PaymentSource,
    PaymentStatus,
    PromiseStatus,
)
from app.services.lease_state import load_lease_state
from app.services.timeline import record_event


def _post_payment(db: Session, lease: Lease, payment: Payment, zone: str) -> None:
    """Ledger credit for a confirmed payment, labelled with the oldest period still owed."""
    state = load_lease_state(db, lease, local_today(zone), zone)
    open_debits = state.allocation.open_debits()
    period = open_debits[0].debit.period if open_debits else period_of(payment.paid_on)
    db.add(
        LedgerEntry(
            organization_id=lease.organization_id,
            lease_id=lease.id,
            payment_id=payment.id,
            type=LedgerEntryType.PAYMENT,
            amount=-payment.amount,
            period=period,
            reference=f"Pago {period}",
        )
    )
    db.flush()


def _resolve_payment_inbox(db: Session, payment: Payment, now: dt.datetime) -> None:
    db.execute(
        update(InboxItem)
        .where(InboxItem.payment_id == payment.id, InboxItem.status == InboxItemStatus.OPEN)
        .values(status=InboxItemStatus.RESOLVED, resolved_at=now, updated_at=now)
    )


def _close_kept_promise(db: Session, lease: Lease, zone: str, user_id: uuid.UUID) -> None:
    """An active promise is kept once nothing is owed any more."""
    promise = db.scalar(
        select(PaymentPromise).where(
            PaymentPromise.lease_id == lease.id, PaymentPromise.status == PromiseStatus.ACTIVE
        )
    )
    if promise is None:
        return
    state = load_lease_state(db, lease, local_today(zone), zone)
    if state.allocation.outstanding > ZERO:
        return
    promise.status = PromiseStatus.KEPT
    db.flush()
    record_event(
        db,
        lease.organization_id,
        "promise.kept",
        lease=lease,
        user_id=user_id,
        promise_id=promise.id,
    )


def confirm_payment(
    db: Session, payment: Payment, lease: Lease, org: Organization, user_id: uuid.UUID
) -> None:
    now = utc_now()
    payment.status = PaymentStatus.CONFIRMED
    payment.confirmed_by_user_id = user_id
    payment.confirmed_at = now
    db.flush()
    _post_payment(db, lease, payment, org.timezone)
    _resolve_payment_inbox(db, payment, now)
    record_event(
        db,
        org.id,
        "payment.confirmed",
        lease=lease,
        user_id=user_id,
        payment_id=payment.id,
        amount=payment.amount,
    )
    _close_kept_promise(db, lease, org.timezone, user_id)


def reject_payment(
    db: Session, payment: Payment, lease: Lease, reason: str, user_id: uuid.UUID
) -> None:
    now = utc_now()
    payment.status = PaymentStatus.REJECTED
    db.flush()
    _resolve_payment_inbox(db, payment, now)
    record_event(
        db,
        lease.organization_id,
        "payment.rejected",
        lease=lease,
        user_id=user_id,
        payment_id=payment.id,
        amount=payment.amount,
        reason=reason,
    )


def record_cash(
    db: Session,
    lease: Lease,
    org: Organization,
    *,
    amount: Decimal,
    paid_on: dt.date,
    note: str | None,
    user_id: uuid.UUID,
) -> Payment:
    now = utc_now()
    payment = Payment(
        organization_id=lease.organization_id,
        lease_id=lease.id,
        amount=amount,
        paid_on=paid_on,
        reference=note,
        status=PaymentStatus.CONFIRMED,
        source=PaymentSource.MANUAL_CASH,
        confirmed_by_user_id=user_id,
        confirmed_at=now,
    )
    db.add(payment)
    db.flush()
    _post_payment(db, lease, payment, org.timezone)
    record_event(
        db,
        org.id,
        "payment.cash_recorded",
        lease=lease,
        user_id=user_id,
        payment_id=payment.id,
        amount=amount,
        paid_on=paid_on,
    )
    _close_kept_promise(db, lease, org.timezone, user_id)
    return payment
