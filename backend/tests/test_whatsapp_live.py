"""LiveWhatsAppChannel against a fake Meta (httpx.MockTransport). No credentials, no network."""

import json

import httpx
import pytest

from app.channels.whatsapp.base import (
    MAX_MEDIA_BYTES,
    Button,
    ButtonsSend,
    ListRowOut,
    ListSend,
    MediaTooLargeError,
    TemplateSend,
    TextSend,
    WhatsAppError,
)
from app.channels.whatsapp.live import LiveWhatsAppChannel
from app.core.config import Settings

PHONE_ID = "106540352242922"
TOKEN = "test-token-not-real"
API = "https://graph.facebook.com/v23.0"


def channel(handler) -> tuple[LiveWhatsAppChannel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    settings = Settings(
        _env_file=None, WHATSAPP_PHONE_NUMBER_ID=PHONE_ID, WHATSAPP_ACCESS_TOKEN=TOKEN
    )
    client = httpx.Client(transport=httpx.MockTransport(record))
    return LiveWhatsAppChannel(settings, client), seen


def accepted(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "messaging_product": "whatsapp",
            "contacts": [{"input": "18095550101", "wa_id": "18095550101"}],
            "messages": [{"id": "wamid.LIVE-1", "message_status": "accepted"}],
        },
    )


def test_template_with_named_parameters_and_button_payloads() -> None:
    live, seen = channel(accepted)
    provider_id = live.send(
        "+18095550101",
        TemplateSend(
            name="aviso_amable_tu",
            language="es",
            params={"nombre": "María", "monto": "RD$25,000"},
            buttons=(Button("v1:paid:L1", "Ya pagué"), Button("v1:pay_later:L1", "Pagaré")),
        ),
    )
    assert provider_id == "wamid.LIVE-1"
    (request,) = seen
    assert str(request.url) == f"{API}/{PHONE_ID}/messages"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(request.content)
    assert body["to"] == "18095550101" and body["type"] == "template"
    template = body["template"]
    assert template["name"] == "aviso_amable_tu" and template["language"] == {"code": "es"}
    body_component, first_button, second_button = template["components"]
    assert body_component == {
        "type": "body",
        "parameters": [
            {"type": "text", "parameter_name": "nombre", "text": "María"},
            {"type": "text", "parameter_name": "monto", "text": "RD$25,000"},
        ],
    }
    assert first_button == {
        "type": "button",
        "sub_type": "quick_reply",
        "index": "0",
        "parameters": [{"type": "payload", "payload": "v1:paid:L1"}],
    }
    assert second_button["index"] == "1"


def test_template_with_a_document_header() -> None:
    live, seen = channel(accepted)
    live.send(
        "+18095550101",
        TemplateSend(
            name="recibo_enviado_tu",
            language="es",
            params={"nombre": "María"},
            header_document_id="MEDIA-1",
            header_document_filename="recibo-0042.pdf",
        ),
    )
    header = json.loads(seen[0].content)["template"]["components"][0]
    assert header == {
        "type": "header",
        "parameters": [
            {"type": "document", "document": {"id": "MEDIA-1", "filename": "recibo-0042.pdf"}}
        ],
    }


def test_session_text_buttons_and_list() -> None:
    live, seen = channel(accepted)
    live.send("+18095550101", TextSend("Recibido."))
    live.send(
        "+18095550101",
        ButtonsSend("¿Pagaste?", (Button("v1:paid:L1", "Ya pagué"),), footer="Pie"),
    )
    live.send(
        "+18095550101",
        ListSend(
            "¿En qué te ayudo?",
            "Ver opciones",
            (ListRowOut("v1:balance:L1", "Ver mi balance"), ListRowOut("x", "Otra", "Detalle")),
        ),
    )
    text, buttons, listing = (json.loads(r.content) for r in seen)
    assert text["text"] == {"preview_url": False, "body": "Recibido."}
    assert buttons["interactive"]["type"] == "button"
    assert buttons["interactive"]["footer"] == {"text": "Pie"}
    assert buttons["interactive"]["action"]["buttons"] == [
        {"type": "reply", "reply": {"id": "v1:paid:L1", "title": "Ya pagué"}}
    ]
    action = listing["interactive"]["action"]
    assert action["button"] == "Ver opciones"
    assert action["sections"][0]["rows"] == [
        {"id": "v1:balance:L1", "title": "Ver mi balance"},
        {"id": "x", "title": "Otra", "description": "Detalle"},
    ]


def test_meta_error_becomes_a_whatsapp_error_with_its_code() -> None:
    live, _ = channel(
        lambda _r: httpx.Response(
            400,
            json={
                "error": {
                    "message": "(#131030) Recipient phone number not in allowed list",
                    "type": "OAuthException",
                    "code": 131030,
                    "fbtrace_id": "A1",
                }
            },
        )
    )
    with pytest.raises(WhatsAppError) as error:
        live.send("+18095550101", TextSend("Hola"))
    assert error.value.code == 131030 and "allowed list" in str(error.value)


def test_network_failure_becomes_a_whatsapp_error() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout", request=request)

    live, _ = channel(boom)
    with pytest.raises(WhatsAppError):
        live.send("+18095550101", TextSend("Hola"))


def media_api(file_size: int, content: bytes):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/MEDIA-1"):
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=1",
                    "mime_type": "image/jpeg",
                    "sha256": "x",
                    "file_size": file_size,
                    "id": "MEDIA-1",
                },
            )
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        return httpx.Response(200, content=content)

    return handler


def test_fetch_media_downloads_with_the_token() -> None:
    live, seen = channel(media_api(4, b"\xff\xd8\xff\xe0"))
    media = live.fetch_media("MEDIA-1")
    assert media.content == b"\xff\xd8\xff\xe0" and media.mime == "image/jpeg"
    assert [r.url.host for r in seen] == ["graph.facebook.com", "lookaside.fbsbx.com"]


def test_fetch_media_refuses_big_files_before_downloading() -> None:
    live, seen = channel(media_api(MAX_MEDIA_BYTES + 1, b""))
    with pytest.raises(MediaTooLargeError):
        live.fetch_media("MEDIA-1")
    assert len(seen) == 1


def test_upload_media_returns_its_id() -> None:
    live, seen = channel(lambda _r: httpx.Response(200, json={"id": "MEDIA-9"}))
    assert live.upload_media(b"%PDF-1.7", "application/pdf", "recibo.pdf") == "MEDIA-9"
    assert str(seen[0].url) == f"{API}/{PHONE_ID}/media"
    assert b'name="messaging_product"' in seen[0].content
