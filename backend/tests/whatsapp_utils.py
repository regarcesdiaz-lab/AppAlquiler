"""Helpers for WhatsApp tests: example payloads, inbound events and the simulator's chat."""

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.channels.whatsapp.base import InboundEvent
from app.channels.whatsapp.simulator import SimulatorChannel
from app.core import timezone as tz
from app.services.inbound import process_inbound

FIXTURES = Path(__file__).parent / "fixtures" / "whatsapp"

JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.7\n" + b"0" * 64
EXE_BYTES = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 64


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def inbound(
    db: Session,
    phone: str,
    text: str | None = None,
    *,
    type: str = "text",
    payload: str | None = None,
    context_id: str | None = None,
    media_id: str | None = None,
    mime: str | None = None,
    filename: str | None = None,
    provider_id: str | None = None,
    at: dt.datetime | None = None,
) -> str:
    """Route one inbound message through the real router; returns how it was routed."""
    event = InboundEvent(
        provider_message_id=provider_id or f"wamid.TEST{uuid.uuid4().hex}",
        phone=phone,
        timestamp=at or tz.utc_now(),  # the frozen clock in tests
        type=type,  # type: ignore[arg-type]
        text=text,
        payload=payload,
        context_id=context_id,
        media_id=media_id,
        media_mime=mime,
        media_filename=filename,
    )
    return process_inbound(db, event)


def outbound(simulator: SimulatorChannel, phone: str) -> list[dict[str, Any]]:
    return [e for e in simulator.chat(phone) if e["direction"] == "out"]


def text_of(entry: dict[str, Any]) -> str:
    content = entry["content"]
    return content.get("preview") or content.get("body") or ""


def last_text(simulator: SimulatorChannel, phone: str) -> str:
    sent = outbound(simulator, phone)
    assert sent, f"nothing was sent to {phone}"
    return text_of(sent[-1])
