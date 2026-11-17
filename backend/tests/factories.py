"""Minimal builders for model tests. Each helper adds and flushes, so ids are available."""

import datetime as dt
import itertools
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    Lease,
    LedgerEntry,
    Organization,
    Payment,
    Tenant,
    TimelineEvent,
    Unit,
    User,
)
from app.models.enums import ActorType, LedgerEntryType, PaymentSource

_counter = itertools.count(1)


def _next() -> int:
    return next(_counter)


def add[T](db: Session, obj: T) -> T:
    db.add(obj)
    db.flush()
    return obj


def make_org(db: Session, name: str = "Organización de prueba") -> Organization:
    return add(db, Organization(name=f"{name} {_next()}"))


def make_user(db: Session, org: Organization, **kwargs: Any) -> User:
    n = _next()
    return add(
        db,
        User(
            organization_id=org.id,
            email=kwargs.pop("email", f"owner{n}@example.com"),
            full_name=kwargs.pop("full_name", f"Propietario {n}"),
            **kwargs,
        ),
    )


def make_unit(db: Session, org: Organization, **kwargs: Any) -> Unit:
    return add(db, Unit(organization_id=org.id, code=kwargs.pop("code", f"U{_next()}"), **kwargs))


def make_tenant(db: Session, org: Organization, **kwargs: Any) -> Tenant:
    n = _next()
    return add(
        db,
        Tenant(
            organization_id=org.id,
            full_name=kwargs.pop("full_name", f"Inquilino {n}"),
            phone=kwargs.pop("phone", f"+1809{n:07d}"),
            **kwargs,
        ),
    )


def make_lease(db: Session, org: Organization, **kwargs: Any) -> Lease:
    unit_id = kwargs.pop("unit_id", None) or make_unit(db, org).id
    tenant_id = kwargs.pop("tenant_id", None) or make_tenant(db, org).id
    return add(
        db,
        Lease(
            organization_id=kwargs.pop("organization_id", org.id),
            unit_id=unit_id,
            tenant_id=tenant_id,
            rent_amount=kwargs.pop("rent_amount", Decimal("25000.00")),
            due_day=kwargs.pop("due_day", 1),
            start_date=kwargs.pop("start_date", dt.date(2026, 1, 1)),
            **kwargs,
        ),
    )


def make_charge(db: Session, lease: Lease, period: str = "2026-11", **kwargs: Any) -> LedgerEntry:
    return add(
        db,
        LedgerEntry(
            organization_id=lease.organization_id,
            lease_id=lease.id,
            type=kwargs.pop("type", LedgerEntryType.CHARGE),
            amount=kwargs.pop("amount", lease.rent_amount),
            period=period,
            **kwargs,
        ),
    )


def make_payment(db: Session, lease: Lease, **kwargs: Any) -> Payment:
    return add(
        db,
        Payment(
            organization_id=lease.organization_id,
            lease_id=lease.id,
            amount=kwargs.pop("amount", lease.rent_amount),
            paid_on=kwargs.pop("paid_on", dt.date(2026, 11, 1)),
            source=kwargs.pop("source", PaymentSource.MANUAL_CASH),
            **kwargs,
        ),
    )


def make_event(db: Session, lease: Lease, event_type: str = "test.event") -> TimelineEvent:
    return add(
        db,
        TimelineEvent(
            organization_id=lease.organization_id,
            lease_id=lease.id,
            unit_id=lease.unit_id,
            event_type=event_type,
            actor_type=ActorType.SYSTEM,
            payload={"n": _next()},
        ),
    )
