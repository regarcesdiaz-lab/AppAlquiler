"""Meta webhook: subscription token, signature, statuses and idempotency, with example payloads."""

import datetime as dt
import json
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.webhooks import signature_for
from app.channels.whatsapp.parser import parse_webhook
from app.core.config import Settings, get_settings
from app.domain.messaging_rules import MessageKind
from app.models import Message
from app.models.enums import MessageChannel, MessageDirection, MessageStatus
from tests.api_utils import make_owner, make_portfolio
from tests.factories import add
from tests.whatsapp_utils import load_fixture

SD = ZoneInfo("America/Santo_Domingo")
MONDAY_10AM = dt.datetime(2026, 11, 23, 10, 0, tzinfo=SD)
SECRET = "test-app-secret"
URL = "/webhooks/whatsapp"


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    config = get_settings()
    monkeypatch.setattr(config, "WHATSAPP_APP_SECRET", SECRET)
    monkeypatch.setattr(config, "WHATSAPP_VERIFY_TOKEN", "verify-me")
    return config


def post(client: TestClient, payload: dict, secret: str | None = SECRET, signature=None):
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    elif secret is not None:
        headers["X-Hub-Signature-256"] = signature_for(body, secret)
    return client.post(URL, content=body, headers=headers)


# -- subscription ------------------------------------------------------------------------------


def test_subscription_challenge_with_the_right_token(client: TestClient, settings) -> None:
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-me",
        "hub.challenge": "1158201444",
    }
    response = client.get(URL, params=params)
    assert response.status_code == 200 and response.text == "1158201444"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": "verify-me", "hub.challenge": "1"},
        {},
    ],
)
def test_subscription_refused(client: TestClient, settings, params) -> None:
    assert client.get(URL, params=params).status_code == 403


def test_subscription_refused_without_a_configured_token(client: TestClient) -> None:
    params = {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "1"}
    assert client.get(URL, params=params).status_code == 403


# -- signature ---------------------------------------------------------------------------------


