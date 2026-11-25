"""Meta WhatsApp Cloud API webhook payload → InboundEvent / StatusUpdate.

Shape (field "messages"):
    {"object": "whatsapp_business_account",
     "entry": [{"id": "<WABA id>", "changes": [{"field": "messages", "value": {
         "metadata": {"phone_number_id": "..."},
         "messages": [{"from": "18095550101", "id": "wamid...", "timestamp": "1732460000",
                       "type": "text", "text": {"body": "hola"}, "context": {"id": "wamid..."}}],
         "statuses": [{"id": "wamid...", "status": "delivered", "timestamp": "...",
                       "errors": [{"code": 131026, "title": "..."}]}]}}]}]}

Unknown parts are skipped (and logged), never fatal: Meta retries a webhook that fails, and a
retry storm would not make an unknown message type understandable.
"""

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any

from app.channels.whatsapp.base import InboundEvent, StatusUpdate
from app.domain.phones import InvalidPhoneError, normalize_phone

logger = logging.getLogger(__name__)

KNOWN_STATUSES = {"sent", "delivered", "read", "failed"}
MEDIA_TYPES = {"image", "document", "audio", "video", "sticker"}


@dataclass
class ParsedWebhook:
    messages: list[InboundEvent] = field(default_factory=list)
    statuses: list[StatusUpdate] = field(default_factory=list)


def _timestamp(raw: Any) -> dt.datetime:
    try:
        return dt.datetime.fromtimestamp(int(raw), tz=dt.UTC)
    except (TypeError, ValueError):
        return dt.datetime.now(dt.UTC)


def _message(raw: dict[str, Any]) -> InboundEvent | None:
    try:
        provider_id = str(raw["id"])
        phone = normalize_phone("+" + str(raw["from"]).lstrip("+"))
    except (KeyError, InvalidPhoneError):
        logger.warning("WhatsApp webhook: message without a valid id or sender skipped")
        return None
    kind = str(raw.get("type") or "other")
    values: dict[str, Any] = {
        "provider_message_id": provider_id,
        "phone": phone,
        "timestamp": _timestamp(raw.get("timestamp")),
        "context_id": (raw.get("context") or {}).get("id"),
    }
    if kind == "text":
        values |= {"type": "text", "text": (raw.get("text") or {}).get("body", "")}
    elif kind == "button":
        button = raw.get("button") or {}
        values |= {"type": "button", "text": button.get("text"), "payload": button.get("payload")}
    elif kind == "interactive":
        interactive = raw.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        values |= {"type": "interactive", "text": reply.get("title"), "payload": reply.get("id")}
    elif kind in MEDIA_TYPES:
        media = raw.get(kind) or {}
        values |= {
            "type": kind,
            "media_id": media.get("id"),
            "media_mime": media.get("mime_type"),
            "media_filename": media.get("filename"),
            "caption": media.get("caption"),
        }
    elif kind in {"location", "reaction"}:
        values |= {"type": kind}
    else:
        values |= {"type": "other"}
    return InboundEvent(**values)


def _status(raw: dict[str, Any]) -> StatusUpdate | None:
    status = raw.get("status")
    if status not in KNOWN_STATUSES or not raw.get("id"):
        logger.info("WhatsApp webhook: status %r skipped", status)
        return None
    errors = raw.get("errors") or [{}]
    first = errors[0] if errors else {}
    code = first.get("code")
    return StatusUpdate(
        provider_message_id=str(raw["id"]),
        status=status,
        timestamp=_timestamp(raw.get("timestamp")),
        error_code=int(code) if isinstance(code, int | str) and str(code).isdigit() else None,
        error_title=first.get("title") or first.get("message"),
    )


def parse_webhook(payload: Any, phone_number_id: str | None = None) -> ParsedWebhook:
    """Messages and statuses of a webhook call. ``phone_number_id`` filters other numbers."""
    parsed = ParsedWebhook()
    if not isinstance(payload, dict):
        return parsed
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or []:
            if (change or {}).get("field") != "messages":
                continue
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            if phone_number_id and metadata.get("phone_number_id") != phone_number_id:
                logger.warning("WhatsApp webhook for another phone number id skipped")
                continue
            for raw in value.get("messages") or []:
                if isinstance(raw, dict) and (event := _message(raw)) is not None:
                    parsed.messages.append(event)
            for raw in value.get("statuses") or []:
                if isinstance(raw, dict) and (status := _status(raw)) is not None:
                    parsed.statuses.append(status)
    return parsed
