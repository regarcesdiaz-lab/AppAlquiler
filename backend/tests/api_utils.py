"""Helpers for API tests: signed-in owners and a small portfolio per organization."""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.core.timezone import local_today, utc_now
from app.models import (
    BankAccount,
    Building,
    ConsentRecord,
    InboxItem,
    Lease,
    Organization,
    Payment,
    Tenant,
    Unit,
    User,
)
from app.models.enums import (
    AccountType,
    AuthMethod,
    ConsentMethod,
    InboxItemType,
    PaymentSource,
    PaymentStatus,
)
from app.services.auth import start_app_session
from tests.factories import add, make_charge, make_org, make_rule_set, make_user

PASSWORD = "una-clave-larga-segura"


@dataclass
class Owner:
    org: Organization
    user: User
    headers: dict[str, str]


def make_owner(db: Session, *, with_password: bool = False) -> Owner:
    """An organization with its owner and a bearer access token (no HTTP round trip)."""
    org = make_org(db)
    user = make_user(db, org, password_hash=hash_password(PASSWORD) if with_password else None)
    tokens = start_app_session(db, user, AuthMethod.PASSWORD, utc_now())
    db.flush()
    return Owner(org, user, {"Authorization": f"Bearer {tokens.access_token}"})


@dataclass
class Portfolio:
    building: Building
    unit: Unit
    vacant_unit: Unit
    tenant: Tenant
    lease: Lease
    payment: Payment
    inbox_item: InboxItem
    bank_account: BankAccount


def make_portfolio(db: Session, org: Organization, *, phone: str = "+18095550101") -> Portfolio:
    """One building with an occupied and a vacant unit, a lease owing this month, a reported
    payment waiting for confirmation and its inbox item."""
    make_rule_set(db, org, is_default=True)
    building = add(db, Building(organization_id=org.id, name="Torre Prueba"))
    unit = add(db, Unit(organization_id=org.id, building_id=building.id, code="1A"))
    vacant = add(db, Unit(organization_id=org.id, building_id=building.id, code="1B"))
    tenant = add(db, Tenant(organization_id=org.id, full_name="María Fernández", phone=phone))
    today = local_today()
    lease = add(
        db,
        Lease(
            organization_id=org.id,
            unit_id=unit.id,
            tenant_id=tenant.id,
            rent_amount=Decimal("25000.00"),
            due_day=1,
            late_fee_agreed=True,
            start_date=today.replace(day=1) - dt.timedelta(days=60),
        ),
    )
    add(
        db,
        ConsentRecord(
            organization_id=org.id,
            lease_id=lease.id,
            tenant_id=tenant.id,
            method=ConsentMethod.CONTRACT_CLAUSE,
            evidence="Cláusula de prueba",
            granted_at=dt.datetime(2020, 1, 1, tzinfo=dt.UTC),  # before any frozen test clock
        ),
    )
    make_charge(db, lease, period=f"{today.year:04d}-{today.month:02d}")
    payment = add(
        db,
        Payment(
            organization_id=org.id,
            lease_id=lease.id,
            amount=Decimal("25000.00"),
            paid_on=today,
            status=PaymentStatus.REPORTED,
            source=PaymentSource.WHATSAPP_RECEIPT,
            file_key="test/receipt.jpg",
        ),
    )
    inbox_item = add(
        db,
        InboxItem(
            organization_id=org.id,
            lease_id=lease.id,
            payment_id=payment.id,
            type=InboxItemType.PAYMENT_TO_CONFIRM,
            summary="Confirma el pago",
            priority=1,
        ),
    )
    bank = add(
        db,
        BankAccount(
            organization_id=org.id,
            bank_name="Banco Popular",
            account_type=AccountType.SAVINGS,
            account_number="7801234567",
            holder_name="Dueña",
        ),
    )
    return Portfolio(building, unit, vacant, tenant, lease, payment, inbox_item, bank)


def lease_payload(unit_id, **overrides) -> dict:
    payload = {
        "unit_id": str(unit_id),
        "tenant": {"full_name": "José Martínez", "phone": "829-555-0102"},
        "rent_amount": "22000.00",
        "due_day": 5,
        "deposit_amount": "44000.00",
        "late_fee_agreed": False,
        "start_date": "2026-01-01",
        "consent_confirmed": True,
    }
    payload.update(overrides)
    return payload


def ok(client: TestClient, method: str, url: str, headers: dict, **kwargs):
    response = client.request(method, url, headers=headers, **kwargs)
    assert response.status_code < 300, response.text
    return response
