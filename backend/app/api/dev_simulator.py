"""Local WhatsApp simulator (development only; never mounted in production).

Page: GET /dev/whatsapp. What you type there becomes a webhook payload in Meta's exact format
and goes through the same function as the real webhook (parser → queue → idempotency → router).
Delivery statuses come back through the same status updater: "delivered" on the next refresh,
"read" while that chat is open.

No authentication: it lists every test tenant and owner in the local database. It is only
mounted when APP_ENV is not production and MESSAGING_MODE=simulator.
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, select

from app.api.deps import DbSession
from app.channels.whatsapp import get_simulator
from app.channels.whatsapp.base import MAX_MEDIA_BYTES, StatusUpdate
from app.channels.whatsapp.simulator import new_provider_id
from app.core.config import get_settings
from app.core.timezone import utc_now
from app.domain.phones import InvalidPhoneError, normalize_phone
from app.models import (
    Building,
    Lease,
    Message,
    Organization,
    Tenant,
    Unit,
    User,
    WhatsAppConversation,
)
from app.models.enums import MessageDirection, MessageStatus
from app.services.inbound import find_candidates, handle_webhook_payload, is_opted_out
from app.services.messaging import SESSION_WINDOW, apartment_label, latest_consent

router = APIRouter(prefix="/dev/whatsapp", include_in_schema=False)
PAGE = Path(__file__).resolve().parent / "static" / "simulator.html"
SIMULATED_PHONE_NUMBER_ID = "SIMULATOR"
# A status for a message whose send hasn't committed yet waits for the next refresh.
STATUS_GRACE = dt.timedelta(seconds=5)
UNDELIVERABLE = (131026, "Message undeliverable")


def _phone(raw: str) -> str:
    try:
        return normalize_phone(raw)
    except InvalidPhoneError:
        raise HTTPException(422, "Número inválido") from None


class SimulatedMessage(BaseModel):
    phone: str
    type: Literal["text", "button", "interactive", "image", "document", "audio"]
    text: str | None = Field(default=None, max_length=4096)
    payload: str | None = Field(default=None, max_length=256)
    interactive_type: Literal["button_reply", "list_reply"] = "button_reply"
    media_id: str | None = None
    mime: str | None = None
    filename: str | None = None
    context_id: str | None = None
    repeat_last: bool = False


def _meta_payload(message: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "SIMULATOR-WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "18090000000",
                                "phone_number_id": get_settings().WHATSAPP_PHONE_NUMBER_ID
                                or SIMULATED_PHONE_NUMBER_ID,
                            },
                            "contacts": [
                                {"profile": {"name": "Simulador"}, "wa_id": message["from"]}
                            ],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def _status_payload(update: StatusUpdate, phone: str) -> dict[str, Any]:
    status: dict[str, Any] = {
        "id": update.provider_message_id,
        "status": update.status,
        "timestamp": str(int(update.timestamp.timestamp())),
        "recipient_id": phone.lstrip("+"),
    }
    if update.error_code is not None:
        status["errors"] = [{"code": update.error_code, "title": update.error_title}]
    payload = _meta_payload({"from": phone.lstrip("+")})
    value = payload["entry"][0]["changes"][0]["value"]
    value.pop("messages")
    value.pop("contacts")
    value["statuses"] = [status]
    return payload


def _meta_message(data: SimulatedMessage, phone: str, provider_id: str) -> dict[str, Any]:
    message: dict[str, Any] = {
        "from": phone.lstrip("+"),
        "id": provider_id,
        "timestamp": str(int(utc_now().timestamp())),
        "type": data.type,
    }
    if data.context_id:
        message["context"] = {"from": "18090000000", "id": data.context_id}
    if data.type == "text":
        message["text"] = {"body": data.text or ""}
    elif data.type == "button":
        message["button"] = {"payload": data.payload, "text": data.text}
    elif data.type == "interactive":
        message["interactive"] = {
            "type": data.interactive_type,
            data.interactive_type: {"id": data.payload, "title": data.text},
        }
    elif data.type in {"image", "document"}:
        media: dict[str, Any] = {"id": data.media_id, "mime_type": data.mime}
        if data.text:
            media["caption"] = data.text
        if data.type == "document":
            media["filename"] = data.filename
        message[data.type] = media
    elif data.type == "audio":
        message["audio"] = {"id": "sim-audio", "mime_type": "audio/ogg; codecs=opus", "voice": True}
    return message


@router.get("")
def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html; charset=utf-8")


@router.get("/api/contacts")
def contacts(db: DbSession) -> list[dict[str, Any]]:
    by_phone: dict[str, dict[str, Any]] = {}

    def entry(phone: str) -> dict[str, Any]:
        return by_phone.setdefault(phone, {"phone": phone, "name": None, "roles": []})

    rows = db.execute(
        select(Tenant, Lease, Organization, Unit, Building)
        .join(
            Lease,
            and_(Lease.tenant_id == Tenant.id, Lease.organization_id == Tenant.organization_id),
        )
        .join(Organization, Organization.id == Lease.organization_id)
        .join(Unit, and_(Unit.id == Lease.unit_id, Unit.organization_id == Lease.organization_id))
        .outerjoin(
            Building,
            and_(Building.id == Unit.building_id, Building.organization_id == Unit.organization_id),
        )
        .where(Lease.is_active)
        .order_by(Tenant.full_name)
    ).all()
    for tenant, _lease, org, unit, building in rows:
        item = entry(tenant.phone)
        item["name"] = item["name"] or tenant.full_name
        item["roles"].append(
            {"role": "tenant", "org": org.name, "unit": apartment_label(unit, building)}
        )
    for user, org in db.execute(
        select(User, Organization)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.whatsapp_phone.is_not(None))
    ).all():
        item = entry(user.whatsapp_phone)
        item["name"] = item["name"] or user.full_name
        item["roles"].append({"role": "owner", "org": org.name, "unit": None})
    for phone in get_simulator().phones():
        entry(phone)
    return sorted(by_phone.values(), key=lambda c: (c["name"] is None, c["name"] or c["phone"]))


def _sync_statuses(db: DbSession, phone: str, viewing: bool) -> None:
    """Simulated delivery: delivered on refresh, read while the chat is open."""
    simulator = get_simulator()
    now = utc_now()
    for item in simulator.chat(phone):
        if item["direction"] != "out" or item["status"] in {"read", "failed"}:
            continue
        targets = ["delivered"] if item["status"] == "sent" else []
        if viewing:
            targets.append("read")
        if not targets:
            continue
        in_db = db.scalar(select(Message.id).where(Message.provider_message_id == item["id"]))
        if in_db is None and now - dt.datetime.fromisoformat(item["at"]) < STATUS_GRACE:
            continue  # its send job hasn't committed yet
        for target in targets:
            update = StatusUpdate(item["id"], target, now)  # type: ignore[arg-type]
            handle_webhook_payload(db, _status_payload(update, phone))
            simulator.set_status(phone, item["id"], target)
    db.commit()


@router.get("/api/chats/{phone}")
def chat(phone: str, db: DbSession, viewing: bool = False) -> dict[str, Any]:
    phone = _phone(phone)
    _sync_statuses(db, phone, viewing)
    entries = get_simulator().chat(phone)
    ids = [e["id"] for e in entries]
    stored = {
        m.provider_message_id: m
        for m in db.scalars(select(Message).where(Message.provider_message_id.in_(ids))).all()
    }
    for item in entries:
        message = stored.get(item["id"])
        item["stored"] = message is not None
        if message is not None and message.direction == MessageDirection.OUTBOUND:
            item["status"] = message.status.value
            item["kind"] = message.kind.value if message.kind else None
            item["error"] = message.error

    last_inbound = db.scalar(
        select(WhatsAppConversation.last_inbound_at).where(WhatsAppConversation.phone == phone)
    )
    window_until = last_inbound + SESSION_WINDOW if last_inbound else None
    leases = []
    for candidate in find_candidates(db, phone):
        if candidate.lease is None:
            continue
        consent = latest_consent(db, candidate.lease.id)
        leases.append(
            {
                "org": candidate.org.name,
                "unit": apartment_label(candidate.unit, candidate.building)
                if candidate.unit
                else "",
                "consent": "baja"
                if is_opted_out(db, candidate.lease)
                else ("vigente" if consent else "sin consentimiento"),
            }
        )
    scheduled = [
        {"body": m.body, "scheduled_for": m.scheduled_for.isoformat(), "kind": m.kind}
        for m in db.scalars(
            select(Message)
            .where(
                Message.contact_phone == phone,
                Message.status == MessageStatus.QUEUED,
                Message.scheduled_for.is_not(None),
            )
            .order_by(Message.scheduled_for)
        ).all()
        if m.scheduled_for
    ]
    return {
        "phone": phone,
        "entries": entries,
        "window_open_until": window_until.isoformat()
        if window_until and window_until > utc_now()
        else None,
        "leases": leases,
        "scheduled": scheduled,
    }


@router.post("/api/messages")
def send_as_contact(data: SimulatedMessage, db: DbSession) -> dict[str, Any]:
    simulator = get_simulator()
    phone = _phone(data.phone)
    last_key = f"{simulator.prefix}:last_in:{phone}"
    if data.repeat_last:
        raw = simulator.redis.get(last_key)
        if raw is None:
            raise HTTPException(409, "Todavía no hay un mensaje para repetir")
        payload = json.loads(raw)
        provider_id = payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
        simulator.record_inbound(phone, provider_id, {"type": "repeat", "text": "(repetido)"})
    else:
        provider_id = new_provider_id()
        payload = _meta_payload(_meta_message(data, phone, provider_id))
        simulator.redis.set(last_key, json.dumps(payload), ex=24 * 3600)
        simulator.record_inbound(
            phone,
            provider_id,
            {"type": data.type, "text": data.text, "filename": data.filename},
        )
    handle_webhook_payload(db, payload)
    db.commit()
    return {"provider_message_id": provider_id}


@router.post("/api/media")
def upload_media(file: UploadFile) -> dict[str, Any]:
    content = file.file.read(MAX_MEDIA_BYTES + 1)
    if len(content) > MAX_MEDIA_BYTES:
        raise HTTPException(413, "El archivo pasa de 10 MB")
    mime = file.content_type or "application/octet-stream"
    media_id = get_simulator().store_media(content, mime, file.filename)
    return {"media_id": media_id, "mime": mime, "filename": file.filename}


@router.post("/api/chats/{phone}/fail-last")
def fail_last(phone: str, db: DbSession) -> dict[str, Any]:
    phone = _phone(phone)
    simulator = get_simulator()
    outbound = [e for e in simulator.chat(phone) if e["direction"] == "out"]
    if not outbound:
        raise HTTPException(409, "No hay mensajes enviados a este número")
    last = outbound[-1]
    update = StatusUpdate(last["id"], "failed", utc_now(), *UNDELIVERABLE)
    handle_webhook_payload(db, _status_payload(update, phone))
    simulator.set_status(phone, last["id"], "failed")
    db.commit()
    return {"provider_message_id": last["id"]}


@router.delete("/api/chats/{phone}")
def clear_chat(phone: str) -> dict[str, bool]:
    get_simulator().clear(_phone(phone))
    return {"cleared": True}
