"""Outbound: welcome on new leases, contact window scheduling, session vs template, statuses."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.whatsapp.base import StatusUpdate
from app.channels.whatsapp.simulator import SimulatorChannel
from app.domain.messaging_rules import MessageKind
from app.jobs.runner import InlineJobRunner
from app.models import AssistantProfile, ConsentRecord, Message, TimelineEvent
from app.models.enums import AssistantTone, MessageContentType, MessageStatus
from app.services.messaging import apply_status, send_welcome
from tests.api_utils import Owner, lease_payload, make_owner, make_portfolio, ok
from tests.factories import add, make_rule_set, make_unit
from tests.whatsapp_utils import inbound, outbound

SD = ZoneInfo("America/Santo_Domingo")
MONDAY_10AM = dt.datetime(2026, 11, 23, 10, 0, tzinfo=SD)
SATURDAY_7_30PM = dt.datetime(2026, 11, 28, 19, 30, tzinfo=SD)
NEXT_MONDAY_8AM = dt.datetime(2026, 11, 30, 8, 0, tzinfo=SD)
CHRISTMAS_EVE_7_30PM = dt.datetime(2026, 12, 24, 19, 30, tzinfo=SD)  # the 25th is a holiday
DECEMBER_26_8AM = dt.datetime(2026, 12, 26, 8, 0, tzinfo=SD)
NEW_TENANT_PHONE = "+18295550102"


def owner_with_assistant(db: Session, tone: AssistantTone = AssistantTone.CORDIAL) -> Owner:
    owner = make_owner(db)
    make_rule_set(db, owner.org, is_default=True)
    add(db, AssistantProfile(organization_id=owner.org.id, display_name="Lucía", tone=tone))
    return owner


def create_lease(client: TestClient, db: Session, owner: Owner) -> str:
    unit = make_unit(db, owner.org, code="3B")
    body = ok(client, "POST", "/v1/leases", owner.headers, json=lease_payload(unit.id)).json()
    return body["lease"]["id"]


def welcome(db: Session, lease_id: str) -> Message:
    return db.scalar(
        select(Message).where(Message.lease_id == lease_id, Message.kind == MessageKind.WELCOME)
    )


def test_new_lease_gets_the_welcome_template_with_the_assistant_name(
    client: TestClient, db_session: Session, whatsapp: SimulatorChannel, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = owner_with_assistant(db_session)
    lease_id = create_lease(client, db_session, owner)

    message = welcome(db_session, lease_id)
    assert message.status == MessageStatus.SENT
    # A new tenant never wrote to us: the 24-hour window is closed, so the approved template.
    assert message.content_type == MessageContentType.TEMPLATE
    assert message.template == "bienvenida_tu"
    assert message.body.startswith("Hola, José. Soy Lucía, el asistente de ")
    assert "para el alquiler de Apto 3B." in message.body
    (sent,) = outbound(whatsapp, NEW_TENANT_PHONE)
    assert sent["content"]["name"] == "bienvenida_tu"
    assert sent["content"]["params"]["asistente"] == "Lucía"
    assert sent["content"]["buttons"] == [{"id": f"v1:menu:{lease_id}", "title": "Ver opciones"}]
    assert sent["content"]["footer"] == "Responde BAJA si no quieres recibir mensajes."


def test_formal_assistant_uses_the_usted_template(
    client: TestClient, db_session: Session, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = owner_with_assistant(db_session, AssistantTone.FORMAL)
    message = welcome(db_session, create_lease(client, db_session, owner))
    assert message.template == "bienvenida_usted" and message.body.startswith("Saludos, José.")


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [(SATURDAY_7_30PM, NEXT_MONDAY_8AM), (CHRISTMAS_EVE_7_30PM, DECEMBER_26_8AM)],
    ids=["saturday-night", "holiday-eve"],
)
def test_welcome_outside_hours_waits_for_the_next_valid_time(
    client: TestClient,
    db_session: Session,
    whatsapp: SimulatorChannel,
    job_runner: InlineJobRunner,
    freeze_time,
    created_at,
    expected,
) -> None:
    freeze_time(created_at)
    owner = owner_with_assistant(db_session)
    lease_id = create_lease(client, db_session, owner)

    message = welcome(db_session, lease_id)
    assert message.status == MessageStatus.QUEUED
    assert message.scheduled_for == expected
    assert [j.at for j in job_runner.future] == [expected]
    assert outbound(whatsapp, NEW_TENANT_PHONE) == []
    events = db_session.scalars(
        select(TimelineEvent.event_type).where(TimelineEvent.lease_id == lease_id)
    ).all()
    assert "message.scheduled" in events

    freeze_time(expected)
    job_runner.run_future(db_session)
    assert message.status == MessageStatus.SENT
    assert len(outbound(whatsapp, NEW_TENANT_PHONE)) == 1


def test_scheduled_welcome_is_checked_again_when_it_is_due(
    db_session: Session, job_runner: InlineJobRunner, freeze_time
) -> None:
    freeze_time(SATURDAY_7_30PM)
    owner = make_owner(db_session)
    portfolio = make_portfolio(db_session, owner.org)
    message = send_welcome(db_session, portfolio.lease)
    assert message is not None and message.status == MessageStatus.QUEUED
    consent = db_session.scalar(
        select(ConsentRecord).where(ConsentRecord.lease_id == portfolio.lease.id)
    )
    consent.revoked_at = SATURDAY_7_30PM + dt.timedelta(hours=1)  # BAJA on Saturday night
    db_session.flush()

    freeze_time(NEXT_MONDAY_8AM)
    job_runner.run_future(db_session)
    assert message.status == MessageStatus.FAILED
    assert message.error == "cancelled: consent_revoked"


def owed_portfolio(client: TestClient, db: Session, owner: Owner):
    """Rent owed and no reported payment waiting (that would block "recordar ahora")."""
    portfolio = make_portfolio(db, owner.org)
    url = f"/v1/payments/{portfolio.payment.id}/reject"
    ok(client, "POST", url, owner.headers, json={"reason": "No aparece."})
    return portfolio


def test_open_window_sends_a_session_message_instead_of_the_template(
    client: TestClient, db_session: Session, whatsapp: SimulatorChannel, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    portfolio = owed_portfolio(client, db_session, owner)
    inbound(db_session, portfolio.tenant.phone, "Hola")  # opens the 24-hour window

    body = ok(client, "POST", f"/v1/leases/{portfolio.lease.id}/reminders", owner.headers).json()
    message = db_session.get(Message, body["message_id"])
    assert message.content_type == MessageContentType.INTERACTIVE  # same text + reply buttons
    assert message.template is None
    content = outbound(whatsapp, portfolio.tenant.phone)[-1]["content"]
    assert content["type"] == "buttons"
    assert [b["title"] for b in content["buttons"]] == ["Ya pagué", "Pagaré en otra fecha"]


def test_window_closes_after_24_hours(client: TestClient, db_session: Session, freeze_time) -> None:
    freeze_time(MONDAY_10AM - dt.timedelta(hours=25))
    owner = make_owner(db_session)
    portfolio = owed_portfolio(client, db_session, owner)
    inbound(db_session, portfolio.tenant.phone, "Hola")
    freeze_time(MONDAY_10AM)
    body = ok(client, "POST", f"/v1/leases/{portfolio.lease.id}/reminders", owner.headers).json()
    message = db_session.get(Message, body["message_id"])
    assert message.content_type == MessageContentType.TEMPLATE
    assert message.template == "aviso_firme_tu"  # this month's rent is overdue in the portfolio


# -- delivery statuses -------------------------------------------------------------------------


def sent_message(db: Session) -> Message:
    owner = make_owner(db)
    portfolio = make_portfolio(db, owner.org)
    message = send_welcome(db, portfolio.lease)
    assert message is not None and message.status == MessageStatus.SENT
    return message


def status(message: Message, value: str, **kwargs) -> StatusUpdate:
    return StatusUpdate(message.provider_message_id, value, MONDAY_10AM, **kwargs)  # type: ignore[arg-type]


def test_statuses_only_move_forward(db_session: Session, freeze_time) -> None:
    freeze_time(MONDAY_10AM)
    message = sent_message(db_session)
    assert apply_status(db_session, status(message, "read"))
    assert not apply_status(db_session, status(message, "delivered"))  # late, ignored
    assert not apply_status(db_session, status(message, "failed", error_code=1))
    assert message.status == MessageStatus.READ
    events = db_session.scalars(
        select(TimelineEvent.event_type).where(TimelineEvent.lease_id == message.lease_id)
    ).all()
    assert events.count("message.read") == 1 and "message.delivered" not in events


def test_failed_status_keeps_the_error(db_session: Session, freeze_time) -> None:
    freeze_time(MONDAY_10AM)
    message = sent_message(db_session)
    assert apply_status(db_session, status(message, "delivered"))
    assert apply_status(
        db_session, status(message, "failed", error_code=131026, error_title="Undeliverable")
    )
    assert message.status == MessageStatus.FAILED
    assert message.error == "131026: Undeliverable"
    assert not apply_status(db_session, status(message, "read"))


def test_unknown_provider_id_is_ignored(db_session: Session) -> None:
    assert not apply_status(db_session, StatusUpdate("wamid.NOPE", "delivered", MONDAY_10AM))
