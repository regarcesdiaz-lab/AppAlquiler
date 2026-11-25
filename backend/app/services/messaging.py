"""Outbound WhatsApp messages: the rules decide, the queue sends, statuses come back.

``send`` never talks to WhatsApp. It asks the rules engine (CLAUDE.md, rule 4), stores the
``Message`` as ``queued`` with its timeline event, and schedules the ``send_message`` job (right
away, or at the next contact window when only the window blocks it). The job checks the rules
again at send time (a BAJA may have arrived meanwhile), picks a session message when the
number's 24-hour window is open or the approved template otherwise, and records the result.
"""

import datetime as dt
import uuid
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.whatsapp import get_whatsapp_channel
from app.channels.whatsapp.base import (
    Button,
    ButtonsSend,
    OutboundContent,
    SessionContent,
    StatusUpdate,
    TemplateSend,
    TextSend,
    WhatsAppError,
    content_from_json,
    content_to_json,
)
from app.channels.whatsapp.templates import Tone, catalog, tone_for
from app.core.timezone import business_zone, utc_now
from app.domain.holidays import load_holidays
from app.domain.messaging_rules import (
    BUSINESS_INITIATED,
    BlockReason,
    Consent,
    ContactRules,
    MessageKind,
    SendDecision,
    SendRequest,
    next_contact_time,
    validate_send,
)
from app.jobs.runner import job, schedule
from app.models import (
    AssistantProfile,
    Building,
    ConsentRecord,
    Lease,
    Message,
    Organization,
    Payment,
    PaymentPromise,
    Tenant,
    Unit,
    User,
    WhatsAppConversation,
)
from app.models.enums import (
    ActorType,
    ContactType,
    MessageChannel,
    MessageContentType,
    MessageDirection,
    MessageStatus,
    PaymentStatus,
    PromiseStatus,
)
from app.services.lease_state import RuleSource, contact_rules
from app.services.timeline import record_event

SESSION_WINDOW = dt.timedelta(hours=24)
WEEK = dt.timedelta(days=7)
# A job that wakes up a little early still sends; a much earlier one waits for its time.
EARLY_TOLERANCE = dt.timedelta(minutes=1)
PAYLOAD_PREFIX = "v1"
APARTMENT_PREFIXES = ("apto", "apt", "apartamento", "casa", "local", "estudio", "ph")
DEFAULT_ASSISTANT_NAME = "Asistente"
NO_NEXT_TIME = frozenset(
    {BlockReason.NO_CONSENT, BlockReason.CONSENT_REVOKED, BlockReason.PENDING_PAYMENT}
)

STATUS_RANK = {
    MessageStatus.QUEUED: 0,
    MessageStatus.SENT: 1,
    MessageStatus.DELIVERED: 2,
    MessageStatus.READ: 3,
}


class SendBlockedError(Exception):
    def __init__(self, decision: SendDecision, next_contact_time: dt.datetime | None) -> None:
        super().__init__(", ".join(decision.reasons))
        self.reasons = decision.reasons
        self.next_contact_time = next_contact_time


@dataclass
class Recipient:
    org: Organization
    phone: str
    contact_type: ContactType
    tone: Tone
    zone: ZoneInfo
    rules: ContactRules = field(default_factory=ContactRules)
    lease: Lease | None = None
    tenant: Tenant | None = None
    user: User | None = None
    assistant_name: str = DEFAULT_ASSISTANT_NAME

    @property
    def full_name(self) -> str:
        return self.tenant.full_name if self.tenant else (self.user.full_name if self.user else "")

    @property
    def tenant_name(self) -> str:
        return self.full_name

    @property
    def first_name(self) -> str:
        return first_name(self.full_name)


def first_name(full_name: str) -> str:
    parts = full_name.split()
    return parts[0] if parts else full_name


def apartment_label(unit: Unit, building: Building | None) -> str:
    code = unit.code.strip()
    label = code if code.lower().startswith(APARTMENT_PREFIXES) else f"Apto {code}"
    return f"{label}, {building.name}" if building else label


def button_payload(action: str, lease_id: uuid.UUID | str | None) -> str:
    return f"{PAYLOAD_PREFIX}:{action}:{lease_id or ''}"


def parse_payload(payload: str | None) -> tuple[str, str] | None:
    """("action", "id") from "v1:<action>:<id>"; None for anything else."""
    if not payload:
        return None
    parts = payload.split(":", 2)
    if len(parts) != 3 or parts[0] != PAYLOAD_PREFIX:
        return None
    return parts[1], parts[2]


