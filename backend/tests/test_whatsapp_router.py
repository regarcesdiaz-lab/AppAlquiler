"""Inbound router: who writes, which contract, keywords, buttons, receipts and the owner."""

import datetime as dt
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.channels.whatsapp.simulator import SimulatorChannel
from app.domain.escalation import consent_revoked_task
from app.domain.messaging_rules import MessageKind
from app.models import (
    AssistantProfile,
    BankAccount,
    ConsentRecord,
    InboxItem,
    Lease,
    Message,
    Organization,
    WhatsAppInboundEvent,
)
from app.models.enums import (
    AccountType,
    AssistantTone,
    ConsentMethod,
    ContactType,
    InboxItemStatus,
    InboxItemType,
    MessageChannel,
    MessageContentType,
    MessageDirection,
    MessageStatus,
)
from app.services.storage import LocalStorage
from tests.api_utils import Portfolio, make_owner, make_portfolio
from tests.factories import add, make_lease, make_org, make_tenant, make_user
from tests.whatsapp_utils import (
    EXE_BYTES,
    JPEG_BYTES,
    PDF_BYTES,
    PNG_BYTES,
    inbound,
    last_text,
    outbound,
)

SD = ZoneInfo("America/Santo_Domingo")
MONDAY_10AM = dt.datetime(2026, 11, 23, 10, 0, tzinfo=SD)
PHONE = "+18095550101"
GRANTED = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)


@pytest.fixture
def portfolio(db_session: Session, freeze_time) -> Portfolio:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    return make_portfolio(db_session, owner.org, phone=PHONE)


def second_lease(db: Session, phone: str = PHONE, org_name: str = "Residencial Sol") -> Lease:
    """The same person rents from another organization."""
    org = make_org(db, org_name)
    tenant = make_tenant(db, org, phone=phone, full_name="María Fernández")
    lease = make_lease(db, org, tenant_id=tenant.id)
    add(
        db,
        ConsentRecord(
            organization_id=org.id,
            lease_id=lease.id,
            tenant_id=tenant.id,
            method=ConsentMethod.CONTRACT_CLAUSE,
            evidence="Cláusula",
            granted_at=GRANTED,
        ),
    )
    return lease


def messages(db: Session, org_id: uuid.UUID, direction: MessageDirection | None = None):
    stmt = select(Message).where(Message.organization_id == org_id)
    if direction is not None:
        stmt = stmt.where(Message.direction == direction)
    return db.scalars(stmt.order_by(Message.created_at)).all()


def open_items(db: Session, lease_id: uuid.UUID, type_: InboxItemType) -> list[InboxItem]:
    return list(
        db.scalars(
            select(InboxItem).where(
                InboxItem.lease_id == lease_id,
                InboxItem.type == type_,
                InboxItem.status == InboxItemStatus.OPEN,
            )
        ).all()
    )


# -- who writes --------------------------------------------------------------------------------


