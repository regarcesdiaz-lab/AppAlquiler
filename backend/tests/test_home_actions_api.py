"""Home screen on the demo data, and the owner's actions: confirm, reject, cash, reminder, inbox."""

import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timezone import local_today, utc_now
from app.demo import seed_demo
from app.domain.periods import period_of
from app.models import (
    ConsentRecord,
    InboxItem,
    Lease,
    LedgerEntry,
    Message,
    Payment,
    PaymentPromise,
    TimelineEvent,
    User,
)
from app.models.enums import (
    AuthMethod,
    InboxItemStatus,
    LedgerEntryType,
    MessageStatus,
    PaymentStatus,
    PromiseCreator,
    PromiseStatus,
)
from app.services.auth import start_app_session
from tests.api_utils import make_owner, make_portfolio, ok
from tests.factories import add

SD = ZoneInfo("America/Santo_Domingo")
MONDAY_10AM = dt.datetime(2026, 11, 23, 10, 0, tzinfo=SD)
SUNDAY_10AM = dt.datetime(2026, 11, 22, 10, 0, tzinfo=SD)
MONDAY_8PM = dt.datetime(2026, 11, 23, 20, 0, tzinfo=SD)
HOLIDAY_10AM = dt.datetime(2026, 11, 9, 10, 0, tzinfo=SD)


def events(db: Session, lease_id) -> list[str]:
    return list(
        db.scalars(select(TimelineEvent.event_type).where(TimelineEvent.lease_id == lease_id))
    )


# -- home --------------------------------------------------------------------------------------


@pytest.fixture
def demo(client: TestClient, db_session: Session):

    result = seed_demo(db_session, local_today())
    user = db_session.get(User, result.owner_id)
    tokens = start_app_session(db_session, user, AuthMethod.PASSWORD, utc_now())
    return result, {"Authorization": f"Bearer {tokens.access_token}"}


def test_home_counts_order_and_money(client: TestClient, db_session: Session, demo) -> None:
    result, headers = demo
    body = ok(client, "GET", "/v1/home", headers).json()
    assert body["period"] == period_of(local_today())
    assert body["counts"] == {
        "overdue": 1,
        "pending_confirmation": 1,
        "promise": 1,
        "upcoming": 1,
        "paid": 2,
        "vacant": 1,
    }
    states = [row["state"] for row in body["rows"]]
    order = ["overdue", "pending_confirmation", "promise", "upcoming", "paid", "vacant"]
    assert states == sorted(states, key=order.index)
    overdue = body["rows"][0]
    assert overdue["in_grace"] is False and overdue["days_late"] == 12

    period = body["period"]
    expected = db_session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(Lease, Lease.id == LedgerEntry.lease_id)
        .where(
            LedgerEntry.organization_id == result.organization_id,
            LedgerEntry.type == LedgerEntryType.CHARGE,
            LedgerEntry.period == period,
            Lease.is_active,
        )
    )
    assert Decimal(body["expected"]) == expected
    assert Decimal("0") < Decimal(body["collected"]) <= Decimal(body["expected"])
    assert len(body["inbox"]) == 3
    assert [i["priority"] for i in body["inbox"]] == sorted(i["priority"] for i in body["inbox"])


def test_home_filters_and_search(client: TestClient, db_session: Session, demo) -> None:
    _, headers = demo
    overdue = ok(client, "GET", "/v1/home?state=overdue", headers).json()["rows"]
    assert [r["tenant_name"] for r in overdue] == ["Pedro Almonte"]
    search = ok(client, "GET", "/v1/home?q=palmas", headers).json()["rows"]
    assert len(search) == 4 and {r["building_name"] for r in search} == {"Residencial Las Palmas"}
    by_phone = ok(client, "GET", "/v1/home?q=5550105", headers).json()["rows"]
    assert [r["tenant_name"] for r in by_phone] == ["Rosa Jiménez"]
    # Summary numbers ignore the list filters.
    assert ok(client, "GET", "/v1/home?state=paid", headers).json()["counts"]["overdue"] == 1