def recipient_for_lease(db: Session, lease: Lease) -> Recipient:
    org = db.get(Organization, lease.organization_id)
    tenant = db.get(Tenant, lease.tenant_id)
    assert org is not None and tenant is not None
    assistant = db.scalar(
        select(AssistantProfile).where(AssistantProfile.organization_id == org.id)
    )
    return Recipient(
        org=org,
        phone=tenant.phone,
        contact_type=ContactType.TENANT,
        tone=tone_for(assistant.tone if assistant else None),
        zone=business_zone(org.timezone),
        rules=contact_rules(RuleSource(db, org.id).for_lease(lease)),
        lease=lease,
        tenant=tenant,
        assistant_name=assistant.display_name if assistant else DEFAULT_ASSISTANT_NAME,
    )


def recipient_for_owner(db: Session, user: User) -> Recipient:
    org = db.get(Organization, user.organization_id)
    assert org is not None and user.whatsapp_phone
    # The owner is always addressed as "tú" (docs/BRAND.md).
    return Recipient(
        org=org,
        phone=user.whatsapp_phone,
        contact_type=ContactType.OWNER,
        tone="tu",
        zone=business_zone(org.timezone),
        user=user,
    )


def lease_params(db: Session, to: Recipient) -> dict[str, str]:
    """Variables every tenant template shares: name, assistant, owner and apartment."""
    assert to.lease is not None
    unit = db.get(Unit, to.lease.unit_id)
    assert unit is not None
    building = db.get(Building, unit.building_id) if unit.building_id else None
    return {
        "nombre": to.first_name,
        "asistente": to.assistant_name,
        "propietario": to.org.name,
        "apartamento": apartment_label(unit, building),
    }


def latest_consent(db: Session, lease_id: uuid.UUID) -> ConsentRecord | None:
    return db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.lease_id == lease_id)
        .order_by(ConsentRecord.revoked_at.is_(None).desc(), ConsentRecord.granted_at.desc())
        .limit(1)
    )


def window_open(db: Session, phone: str, now: dt.datetime) -> bool:
    """Meta's 24-hour customer service window for the central number and this phone."""
    last = db.scalar(
        select(WhatsAppConversation.last_inbound_at).where(WhatsAppConversation.phone == phone)
    )
    return last is not None and last > now - SESSION_WINDOW


def _decision(
    db: Session,
    to: Recipient,
    kind: MessageKind,
    now: dt.datetime,
    exclude_message_id: uuid.UUID | None = None,
) -> SendDecision:
    consent: Consent | None = None
    reminders: list[dt.datetime] = []
    promise_date: dt.date | None = None
    pending_payment = False
    if to.lease is not None:
        row = latest_consent(db, to.lease.id)
        if row is not None:
            consent = Consent(granted_at=row.granted_at, revoked_at=row.revoked_at)
        if kind == MessageKind.COLLECTION_REMINDER:
            stmt = select(Message.created_at).where(
                Message.organization_id == to.org.id,
                Message.lease_id == to.lease.id,
                Message.direction == MessageDirection.OUTBOUND,
                Message.kind == MessageKind.COLLECTION_REMINDER,
                Message.status != MessageStatus.FAILED,
                Message.created_at >= now - WEEK,
            )
            if exclude_message_id is not None:
                stmt = stmt.where(Message.id != exclude_message_id)
            reminders = list(db.scalars(stmt).all())
            promise_date = db.scalar(
                select(PaymentPromise.promised_date).where(
                    PaymentPromise.organization_id == to.org.id,
                    PaymentPromise.lease_id == to.lease.id,
                    PaymentPromise.status == PromiseStatus.ACTIVE,
                )
            )
            pending_payment = (
                db.scalar(
                    select(Payment.id)
                    .where(
                        Payment.organization_id == to.org.id,
                        Payment.lease_id == to.lease.id,
                        Payment.status == PaymentStatus.REPORTED,
                    )
                    .limit(1)
                )
                is not None
            )
    elif kind in BUSINESS_INITIATED:
        raise ValueError(f"{kind} messages need a lease")
    return validate_send(
        SendRequest(
            kind=kind,
            at=now,
            consent=consent,
            reminders_sent=reminders,
            active_promise_date=promise_date,
            has_pending_payment=pending_payment,
        ),
        to.rules,
        load_holidays(),
        to.zone,
    )


def _next_window(to: Recipient, now: dt.datetime) -> dt.datetime | None:
    try:
        return next_contact_time(now, to.rules, load_holidays(), to.zone)
    except ValueError:
        return None