def test_unknown_number_gets_one_polite_reply_a_day(
    db_session: Session, whatsapp: SimulatorChannel, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    stranger = "+18095559999"
    assert inbound(db_session, stranger, "Hola") == "unknown"
    assert "No encontramos un contrato con este número" in last_text(whatsapp, stranger)
    assert inbound(db_session, stranger, "¿Hola?") == "unknown"
    assert len(outbound(whatsapp, stranger)) == 1
    # Nothing is stored in any organization, only the platform's idempotency record.
    assert db_session.scalar(select(func.count()).select_from(Message)) == 0
    events = db_session.scalars(
        select(WhatsAppInboundEvent).where(WhatsAppInboundEvent.phone == stranger)
    ).all()
    assert {e.routing for e in events} == {"unknown"} and len(events) == 2


def test_owner_is_recognised_and_gets_the_temporary_answer(
    db_session: Session, whatsapp: SimulatorChannel, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    org = make_org(db_session)
    owner = make_user(db_session, org, full_name="Carmen Rodríguez", whatsapp_phone="+18095550100")
    assert inbound(db_session, "+18095550100", "¿Qué tengo pendiente?") == "owner"
    received = messages(db_session, org.id, MessageDirection.INBOUND)
    assert len(received) == 1
    assert received[0].contact_type == ContactType.OWNER and received[0].user_id == owner.id
    reply = last_text(whatsapp, "+18095550100")
    assert reply.startswith("Hola, Carmen.") and "panel" in reply


def test_same_provider_message_is_processed_once(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    assert inbound(db_session, PHONE, "MENÚ", provider_id="wamid.ONCE") == "tenant"
    assert inbound(db_session, PHONE, "MENÚ", provider_id="wamid.ONCE") == "duplicate"
    assert len(messages(db_session, portfolio.lease.organization_id, MessageDirection.INBOUND)) == 1
    assert len(outbound(whatsapp, PHONE)) == 1


# -- menu, buttons and list rows ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["MENÚ", "menu", " Menú. "])
def test_menu_is_an_interactive_list(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel, text: str
) -> None:
    assert inbound(db_session, PHONE, text) == "tenant"
    content = outbound(whatsapp, PHONE)[-1]["content"]
    assert content["type"] == "list" and content["button"] == "Ver opciones"
    assert content["body"] == "¿En qué te ayudo, María?"
    assert [r["title"] for r in content["rows"]] == [
        "Ver mi balance",
        "Datos para pagar",
        "Enviar comprobante",
        "Pagaré en otra fecha",
        "Otra consulta",
    ]
    assert all(r["id"].endswith(str(portfolio.lease.id)) for r in content["rows"])


def test_welcome_button_opens_the_same_menu(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    inbound(
        db_session, PHONE, "Ver opciones", type="button", payload=f"v1:menu:{portfolio.lease.id}"
    )
    assert outbound(whatsapp, PHONE)[-1]["content"]["type"] == "list"


def tap(db: Session, lease: Lease, action: str, title: str) -> str:
    return inbound(db, PHONE, title, type="interactive", payload=f"v1:{action}:{lease.id}")


def test_balance(db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel) -> None:
    tap(db_session, portfolio.lease, "balance", "Ver mi balance")
    org = db_session.get(Organization, portfolio.lease.organization_id)
    assert last_text(whatsapp, PHONE).startswith(
        f"Tu balance pendiente con {org.name} es RD$25,000."
    )


def test_payment_details_list_only_active_accounts(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    add(
        db_session,
        BankAccount(
            organization_id=portfolio.lease.organization_id,
            bank_name="Banreservas",
            account_type=AccountType.CHECKING,
            account_number="9600112233",
            holder_name="Dueña",
            is_active=False,
        ),
    )
    tap(db_session, portfolio.lease, "pay_info", "Datos para pagar")
    text = last_text(whatsapp, PHONE)
    assert "Puedes pagarle a" in text
    assert "Banco Popular · Cuenta de ahorros\nNo. 7801234567\nTitular: Dueña" in text
    assert "Banreservas" not in text
    assert text.endswith("Cuando pagues, envíanos el comprobante por aquí.")


def test_payment_details_without_accounts_and_in_usted(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    org_id = portfolio.lease.organization_id
    portfolio.bank_account.is_active = False
    add(
        db_session,
        AssistantProfile(organization_id=org_id, display_name="Lucía", tone=AssistantTone.FORMAL),
    )
    tap(db_session, portfolio.lease, "pay_info", "Datos para pagar")
    org = db_session.get(Organization, org_id)
    assert last_text(whatsapp, PHONE) == f"Pídale los datos de pago a {org.name}."


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("paid", "Perfecto. Envía aquí la foto o el PDF del comprobante."),
        ("send_receipt", "Perfecto. Envía aquí la foto o el PDF del comprobante."),
        ("pay_later", "¿Qué día puedes pagar?"),
        ("other", "Escribe tu consulta"),
    ],
)
def test_buttons_answer_with_their_session_message(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel, action, expected
) -> None:
    tap(db_session, portfolio.lease, action, "x")
    assert last_text(whatsapp, PHONE).startswith(expected)


# -- free text and media the assistant can't handle yet ----------------------------------------


def test_free_text_becomes_one_pending_item_and_one_acknowledgement(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    inbound(db_session, PHONE, "Puedo pagar el viernes")
    inbound(db_session, PHONE, "Mejor el sábado")
    items = open_items(db_session, portfolio.lease.id, InboxItemType.TENANT_MESSAGE)
    assert len(items) == 1
    assert items[0].summary == "María Fernández te escribió por WhatsApp: “Mejor el sábado”"
    acks = [e for e in outbound(whatsapp, PHONE) if "Recibido." in e["content"].get("body", "")]
    assert len(acks) == 1


def test_voice_note_asks_for_text_and_tells_the_owner(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    inbound(db_session, PHONE, type="audio", media_id="123", mime="audio/ogg")
    assert last_text(whatsapp, PHONE).startswith("Por ahora no puedo escuchar notas de voz")
    (item,) = open_items(db_session, portfolio.lease.id, InboxItemType.TENANT_MESSAGE)
    assert "una nota de voz" in item.summary


# -- receipts ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("type_", "content", "mime", "ext"),
    [
        ("image", JPEG_BYTES, "image/jpeg", "jpg"),
        ("image", PNG_BYTES, "image/png", "png"),
        ("document", PDF_BYTES, "application/pdf", "pdf"),
    ],
)
def test_receipt_is_stored_confirmed_and_sent_to_the_owner(
    db_session: Session,
    portfolio: Portfolio,
    whatsapp: SimulatorChannel,
    storage: LocalStorage,
    type_,
    content,
    mime,
    ext,
) -> None:
    media_id = whatsapp.store_media(content, mime, f"comprobante.{ext}")
    inbound(db_session, PHONE, type=type_, media_id=media_id, mime=mime, filename=f"c.{ext}")
    (received,) = messages(db_session, portfolio.lease.organization_id, MessageDirection.INBOUND)
    assert received.media_mime == mime and received.media_key.endswith(f".{ext}")
    assert received.media_key.startswith(f"org/{portfolio.lease.organization_id}/messages/")
    assert storage.read(received.media_key) == content
    assert last_text(whatsapp, PHONE).startswith("Gracias, María. Recibimos tu comprobante")
    (item,) = open_items(db_session, portfolio.lease.id, InboxItemType.TENANT_MESSAGE)
    assert "comprobante" in item.summary


@pytest.mark.parametrize(
    ("type_", "content", "declared", "reason"),
    [
        ("image", EXE_BYTES, "image/jpeg", "unsupported_type"),
        ("document", b"just some text", "application/pdf", "unsupported_type"),
        ("image", PNG_BYTES, "image/jpeg", "type_mismatch"),
        ("document", JPEG_BYTES, "application/pdf", "type_mismatch"),
    ],
)
def test_file_whose_content_is_not_what_it_claims_is_rejected(
    db_session: Session,
    portfolio: Portfolio,
    whatsapp: SimulatorChannel,
    storage: LocalStorage,
    type_,
    content,
    declared,
    reason,
) -> None:
    media_id = whatsapp.store_media(content, declared, "comprobante")
    inbound(db_session, PHONE, type=type_, media_id=media_id, mime=declared)
    (received,) = messages(db_session, portfolio.lease.organization_id, MessageDirection.INBOUND)
    assert received.media_key is None
    assert received.payload["media_rejected"] == reason
    assert not storage.root.exists() or not any(storage.root.rglob("*.*"))
    assert last_text(whatsapp, PHONE) == (
        "No pude abrir ese archivo. Envíame la foto (JPG o PNG) o el PDF del comprobante."
    )
    assert open_items(db_session, portfolio.lease.id, InboxItemType.TENANT_MESSAGE) == []


# -- two organizations -------------------------------------------------------------------------


def test_two_organizations_ask_which_and_then_process_the_waiting_message(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    other = second_lease(db_session)
    assert inbound(db_session, PHONE, "Ya deposité") == "awaiting_choice"
    question = outbound(whatsapp, PHONE)[-1]["content"]
    assert question["type"] == "list" and question["button"] == "Ver contratos"
    assert len(question["rows"]) == 2
    # Nothing is stored in either organization until the tenant answers.
    assert messages(db_session, portfolio.lease.organization_id) == []
    assert messages(db_session, other.organization_id) == []

    row = next(r for r in question["rows"] if r["id"].endswith(str(other.id)))
    assert inbound(db_session, PHONE, row["title"], type="interactive", payload=row["id"]) == (
        "tenant"
    )
    received = messages(db_session, other.organization_id, MessageDirection.INBOUND)
    assert sorted(m.body for m in received) == sorted([row["title"], "Ya deposité"])
    assert len(open_items(db_session, other.id, InboxItemType.TENANT_MESSAGE)) == 1
    assert messages(db_session, portfolio.lease.organization_id) == []

    # The choice holds for the next messages.
    inbound(db_session, PHONE, "¿Me confirman?")
    assert len(messages(db_session, other.organization_id, MessageDirection.INBOUND)) == 3
    assert messages(db_session, portfolio.lease.organization_id) == []


def test_choice_by_number(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    second_lease(db_session)
    inbound(db_session, PHONE, "MENÚ")
    first_row = outbound(whatsapp, PHONE)[-1]["content"]["rows"][0]
    inbound(db_session, PHONE, "1")
    chosen_id = uuid.UUID(first_row["id"].rsplit(":", 1)[1])
    chosen = db_session.get(Lease, chosen_id)
    assert outbound(whatsapp, PHONE)[-1]["content"]["type"] == "list"  # the menu, now
    assert len(messages(db_session, chosen.organization_id, MessageDirection.INBOUND)) == 2


def test_reply_to_a_message_goes_to_its_organization_without_asking(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    other = second_lease(db_session)
    add(
        db_session,
        Message(
            organization_id=other.organization_id,
            lease_id=other.id,
            tenant_id=other.tenant_id,
            channel=MessageChannel.WHATSAPP,
            direction=MessageDirection.OUTBOUND,
            kind=MessageKind.COLLECTION_REMINDER,
            body="Aviso",
            status=MessageStatus.DELIVERED,
            provider_message_id="wamid.FROM-B",
        ),
    )
    assert inbound(db_session, PHONE, "Ok, gracias", context_id="wamid.FROM-B") == "tenant"
    assert len(messages(db_session, other.organization_id, MessageDirection.INBOUND)) == 1
    assert messages(db_session, portfolio.lease.organization_id) == []


def test_button_with_someone_elses_lease_is_ignored(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    stranger_lease = second_lease(db_session, phone="+18295550199")
    inbound(db_session, PHONE, "Ya pagué", type="button", payload=f"v1:paid:{stranger_lease.id}")
    assert messages(db_session, stranger_lease.organization_id) == []
    assert len(messages(db_session, portfolio.lease.organization_id, MessageDirection.INBOUND)) == 1


def test_button_of_an_ended_lease(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    portfolio.lease.is_active = False
    db_session.flush()
    routed = inbound(
        db_session, PHONE, "Ya pagué", type="button", payload=f"v1:paid:{portfolio.lease.id}"
    )
    assert routed == "inactive_lease"
    assert last_text(whatsapp, PHONE).startswith("Ese contrato ya no está activo.")


# -- BAJA and ALTA -----------------------------------------------------------------------------


def current_consent(db: Session, lease: Lease) -> ConsentRecord | None:
    return db.scalar(
        select(ConsentRecord).where(
            ConsentRecord.lease_id == lease.id, ConsentRecord.revoked_at.is_(None)
        )
    )


def test_baja_without_context_stops_every_organization_at_once(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    other = second_lease(db_session)
    scheduled = add(
        db_session,
        Message(
            organization_id=other.organization_id,
            lease_id=other.id,
            tenant_id=other.tenant_id,
            channel=MessageChannel.WHATSAPP,
            direction=MessageDirection.OUTBOUND,
            kind=MessageKind.COLLECTION_REMINDER,
            body="Aviso programado",
            status=MessageStatus.QUEUED,
            scheduled_for=MONDAY_10AM + dt.timedelta(days=1),
        ),
    )
    assert inbound(db_session, PHONE, "Baja") == "tenant"

    for lease in (portfolio.lease, other):
        assert current_consent(db_session, lease) is None
        (item,) = open_items(db_session, lease.id, InboxItemType.CONSENT_REVOKED)
        assert item.summary == consent_revoked_task("María Fernández").summary
    assert scheduled.status == MessageStatus.FAILED and "consent_revoked" in scheduled.error

    confirmations = [
        m
        for m in db_session.scalars(
            select(Message).where(Message.kind == MessageKind.OPT_OUT_CONFIRMATION)
        ).all()
    ]
    assert {m.organization_id for m in confirmations} == {
        portfolio.lease.organization_id,
        other.organization_id,
    }
    assert all(m.status == MessageStatus.SENT for m in confirmations)
    assert all("Ya no recibirás mensajes de" in m.body for m in confirmations)


def test_baja_answering_one_organization_only_stops_that_one(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    other = second_lease(db_session)
    add(
        db_session,
        Message(
            organization_id=other.organization_id,
            lease_id=other.id,
            channel=MessageChannel.WHATSAPP,
            direction=MessageDirection.OUTBOUND,
            kind=MessageKind.COLLECTION_REMINDER,
            body="Aviso",
            status=MessageStatus.READ,
            provider_message_id="wamid.B-REMINDER",
        ),
    )
    inbound(db_session, PHONE, "BAJA", context_id="wamid.B-REMINDER")
    assert current_consent(db_session, other) is None
    assert current_consent(db_session, portfolio.lease) is not None


def test_after_baja_only_one_notice_a_day_and_alta_turns_it_back_on(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    inbound(db_session, PHONE, "BAJA")
    sent_after_baja = len(outbound(whatsapp, PHONE))
    inbound(db_session, PHONE, "Hola")
    inbound(db_session, PHONE, "¿Hola?")
    assert len(outbound(whatsapp, PHONE)) == sent_after_baja + 1
    assert last_text(whatsapp, PHONE).startswith("Tienes los mensajes desactivados.")
    assert open_items(db_session, portfolio.lease.id, InboxItemType.TENANT_MESSAGE) == []

    inbound(db_session, PHONE, "alta")
    consent = current_consent(db_session, portfolio.lease)
    assert consent is not None and consent.method == ConsentMethod.WHATSAPP_REPLY
    assert "ALTA" in consent.evidence
    assert open_items(db_session, portfolio.lease.id, InboxItemType.CONSENT_REVOKED) == []
    assert last_text(whatsapp, PHONE).startswith("Listo, María. Volverás a recibir los avisos")


def test_every_message_lands_in_message_and_the_timeline(
    db_session: Session, portfolio: Portfolio, whatsapp: SimulatorChannel
) -> None:
    from app.models import TimelineEvent

    inbound(db_session, PHONE, "MENÚ")
    (received,) = messages(db_session, portfolio.lease.organization_id, MessageDirection.INBOUND)
    (reply,) = messages(db_session, portfolio.lease.organization_id, MessageDirection.OUTBOUND)
    assert received.body == "MENÚ" and received.contact_phone == PHONE
    assert reply.content_type == MessageContentType.INTERACTIVE and reply.kind == MessageKind.REPLY
    events = db_session.scalars(
        select(TimelineEvent.event_type).where(TimelineEvent.lease_id == portfolio.lease.id)
    ).all()
    assert {"message.received", "message.queued", "message.sent"} <= set(events)