def test_home_marks_overdue_in_grace(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    portfolio = make_portfolio(db_session, owner.org)
    portfolio.payment.status = PaymentStatus.REJECTED
    lease = portfolio.lease
    lease.due_day = max(1, local_today().day - 1)
    if lease.due_day >= local_today().day:  # first of the month: nothing can be late yet
        pytest.skip("needs a day after the 1st")
    db_session.flush()
    row = ok(client, "GET", "/v1/home?state=overdue", owner.headers).json()["rows"][0]
    assert row["in_grace"] is True and row["days_late"] == 1


# -- confirm / reject / cash -------------------------------------------------------------------


def test_confirm_payment_posts_ledger_resolves_inbox_and_cannot_repeat(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    body = ok(client, "POST", f"/v1/payments/{p.payment.id}/confirm", owner.headers).json()
    assert body["status"] == "confirmed"
    db_session.refresh(p.payment)
    assert p.payment.confirmed_by_user_id == owner.user.id
    balance = db_session.scalar(
        select(func.sum(LedgerEntry.amount)).where(LedgerEntry.lease_id == p.lease.id)
    )
    assert balance == Decimal("0.00")
    db_session.refresh(p.inbox_item)
    assert p.inbox_item.status == InboxItemStatus.RESOLVED
    assert "payment.confirmed" in events(db_session, p.lease.id)
    again = client.post(f"/v1/payments/{p.payment.id}/confirm", headers=owner.headers)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "payment_not_reported"
    detail = ok(client, "GET", f"/v1/leases/{p.lease.id}", owner.headers).json()
    assert detail["status"]["state"] == "paid"


def test_confirming_the_full_balance_keeps_the_promise(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    promise = add(
        db_session,
        PaymentPromise(
            organization_id=owner.org.id,
            lease_id=p.lease.id,
            promised_date=local_today() + dt.timedelta(days=3),
            created_by=PromiseCreator.TENANT,
        ),
    )
    ok(client, "POST", f"/v1/payments/{p.payment.id}/confirm", owner.headers)
    db_session.refresh(promise)
    assert promise.status == PromiseStatus.KEPT
    assert "promise.kept" in events(db_session, p.lease.id)


def test_reject_payment(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    assert (
        client.post(
            f"/v1/payments/{p.payment.id}/reject", headers=owner.headers, json={"reason": ""}
        ).status_code
        == 422
    )
    ok(
        client,
        "POST",
        f"/v1/payments/{p.payment.id}/reject",
        owner.headers,
        json={"reason": "El monto no coincide con el banco."},
    )
    db_session.refresh(p.payment)
    db_session.refresh(p.inbox_item)
    assert p.payment.status == PaymentStatus.REJECTED
    assert p.inbox_item.status == InboxItemStatus.RESOLVED
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.payment_id == p.payment.id)
        )
        == 0
    )


def test_record_cash_payment(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    body = ok(
        client,
        "POST",
        f"/v1/leases/{p.lease.id}/payments/cash",
        owner.headers,
        json={"amount": "10000.00", "paid_on": local_today().isoformat(), "note": "Recibo 15"},
    ).json()
    payment = db_session.get(Payment, body["payment_id"])
    assert (
        payment.status == PaymentStatus.CONFIRMED and payment.confirmed_by_user_id == owner.user.id
    )
    assert "payment.cash_recorded" in events(db_session, p.lease.id)
    future = client.post(
        f"/v1/leases/{p.lease.id}/payments/cash",
        headers=owner.headers,
        json={"amount": "1.00", "paid_on": (local_today() + dt.timedelta(days=2)).isoformat()},
    )
    assert future.status_code == 422


# -- reminder now ------------------------------------------------------------------------------


def portfolio_without_pending_payment(client: TestClient, db_session: Session, owner):
    """The portfolio's reported payment, rejected: the rent is owed and nothing waits."""
    p = make_portfolio(db_session, owner.org)
    ok(
        client,
        "POST",
        f"/v1/payments/{p.payment.id}/reject",
        owner.headers,
        json={"reason": "No aparece en el banco."},
    )
    return p


def test_reminder_is_blocked_while_a_reported_payment_waits(
    client: TestClient, db_session: Session, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)  # its payment is reported, not confirmed
    response = client.post(f"/v1/leases/{p.lease.id}/reminders", headers=owner.headers)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "reminder_blocked"
    assert detail["reasons"] == ["pending_payment"]
    assert detail["message"] == (
        "María Fernández envió un comprobante. "
        "Confírmalo o recházalo antes de enviarle un recordatorio."
    )
    assert detail["next_contact_time"] is None
    assert db_session.scalar(select(func.count()).select_from(Message)) == 0


def test_reminder_is_sent_through_the_simulator(
    client: TestClient, db_session: Session, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    p = portfolio_without_pending_payment(client, db_session, owner)
    body = ok(client, "POST", f"/v1/leases/{p.lease.id}/reminders", owner.headers).json()
    assert body["status"] == "sent"
    assert "RD$25,000" in body["body"]
    message = db_session.get(Message, body["message_id"])
    assert message.status == MessageStatus.SENT and message.provider_message_id.startswith(
        "wamid.SIM"
    )
    assert {"message.queued", "message.sent"} <= set(events(db_session, p.lease.id))

    # Default rules: at most one collection reminder per day.
    second = client.post(f"/v1/leases/{p.lease.id}/reminders", headers=owner.headers)
    assert second.status_code == 409
    assert second.json()["detail"]["reasons"] == ["daily_limit"]


@pytest.mark.parametrize(
    ("when", "reason"),
    [(SUNDAY_10AM, "sunday"), (MONDAY_8PM, "outside_hours"), (HOLIDAY_10AM, "holiday")],
)
def test_reminder_respects_the_contact_window(
    client: TestClient, db_session: Session, freeze_time, when, reason
) -> None:
    freeze_time(when)
    owner = make_owner(db_session)
    p = portfolio_without_pending_payment(client, db_session, owner)
    response = client.post(f"/v1/leases/{p.lease.id}/reminders", headers=owner.headers)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "reminder_blocked" and reason in detail["reasons"]
    assert detail["next_contact_time"] is not None
    assert db_session.scalar(select(func.count()).select_from(Message)) == 0


def test_reminder_needs_current_consent(
    client: TestClient, db_session: Session, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    p = portfolio_without_pending_payment(client, db_session, owner)
    consent = db_session.scalar(select(ConsentRecord).where(ConsentRecord.lease_id == p.lease.id))
    consent.revoked_at = MONDAY_10AM - dt.timedelta(days=1)
    db_session.flush()
    response = client.post(f"/v1/leases/{p.lease.id}/reminders", headers=owner.headers)
    assert response.status_code == 409
    assert response.json()["detail"]["reasons"] == ["consent_revoked"]
    assert response.json()["detail"]["next_contact_time"] is None


def test_no_reminder_when_nothing_is_owed(
    client: TestClient, db_session: Session, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    ok(client, "POST", f"/v1/payments/{p.payment.id}/confirm", owner.headers)
    response = client.post(f"/v1/leases/{p.lease.id}/reminders", headers=owner.headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "nothing_to_remind"


# -- inbox -------------------------------------------------------------------------------------


def test_resolve_inbox_item(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    body = ok(
        client,
        "POST",
        f"/v1/inbox/{p.inbox_item.id}/resolve",
        owner.headers,
        json={"status": "dismissed", "note": "Lo hablé con ella"},
    ).json()
    assert body["id"] == str(p.inbox_item.id)
    assert db_session.get(InboxItem, p.inbox_item.id).status == InboxItemStatus.DISMISSED
    assert "inbox.resolved" in events(db_session, p.lease.id)
    again = client.post(
        f"/v1/inbox/{p.inbox_item.id}/resolve", headers=owner.headers, json={"status": "resolved"}
    )
    assert again.status_code == 409
    assert (
        client.post(
            f"/v1/inbox/{p.inbox_item.id}/resolve", headers=owner.headers, json={"status": "open"}
        ).status_code
        == 422
    )
