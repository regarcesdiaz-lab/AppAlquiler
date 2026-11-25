"""The local simulator page goes through the real webhook path, and never exists in production."""

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.whatsapp.simulator import SimulatorChannel
from app.core.config import get_settings
from app.main import create_app
from app.models import Message, WhatsAppInboundEvent
from app.models.enums import MessageDirection, MessageStatus
from tests.api_utils import make_owner, make_portfolio
from tests.whatsapp_utils import EXE_BYTES, JPEG_BYTES

SD = ZoneInfo("America/Santo_Domingo")
MONDAY_10AM = dt.datetime(2026, 11, 23, 10, 0, tzinfo=SD)
PHONE = "+18095550101"
API = "/dev/whatsapp/api"


@pytest.fixture
def tenant_chat(db_session: Session, freeze_time):
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    return make_portfolio(db_session, owner.org, phone=PHONE)


def chat(client: TestClient, viewing: bool = True) -> dict:
    response = client.get(f"{API}/chats/%2B18095550101", params={"viewing": int(viewing)})
    assert response.status_code == 200, response.text
    return response.json()


def test_page_is_served(client: TestClient) -> None:
    response = client.get("/dev/whatsapp")
    assert response.status_code == 200
    assert "Simulador de WhatsApp" in response.text


def test_simulator_does_not_exist_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    get_settings.cache_clear()
    try:
        production = create_app()
        with TestClient(production) as client:
            for path in ("/dev/whatsapp", "/dev/whatsapp/api/contacts"):
                assert client.get(path).status_code == 404
            assert client.post("/dev/whatsapp/api/messages", json={}).status_code == 404
            # The real webhook is there (it refuses the wrong token).
            assert client.get("/webhooks/whatsapp").status_code == 403
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


def test_typing_as_the_tenant_goes_through_the_router(
    client: TestClient, db_session: Session, tenant_chat
) -> None:
    response = client.post(f"{API}/messages", json={"phone": PHONE, "type": "text", "text": "MENÚ"})
    assert response.status_code == 200
    provider_id = response.json()["provider_message_id"]
    assert provider_id.startswith("wamid.SIM")
    assert db_session.scalar(
        select(WhatsAppInboundEvent).where(WhatsAppInboundEvent.provider_message_id == provider_id)
    )

    data = chat(client)
    incoming, menu = data["entries"]
    assert incoming["direction"] == "in" and incoming["content"]["text"] == "MENÚ"
    assert menu["content"]["type"] == "list" and menu["status"] == "read"
    assert data["window_open_until"] is not None
    assert data["leases"][0]["consent"] == "vigente"

    # Tapping a row of the list is an interactive reply to that message.
    row = menu["content"]["rows"][0]
    client.post(
        f"{API}/messages",
        json={
            "phone": PHONE,
            "type": "interactive",
            "interactive_type": "list_reply",
            "payload": row["id"],
            "text": row["title"],
            "context_id": menu["id"],
        },
    )
    assert chat(client)["entries"][-1]["content"]["body"].startswith("Tu balance pendiente")


def test_delivery_statuses_come_back_through_the_status_updater(
    client: TestClient, db_session: Session, tenant_chat
) -> None:
    client.post(f"{API}/messages", json={"phone": PHONE, "type": "text", "text": "MENÚ"})
    reply = db_session.scalar(select(Message).where(Message.direction == MessageDirection.OUTBOUND))
    chat(client, viewing=False)
    db_session.refresh(reply)
    assert reply.status == MessageStatus.DELIVERED
    chat(client, viewing=True)
    db_session.refresh(reply)
    assert reply.status == MessageStatus.READ


def test_repeating_the_last_message_does_not_duplicate_it(
    client: TestClient, db_session: Session, tenant_chat
) -> None:
    client.post(f"{API}/messages", json={"phone": PHONE, "type": "text", "text": "Hola"})
    again = client.post(
        f"{API}/messages", json={"phone": PHONE, "type": "text", "repeat_last": True}
    )
    assert again.status_code == 200
    inbound = db_session.scalars(
        select(Message).where(Message.direction == MessageDirection.INBOUND)
    ).all()
    assert len(inbound) == 1


def test_uploading_a_receipt(
    client: TestClient, db_session: Session, tenant_chat, whatsapp: SimulatorChannel
) -> None:
    media = client.post(
        f"{API}/media", files={"file": ("transferencia.jpg", JPEG_BYTES, "image/jpeg")}
    ).json()
    client.post(
        f"{API}/messages",
        json={"phone": PHONE, "type": "image", **{k: media[k] for k in ("media_id", "mime")}},
    )
    received = db_session.scalar(
        select(Message).where(Message.direction == MessageDirection.INBOUND)
    )
    assert received.media_key is not None
    assert chat(client)["entries"][-1]["content"]["body"].startswith("Gracias, María.")


def test_uploading_a_disguised_file(client: TestClient, db_session: Session, tenant_chat) -> None:
    media = client.post(
        f"{API}/media", files={"file": ("foto.jpg", EXE_BYTES, "image/jpeg")}
    ).json()
    client.post(
        f"{API}/messages",
        json={
            "phone": PHONE,
            "type": "image",
            "media_id": media["media_id"],
            "mime": media["mime"],
        },
    )
    received = db_session.scalar(
        select(Message).where(Message.direction == MessageDirection.INBOUND)
    )
    assert received.media_key is None and received.payload["media_rejected"] == "unsupported_type"
    assert chat(client)["entries"][-1]["content"]["body"].startswith("No pude abrir ese archivo.")


def test_simulated_delivery_failure(client: TestClient, db_session: Session, tenant_chat) -> None:
    client.post(f"{API}/messages", json={"phone": PHONE, "type": "text", "text": "MENÚ"})
    assert client.post(f"{API}/chats/%2B18095550101/fail-last").status_code == 200
    reply = db_session.scalar(select(Message).where(Message.direction == MessageDirection.OUTBOUND))
    db_session.refresh(reply)
    assert reply.status == MessageStatus.FAILED and reply.error.startswith("131026")


def test_contacts_list_tenants_owners_and_other_numbers(
    client: TestClient, db_session: Session, tenant_chat, whatsapp: SimulatorChannel
) -> None:
    whatsapp.record_inbound("+18095559999", "wamid.X", {"type": "text", "text": "hola"})
    contacts = {c["phone"]: c for c in client.get(f"{API}/contacts").json()}
    assert contacts[PHONE]["roles"][0]["role"] == "tenant"
    assert contacts["+18095559999"]["roles"] == []