def template_content(
    to: Recipient, template_key: str, params: dict[str, str]
) -> tuple[TemplateSend, SessionContent]:
    """The approved template, and the same text as a session message for an open window."""
    cat = catalog()
    definition = cat.template(template_key)
    body = cat.render_template(template_key, to.tone, params)
    footer = cat.footer(to.tone) if definition.footer else None
    lease_id = to.lease.id if to.lease else None
    buttons = tuple(Button(button_payload(b.action, lease_id), b.text) for b in definition.buttons)
    template = TemplateSend(
        name=definition.meta_name(to.tone),
        language=cat.language,
        params={name: params[name] for name in definition.variables},
        buttons=buttons,
        footer=footer,
        preview=body,
    )
    session: SessionContent
    if buttons:
        session = ButtonsSend(body=body, buttons=buttons, footer=footer)
    else:
        session = TextSend(body=f"{body}\n\n{footer}" if footer else body)
    return template, session


def _event_payload(message: Message) -> dict[str, Any]:
    return {
        "message_id": message.id,
        "kind": message.kind.value if message.kind else None,
        "template": message.payload.get("template_key"),
        "text_key": message.payload.get("text_key"),
        "contact_type": message.contact_type.value,
    }


def send(
    db: Session,
    to: Recipient,
    kind: MessageKind,
    *,
    template: str | None = None,
    params: dict[str, str] | None = None,
    session: SessionContent | None = None,
    text_key: str | None = None,
    actor: ActorType = ActorType.AGENT,
    user_id: uuid.UUID | None = None,
    wait_for_window: bool = True,
) -> Message:
    """Validate and queue one message. Raises SendBlockedError when the rules say no."""
    if template is None and session is None:
        raise ValueError("A message needs a template or a session content")
    now = utc_now()
    decision = _decision(db, to, kind, now)
    scheduled_for: dt.datetime | None = None
    if not decision.allowed:
        next_time = None
        # Waiting for a time window makes sense; waiting for consent or the owner doesn't.
        if not NO_NEXT_TIME & set(decision.reasons):
            next_time = _next_window(to, now)
        if decision.only_outside_window and wait_for_window and next_time is not None:
            scheduled_for = next_time
        else:
            if to.lease is not None:
                record_event(
                    db,
                    to.org.id,
                    "message.blocked",
                    lease=to.lease,
                    actor=actor,
                    user_id=user_id,
                    kind=kind.value,
                    template=template,
                    reasons=[r.value for r in decision.reasons],
                )
            raise SendBlockedError(decision, next_time)

    payload: dict[str, Any] = {"text_key": text_key}
    body = ""
    if template is not None:
        template_send, derived_session = template_content(to, template, params or {})
        payload |= {"template_key": template, "template": content_to_json(template_send)}
        session = session or derived_session
        body = template_send.preview
    if session is not None:
        payload["session"] = content_to_json(session)
        body = body or session.body

    message = Message(
        organization_id=to.org.id,
        lease_id=to.lease.id if to.lease else None,
        tenant_id=to.tenant.id if to.tenant else None,
        user_id=to.user.id if to.user else None,
        channel=MessageChannel.WHATSAPP,
        direction=MessageDirection.OUTBOUND,
        kind=kind,
        contact_type=to.contact_type,
        contact_phone=to.phone,
        content_type=MessageContentType.TEXT,
        body=body,
        payload=payload,
        status=MessageStatus.QUEUED,
        scheduled_for=scheduled_for,
        created_at=now,
        updated_at=now,
    )
    db.add(message)
    db.flush()
    record_event(
        db,
        to.org.id,
        "message.scheduled" if scheduled_for else "message.queued",
        lease=to.lease,
        actor=actor,
        user_id=user_id,
        scheduled_for=scheduled_for,
        **_event_payload(message),
    )
    schedule(db, "send_message", str(message.id), at=scheduled_for)
    return message


def send_welcome(db: Session, lease: Lease, user_id: uuid.UUID | None = None) -> Message | None:
    """Welcome from the owner's assistant when a lease is created with consent.

    Outside the contact window it is scheduled for the next valid moment. Never raises: a
    blocked welcome (e.g. no consent) must not undo the lease.
    """
    to = recipient_for_lease(db, lease)
    try:
        return send(
            db,
            to,
            MessageKind.WELCOME,
            template="bienvenida",
            params=lease_params(db, to),
            actor=ActorType.SYSTEM,
            user_id=user_id,
        )
    except SendBlockedError:
        return None


def _recipient_for_message(db: Session, message: Message) -> Recipient | None:
    if message.lease_id is not None:
        lease = db.get(Lease, message.lease_id)
        return recipient_for_lease(db, lease) if lease else None
    if message.user_id is not None:
        user = db.get(User, message.user_id)
        return recipient_for_owner(db, user) if user and user.whatsapp_phone else None
    return None


def _fail(db: Session, message: Message, lease: Lease | None, event: str, error: str) -> None:
    message.status = MessageStatus.FAILED
    message.error = error[:500]
    message.status_updated_at = utc_now()
    db.flush()
    record_event(
        db,
        message.organization_id,
        event,
        lease=lease,
        actor=ActorType.SYSTEM,
        error=message.error,
        **_event_payload(message),
    )


