"""Meta WhatsApp Cloud API client (only with MESSAGING_MODE=live, in production).

Tested with httpx.MockTransport and example payloads; it never runs without real credentials.
"""

import logging
from typing import Any

import httpx

from app.channels.whatsapp.base import (
    MAX_MEDIA_BYTES,
    ButtonsSend,
    ListSend,
    MediaFile,
    MediaTooLargeError,
    OutboundContent,
    TemplateSend,
    TextSend,
    WhatsAppError,
)
from app.core.config import Settings

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(10.0)


def _interactive_footer(footer: str | None) -> dict[str, Any]:
    return {"footer": {"text": footer}} if footer else {}


def message_body(to: str, content: OutboundContent) -> dict[str, Any]:
    """JSON for POST /{phone_number_id}/messages."""
    base: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
    }
    if isinstance(content, TextSend):
        return base | {"type": "text", "text": {"preview_url": False, "body": content.body}}
    if isinstance(content, ButtonsSend):
        return base | {
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": content.body},
                **_interactive_footer(content.footer),
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": b.id, "title": b.title}}
                        for b in content.buttons
                    ]
                },
            },
        }
    if isinstance(content, ListSend):
        return base | {
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": content.body},
                **_interactive_footer(content.footer),
                "action": {
                    "button": content.button,
                    "sections": [
                        {
                            "rows": [
                                {
                                    "id": r.id,
                                    "title": r.title,
                                    **({"description": r.description} if r.description else {}),
                                }
                                for r in content.rows
                            ]
                        }
                    ],
                },
            },
        }
    if isinstance(content, TemplateSend):
        components: list[dict[str, Any]] = []
        if content.header_document_id:
            document: dict[str, Any] = {"id": content.header_document_id}
            if content.header_document_filename:
                document["filename"] = content.header_document_filename
            components.append(
                {"type": "header", "parameters": [{"type": "document", "document": document}]}
            )
        if content.params:
            components.append(
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "parameter_name": name, "text": value}
                        for name, value in content.params.items()
                    ],
                }
            )
        for index, button in enumerate(content.buttons):
            components.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(index),
                    "parameters": [{"type": "payload", "payload": button.id}],
                }
            )
        return base | {
            "type": "template",
            "template": {
                "name": content.name,
                "language": {"code": content.language},
                "components": components,
            },
        }
    raise TypeError(f"Unsupported content {type(content).__name__}")


class LiveWhatsAppChannel:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self._phone_number_id = settings.WHATSAPP_PHONE_NUMBER_ID
        self._token = settings.WHATSAPP_ACCESS_TOKEN
        base = settings.WHATSAPP_API_BASE_URL.rstrip("/")
        self._api = f"{base}/{settings.WHATSAPP_GRAPH_VERSION}"
        self._client = client or httpx.Client(timeout=TIMEOUT)

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _check(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.is_success and isinstance(data, dict):
            return data
        error = data.get("error") if isinstance(data, dict) else None
        error = error if isinstance(error, dict) else {}
        message = str(error.get("message") or f"HTTP {response.status_code}")
        code = error.get("code")
        raise WhatsAppError(message, code=code if isinstance(code, int) else None)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return self._client.request(method, url, headers=self._auth, **kwargs)
        except httpx.HTTPError as exc:
            raise WhatsAppError(f"Could not reach WhatsApp: {exc.__class__.__name__}") from exc

    def send(self, to: str, content: OutboundContent) -> str:
        response = self._request(
            "POST",
            f"{self._api}/{self._phone_number_id}/messages",
            json=message_body(to, content),
        )
        data = self._check(response)
        try:
            return str(data["messages"][0]["id"])
        except (KeyError, IndexError, TypeError):
            raise WhatsAppError("WhatsApp answered without a message id") from None

    def fetch_media(self, media_id: str) -> MediaFile:
        info = self._check(self._request("GET", f"{self._api}/{media_id}"))
        url = info.get("url")
        if not url:
            raise WhatsAppError("WhatsApp media without a download URL")
        size = info.get("file_size")
        if isinstance(size, int) and size > MAX_MEDIA_BYTES:
            raise MediaTooLargeError(f"Media is {size} bytes")
        response = self._request("GET", str(url))
        if not response.is_success:
            raise WhatsAppError(f"Media download failed: HTTP {response.status_code}")
        if len(response.content) > MAX_MEDIA_BYTES:
            raise MediaTooLargeError(f"Media is {len(response.content)} bytes")
        return MediaFile(content=response.content, mime=str(info.get("mime_type") or ""))

    def upload_media(self, content: bytes, mime: str, filename: str) -> str:
        """Upload a file (e.g. a receipt PDF) for a template header; returns the media id."""
        response = self._request(
            "POST",
            f"{self._api}/{self._phone_number_id}/media",
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (filename, content, mime)},
        )
        data = self._check(response)
        if not data.get("id"):
            raise WhatsAppError("WhatsApp answered without a media id")
        return str(data["id"])