def test_valid_signature_processes_the_message(
    client: TestClient, db_session: Session, settings, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    make_portfolio(db_session, owner.org, phone="+18095550101")
    assert post(client, load_fixture("text")).status_code == 200
    received = db_session.scalar(
        select(Message).where(Message.direction == MessageDirection.INBOUND)
    )
    assert received.body == "Hola, ¿cuánto debo?"
    assert received.provider_message_id == "wamid.HBgLMTgwOTU1NTAxMDEVAgASGBQzQTc3"


@pytest.mark.parametrize(
    ("secret", "signature"),
    [(None, None), ("other-secret", None), (None, "sha256=deadbeef"), (None, "")],
    ids=["missing", "wrong-secret", "garbage", "empty"],
)
def test_bad_signature_is_rejected(
    client: TestClient, db_session: Session, settings, secret, signature
) -> None:
    response = post(client, load_fixture("text"), secret=secret, signature=signature)
    assert response.status_code == 401
    assert db_session.scalar(select(Message.id).limit(1)) is None


def test_webhook_refuses_to_work_without_an_app_secret(client: TestClient) -> None:
    assert post(client, load_fixture("text"), secret="anything").status_code == 503


def test_same_payload_twice_is_processed_once(
    client: TestClient, db_session: Session, settings, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    owner = make_owner(db_session)
    make_portfolio(db_session, owner.org, phone="+18095550101")
    payload = load_fixture("text")
    assert post(client, payload).status_code == 200
    assert post(client, payload).status_code == 200  # Meta retries
    inbound = db_session.scalars(
        select(Message).where(Message.direction == MessageDirection.INBOUND)
    ).all()
    assert len(inbound) == 1
    replies = db_session.scalars(
        select(Message).where(Message.direction == MessageDirection.OUTBOUND)
    ).all()
    assert len(replies) == 1


def test_other_phone_number_ids_are_ignored(
    client: TestClient, db_session: Session, settings, monkeypatch, freeze_time
) -> None:
    freeze_time(MONDAY_10AM)
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "999999")
    owner = make_owner(db_session)
    make_portfolio(db_session, owner.org, phone="+18095550101")
    assert post(client, load_fixture("text")).status_code == 200
    assert db_session.scalar(select(Message.id).limit(1)) is None


def test_body_that_is_not_json_is_acknowledged(client: TestClient, settings) -> None:
    body = b"not json"
    response = client.post(
        URL, content=body, headers={"X-Hub-Signature-256": signature_for(body, SECRET)}
    )
    assert response.status_code == 200


# -- statuses ----------------------------------------------------------------------------------


def test_statuses_from_meta_update_the_messages(
    client: TestClient, db_session: Session, settings
) -> None:
    owner = make_owner(db_session)
    portfolio = make_portfolio(db_session, owner.org)

    def outbound(provider_id: str) -> Message:
        return add(
            db_session,
            Message(
                organization_id=owner.org.id,
                lease_id=portfolio.lease.id,
                channel=MessageChannel.WHATSAPP,
                direction=MessageDirection.OUTBOUND,
                kind=MessageKind.COLLECTION_REMINDER,
                body="Aviso",
                status=MessageStatus.QUEUED,
                provider_message_id=provider_id,
            ),
        )

    read, failed = outbound("wamid.OUT-1"), outbound("wamid.OUT-2")
    assert post(client, load_fixture("statuses")).status_code == 200
    db_session.refresh(read)
    db_session.refresh(failed)
    assert read.status == MessageStatus.READ
    assert failed.status == MessageStatus.FAILED
    assert failed.error == "131026: Message undeliverable"


# -- parser ------------------------------------------------------------------------------------


def test_parser_reads_every_message_type() -> None:
    text = parse_webhook(load_fixture("text")).messages[0]
    assert (text.type, text.phone, text.text) == ("text", "+18095550101", "Hola, ¿cuánto debo?")
    assert text.timestamp == dt.datetime.fromtimestamp(1795957200, tz=dt.UTC)

    button = parse_webhook(load_fixture("button")).messages[0]
    assert button.type == "button" and button.text == "Ya pagué"
    assert button.payload.startswith("v1:paid:")
    assert button.context_id == "wamid.OUTBOUND-TEMPLATE-1"

    row = parse_webhook(load_fixture("list_reply")).messages[0]
    assert (row.type, row.text) == ("interactive", "Ver mi balance")
    assert row.payload.startswith("v1:balance:")

    reply = parse_webhook(load_fixture("button_reply")).messages[0]
    assert reply.type == "interactive" and reply.payload.startswith("v1:menu:")

    image = parse_webhook(load_fixture("image")).messages[0]
    assert (image.type, image.media_id, image.media_mime) == (
        "image",
        "1048596549601234",
        "image/jpeg",
    )
    assert image.caption == "Transferencia de noviembre"

    document = parse_webhook(load_fixture("document")).messages[0]
    assert document.media_filename == "comprobante.pdf"
    assert document.media_mime == "application/pdf"

    audio = parse_webhook(load_fixture("audio")).messages[0]
    assert audio.type == "audio" and audio.media_mime.startswith("audio/ogg")


def test_parser_skips_what_it_cannot_use() -> None:
    parsed = parse_webhook(load_fixture("batch"))
    assert [(m.provider_message_id, m.type) for m in parsed.messages] == [
        ("wamid.BATCH-1", "text"),
        ("wamid.BATCH-2", "reaction"),
        ("wamid.BATCH-3", "location"),
        ("wamid.BATCH-4", "other"),
    ]  # the message without a sender is dropped
    statuses = parse_webhook(load_fixture("statuses")).statuses
    assert [s.status for s in statuses] == ["sent", "delivered", "read", "failed"]  # no "deleted"
    assert statuses[-1].error_code == 131026
    assert parse_webhook("not a dict").messages == []
    assert parse_webhook(load_fixture("text"), phone_number_id="other").messages == []