def _content_type(content: OutboundContent) -> MessageContentType:
    if isinstance(content, TemplateSend):
        return MessageContentType.TEMPLATE
    if isinstance(content, TextSend):
        return MessageContentType.TEXT
    return MessageContentType.INTERACTIVE


def deliver(db: Session, message_id: uuid.UUID) -> None:
    """The send_message job: check again, pick session or template, hand it to the channel."""
    message = db.scalar(select(Message).where(Message.id == message_id).with_for_update())
    if message is None or message.status != MessageStatus.QUEUED:
        return  # already sent, failed or cancelled: jobs may run twice
    now = utc_now()
    if message.scheduled_for is not None and message.scheduled_for > now + EARLY_TOLERANCE:
        schedule(db, "send_message", str(message.id), at=message.scheduled_for)
        return
    to = _recipient_for_message(db, message)
    if to is None:
        _fail(db, message, None, "message.failed", "recipient_not_found")
        return
    assert message.kind is not None
    decision = _decision(db, to, message.kind, now, exclude_message_id=message.id)
    if not decision.allowed:
        next_time = _next_window(to, now) if decision.only_outside_window else None
        if next_time is not None:
            message.scheduled_for = next_time
            db.flush()
            record_event(
                db,
                message.organization_id,
                "message.scheduled",
                lease=to.lease,
                actor=ActorType.SYSTEM,
                scheduled_for=next_time,
                **_event_payload(message),
            )
            schedule(db, "send_message", str(message.id), at=next_time)
            return
        reasons = ", ".join(r.value for r in decision.reasons)
        _fail(db, message, to.lease, "message.cancelled", f"cancelled: {reasons}")
        return

    content: OutboundContent | None = None
    if window_open(db, to.phone, now) and message.payload.get("session"):
        content = content_from_json(message.payload["session"])
    elif message.payload.get("template"):
        content = content_from_json(message.payload["template"])
    if content is None:
        _fail(db, message, to.lease, "message.failed", "window_closed_without_template")
        return

    try:
        provider_id = get_whatsapp_channel().send(to.phone, content)
    except WhatsAppError as exc:
        code = f"{exc.code}: " if exc.code is not None else ""
        _fail(db, message, to.lease, "message.failed", f"{code}{exc}")
        return
    message.provider_message_id = provider_id
    message.status = MessageStatus.SENT
    message.content_type = _content_type(content)
    message.template = content.name if isinstance(content, TemplateSend) else None
    message.sent_at = now
    message.status_updated_at = now
    db.flush()
    record_event(
        db,
        message.organization_id,
        "message.sent",
        lease=to.lease,
        actor=ActorType.SYSTEM,
        sent_as=message.content_type.value,
        provider_message_id=provider_id,
        **_event_payload(message),
    )


@job("send_message")
def send_message_job(db: Session, message_id: str) -> None:
    deliver(db, uuid.UUID(message_id))


def cancel_queued(db: Session, lease: Lease, reason: str) -> int:
    """Cancel the lease's business-initiated messages that haven't gone out (e.g. after BAJA)."""
    queued = db.scalars(
        select(Message).where(
            Message.organization_id == lease.organization_id,
            Message.lease_id == lease.id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.status == MessageStatus.QUEUED,
            Message.kind.in_(BUSINESS_INITIATED),
        )
    ).all()
    for message in queued:
        _fail(db, message, lease, "message.cancelled", f"cancelled: {reason}")
    return len(queued)


def apply_status(db: Session, update: StatusUpdate) -> bool:
    """Delivery status from the provider. Only moves forward; returns True when it changed."""
    message = db.scalar(
        select(Message)
        .where(
            Message.provider_message_id == update.provider_message_id,
            Message.direction == MessageDirection.OUTBOUND,
        )
        .with_for_update()
    )
    if message is None or message.status == MessageStatus.FAILED:
        return False
    new = MessageStatus(update.status)
    if new == MessageStatus.FAILED:
        if message.status == MessageStatus.READ:
            return False
        error = " ".join(
            part
            for part in (
                f"{update.error_code}:" if update.error_code is not None else None,
                update.error_title,
            )
            if part
        )
        message.error = (error or "failed")[:500]
    elif STATUS_RANK[new] <= STATUS_RANK[message.status]:
        return False
    message.status = new
    message.status_updated_at = update.timestamp
    db.flush()
    lease = db.get(Lease, message.lease_id) if message.lease_id else None
    record_event(
        db,
        message.organization_id,
        f"message.{new.value}",
        lease=lease,
        actor=ActorType.SYSTEM,
        provider_message_id=update.provider_message_id,
        error=message.error if new == MessageStatus.FAILED else None,
        **_event_payload(message),
    )
    return True
