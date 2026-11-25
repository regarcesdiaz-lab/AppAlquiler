"""Inbound WhatsApp router: the same path for Meta's webhook and the local simulator.

    webhook / simulator → parse_webhook → process_inbound job → this module

1. **Once**: the provider message id is claimed in ``whatsapp_inbound_events`` in the same
   transaction as the processing; a repeat is dropped, a failure rolls the claim back.
2. **Who writes**: the number's candidates are every active lease whose tenant has that phone
   (any organization) and the owner whose ``whatsapp_phone`` it is.
3. **Which one**: a reply to one of our messages (WhatsApp context) → that message's lease; a
   button → the lease in its payload (if it is that tenant's); a recent choice (30 min); a
   single candidate. Several candidates → ask with a list and keep the message until the
   answer. No candidate → a polite generic reply (at most once a day).
   BAJA and ALTA without context apply to all the number's leases, without asking.
4. **What to do**: keywords (MENÚ, BAJA, ALTA), buttons and list rows, receipts (images and
   PDF, checked by content), free text and unplayable media (a pending item for the owner).

Queries here look the phone up across organizations on purpose: an inbound message has no
signed-in organization. Once the lease or owner is resolved, everything is scoped to its
organization.
"""

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.agents import owner_assistant
from app.channels.whatsapp import get_whatsapp_channel
from app.channels.whatsapp.base import (
    InboundEvent,
    ListRowOut,
    ListSend,
    MediaTooLargeError,
    SessionContent,
    TextSend,
    WhatsAppError,
)
from app.channels.whatsapp.parser import ParsedWebhook, parse_webhook
from app.channels.whatsapp.templates import catalog
from app.core.config import get_settings
from app.core.timezone import local_today, utc_now
from app.domain.escalation import (
    OwnerTask,
    consent_revoked_task,
    tenant_message_task,
    tenant_receipt_task,
    tenant_unplayable_task,
)
from app.domain.formatting import format_dop, format_long_date
from app.domain.keywords import Keyword, detect_keyword
from app.domain.media import EXTENSIONS, MediaCheck, RejectReason, check_receipt_file
from app.domain.messaging_rules import MessageKind
from app.domain.periods import add_months, due_date, period_of
from app.jobs.runner import job, schedule
from app.models import (
    BankAccount,
    Building,
    ConsentRecord,
    InboxItem,
    Lease,
    Message,
    Organization,
    Tenant,
    Unit,
    User,
    WhatsAppConversation,
    WhatsAppInboundEvent,
)
from app.models.enums import (
    AccountType,
    ActorType,
    ConsentMethod,
    ContactType,
    InboxItemStatus,
    InboxItemType,
    MessageChannel,
    MessageContentType,
    MessageDirection,
    MessageStatus,
)
from app.services import messaging
from app.services.lease_state import load_lease_state
from app.services.messaging import (
    Recipient,
    SendBlockedError,
    apartment_label,
    button_payload,
    latest_consent,
    parse_payload,
    recipient_for_lease,
    recipient_for_owner,
)
from app.services.storage import get_storage, organization_prefix
from app.services.timeline import record_event

logger = logging.getLogger(__name__)

CHOICE_TTL = dt.timedelta(minutes=30)
FREE_TEXT_ACK_EVERY = dt.timedelta(minutes=30)
NOTICE_EVERY = dt.timedelta(hours=24)
MAX_CHOICES = 10
UNPLAYABLE_TYPES = {"audio", "video", "sticker", "location", "other"}

CONTENT_TYPES = {
    "text": MessageContentType.TEXT,
    "button": MessageContentType.INTERACTIVE,
    "interactive": MessageContentType.INTERACTIVE,
    "image": MessageContentType.IMAGE,
    "document": MessageContentType.DOCUMENT,
    "audio": MessageContentType.AUDIO,
    "video": MessageContentType.VIDEO,
}
PLACEHOLDERS = {
    "image": "[Imagen]",
    "document": "[Documento]",
    "audio": "[Nota de voz]",
    "video": "[Video]",
    "sticker": "[Sticker]",
    "location": "[Ubicación]",
    "other": "[Mensaje no compatible]",
}


@dataclass(frozen=True)
class Candidate:
    key: str  # "lease:<id>" or "owner:<user id>"
    role: ContactType
    org: Organization
    lease: Lease | None = None
    tenant: Tenant | None = None
    unit: Unit | None = None
    building: Building | None = None
    user: User | None = None


@dataclass(frozen=True)
class InactiveLease:
    """A button of a lease that has ended (still that tenant's)."""

    lease: Lease


# -- entry points ------------------------------------------------------------------------------


def handle_webhook_payload(db: Session, payload: Any) -> ParsedWebhook:
    """Webhook body (already authenticated) → status updates now, messages to the queue.

    The simulator calls this same function. The caller commits.
    """
    phone_number_id = get_settings().WHATSAPP_PHONE_NUMBER_ID or None
    parsed = parse_webhook(payload, phone_number_id)
    for status in parsed.statuses:
        messaging.apply_status(db, status)
    for event in parsed.messages:
        schedule(db, "process_inbound", event.model_dump(mode="json"))
    return parsed


@job("process_inbound")
def process_inbound_job(db: Session, event: dict[str, Any]) -> None:
    process_inbound(db, InboundEvent.model_validate(event))


def process_inbound(db: Session, event: InboundEvent) -> str:
    """Route one inbound message. Returns how it was routed (for logs and tests)."""
    record = _claim(db, event)
    if record is None:
        return "duplicate"
    conversation = _conversation(db, event.phone)
    if conversation.last_inbound_at is None or event.timestamp > conversation.last_inbound_at:
        conversation.last_inbound_at = event.timestamp
    if event.type == "reaction":
        record.routing = "ignored"
        return "ignored"

    now = utc_now()
    candidates = find_candidates(db, event.phone)

    picked = _picked(event, conversation, candidates)
    if picked is not None:
        _bind(conversation, picked, now)
        pending = list(conversation.pending_event_ids)
        conversation.pending_event_ids = []
        conversation.choice_options = []
        _route(db, record, picked, event, conversation, is_pick=True)
        for pending_id in pending:
            pending_record = db.get(WhatsAppInboundEvent, uuid.UUID(pending_id))
            if pending_record is not None:
                pending_event = InboundEvent.model_validate(pending_record.event)
                _route(
                    db,
                    pending_record,
                    picked,
                    pending_event,
                    conversation,
                    keyword=_keyword(pending_event),
                )
        return picked.role.value

    keyword = _keyword(event)
    target = _from_context(db, event, candidates)
    if target is None:
        found = _from_button(db, event, candidates)
        if isinstance(found, InactiveLease):
            _lease_inactive(db, record, found.lease, event)
            return "inactive_lease"
        target = found

    tenants = [c for c in candidates if c.role == ContactType.TENANT]
    if target is None and keyword == Keyword.BAJA and tenants:
        _opt_out(db, record, tenants, event)
        return ContactType.TENANT.value
    if target is None and keyword == Keyword.ALTA:
        opted_out = [c for c in tenants if c.lease is not None and is_opted_out(db, c.lease)]
        if opted_out:
            _opt_in(db, record, opted_out, event)
            return ContactType.TENANT.value

    if target is None:
        target = _from_binding(conversation, candidates, now)
    if target is None:
        if not candidates:
            _unknown(db, record, event, conversation, now)
            return "unknown"
        all_opted_out = len(tenants) == len(candidates) and all(
            c.lease is not None and is_opted_out(db, c.lease) for c in tenants
        )
        if len(candidates) == 1 or all_opted_out:
            target = candidates[0]
        else:
            _ask_which(db, record, event, conversation, candidates)
            return "awaiting_choice"
    elif conversation.bound_key == target.key:
        conversation.bound_until = now + CHOICE_TTL
    _route(db, record, target, event, conversation, keyword=keyword)
    return target.role.value


# -- claim, conversation, candidates -----------------------------------------------------------


def _claim(db: Session, event: InboundEvent) -> WhatsAppInboundEvent | None:
    new_id = db.execute(
        pg_insert(WhatsAppInboundEvent)
        .values(
            id=uuid.uuid4(),
            provider_message_id=event.provider_message_id,
            phone=event.phone,
            received_at=event.timestamp,
            event=event.model_dump(mode="json"),
        )
        .on_conflict_do_nothing(index_elements=["provider_message_id"])
        .returning(WhatsAppInboundEvent.id)
    ).scalar()
    if new_id is None:
        logger.info("WhatsApp message %s already processed", event.provider_message_id)
        return None
    return db.get(WhatsAppInboundEvent, new_id)


def _conversation(db: Session, phone: str) -> WhatsAppConversation:
    db.execute(
        pg_insert(WhatsAppConversation)
        .values(phone=phone)
        .on_conflict_do_nothing(index_elements=["phone"])
    )
    conversation = db.scalar(
        select(WhatsAppConversation)
        .where(WhatsAppConversation.phone == phone)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert conversation is not None
    return conversation


def find_candidates(db: Session, phone: str) -> list[Candidate]:
    rows = db.execute(
        select(Lease, Tenant, Organization, Unit, Building)
        .join(
            Tenant,
            and_(Tenant.id == Lease.tenant_id, Tenant.organization_id == Lease.organization_id),
        )
        .join(Organization, Organization.id == Lease.organization_id)
        .join(Unit, and_(Unit.id == Lease.unit_id, Unit.organization_id == Lease.organization_id))
        .outerjoin(
            Building,
            and_(Building.id == Unit.building_id, Building.organization_id == Unit.organization_id),
        )
        .where(Tenant.phone == phone, Lease.is_active)
        .order_by(Organization.name, Unit.code, Lease.id)
    ).all()
    candidates = [
        Candidate(
            key=f"lease:{lease.id}",
            role=ContactType.TENANT,
            org=org,
            lease=lease,
            tenant=tenant,
            unit=unit,
            building=building,
        )
        for lease, tenant, org, unit, building in rows
    ]
    for user, org in db.execute(
        select(User, Organization)
        .join(Organization, Organization.id == User.organization_id)
        .where(User.whatsapp_phone == phone)
    ).all():
        candidates.append(
            Candidate(key=f"owner:{user.id}", role=ContactType.OWNER, org=org, user=user)
        )
    return candidates


def _keyword(event: InboundEvent) -> Keyword | None:
    return detect_keyword(event.text) if event.type == "text" else None


def _by_key(candidates: list[Candidate], key: str | None) -> Candidate | None:
    return next((c for c in candidates if c.key == key), None)


def _bind(conversation: WhatsAppConversation, target: Candidate, now: dt.datetime) -> None:
    conversation.bound_key = target.key
    conversation.bound_until = now + CHOICE_TTL


def _picked(
    event: InboundEvent, conversation: WhatsAppConversation, candidates: list[Candidate]
) -> Candidate | None:
    """The answer to "which contract?": a list row, or its number while a question is open."""
    parsed = parse_payload(event.payload)
    if parsed is not None and parsed[0] == "pick":
        return _by_key(candidates, parsed[1])
    text = (event.text or "").strip()
    if event.type == "text" and conversation.pending_event_ids and text.isdigit():
        index = int(text) - 1
        options = list(conversation.choice_options)
        if 0 <= index < len(options):
            return _by_key(candidates, options[index])
    return None


def _from_context(
    db: Session, event: InboundEvent, candidates: list[Candidate]
) -> Candidate | None:
    if not event.context_id:
        return None
    replied = db.scalar(
        select(Message).where(
            Message.provider_message_id == event.context_id,
            Message.direction == MessageDirection.OUTBOUND,
        )
    )
    if replied is None:
        return None
    if replied.lease_id is not None:
        return _by_key(candidates, f"lease:{replied.lease_id}")
    if replied.user_id is not None:
        return _by_key(candidates, f"owner:{replied.user_id}")
    return None


def _from_button(
    db: Session, event: InboundEvent, candidates: list[Candidate]
) -> Candidate | InactiveLease | None:
    parsed = parse_payload(event.payload)
    if parsed is None or parsed[0] == "pick" or not parsed[1]:
        return None
    found = _by_key(candidates, f"lease:{parsed[1]}")
    if found is not None:
        return found
    try:
        lease = db.get(Lease, uuid.UUID(parsed[1]))
    except ValueError:
        return None
    if lease is None or lease.is_active:
        return None
    tenant = db.get(Tenant, lease.tenant_id)
    # Someone else's lease id in a payload is ignored, never trusted.
    return InactiveLease(lease) if tenant is not None and tenant.phone == event.phone else None


def _from_binding(
    conversation: WhatsAppConversation, candidates: list[Candidate], now: dt.datetime
) -> Candidate | None:
    if conversation.bound_key and conversation.bound_until and conversation.bound_until > now:
        return _by_key(candidates, conversation.bound_key)
    return None


# -- storing -----------------------------------------------------------------------------------


def _body(event: InboundEvent) -> str:
    if event.type in {"text", "button", "interactive"}:
        return event.text or ""
    if event.caption:
        return event.caption
    if event.type == "document" and event.media_filename:
        return f"[Documento] {event.media_filename}"
    return PLACEHOLDERS.get(event.type, PLACEHOLDERS["other"])


def _store_inbound(
    db: Session, record: WhatsAppInboundEvent, target: Candidate, event: InboundEvent
) -> Message:
    message = Message(
        organization_id=target.org.id,
        lease_id=target.lease.id if target.lease else None,
        tenant_id=target.tenant.id if target.tenant else None,
        user_id=target.user.id if target.user else None,
        channel=MessageChannel.WHATSAPP,
        direction=MessageDirection.INBOUND,
        contact_type=target.role,
        contact_phone=event.phone,
        content_type=CONTENT_TYPES.get(event.type, MessageContentType.OTHER),
        body=_body(event),
        payload={
            "type": event.type,
            "payload": event.payload,
            "declared_mime": event.media_mime,
            "filename": event.media_filename,
        },
        context_provider_message_id=event.context_id,
        status=MessageStatus.RECEIVED,
        provider_message_id=event.provider_message_id,
        created_at=event.timestamp,
        updated_at=event.timestamp,
    )
    db.add(message)
    db.flush()
    record.routing = target.role.value
    record.organization_id = target.org.id
    record.message_id = message.id
    record_event(
        db,
        target.org.id,
        "message.received",
        lease=target.lease,
        actor=ActorType.TENANT if target.role == ContactType.TENANT else ActorType.OWNER,
        message_id=message.id,
        content_type=message.content_type.value,
        contact_type=target.role.value,
    )
    return message


# -- routing to a tenant or the owner ----------------------------------------------------------


def _route(
    db: Session,
    record: WhatsAppInboundEvent,
    target: Candidate,
    event: InboundEvent,
    conversation: WhatsAppConversation,
    *,
    keyword: Keyword | None = None,
    is_pick: bool = False,
) -> None:
    if target.role == ContactType.OWNER:
        _handle_owner(db, record, target, event, is_pick)
    else:
        _handle_tenant(db, record, target, event, conversation, keyword, is_pick)


def _handle_owner(
    db: Session, record: WhatsAppInboundEvent, target: Candidate, event: InboundEvent, is_pick: bool
) -> None:
    message = _store_inbound(db, record, target, event)
    if is_pick or owner_assistant.handle(db, message):
        return
    assert target.user is not None
    to = recipient_for_owner(db, target.user)
    text = catalog().message("owner_placeholder")
    _reply(
        db,
        to,
        "owner_placeholder",
        TextSend(
            text.render("tu", {"nombre": to.first_name, "panel_url": get_settings().PANEL_URL})
        ),
    )


def _handle_tenant(
    db: Session,
    record: WhatsAppInboundEvent,
    target: Candidate,
    event: InboundEvent,
    conversation: WhatsAppConversation,
    keyword: Keyword | None,
    is_pick: bool,
) -> None:
    assert target.lease is not None
    if keyword == Keyword.BAJA:
        _opt_out(db, record, [target], event)
        return
    if keyword == Keyword.ALTA:
        _opt_in(db, record, [target], event)
        return
    message = _store_inbound(db, record, target, event)
    if is_pick:
        return
    to = recipient_for_lease(db, target.lease)
    if is_opted_out(db, target.lease):
        _opt_out_notice(db, to, conversation)
        return

    parsed = parse_payload(event.payload) if event.type in {"button", "interactive"} else None
    action = parsed[0] if parsed else None
    if keyword == Keyword.MENU or action == "menu":
        _reply(db, to, "menu", menu_content(to))
    elif action in {"paid", "send_receipt"}:
        _reply_text(db, to, "send_receipt")
    elif action == "balance":
        _reply_balance(db, to)
    elif action == "pay_info":
        _reply(db, to, "pay_info", pay_info_content(db, to))
    elif action == "pay_later":
        _reply_text(db, to, "pay_later", {"propietario": to.org.name})
    elif action == "other":
        _reply_text(db, to, "other", {"propietario": to.org.name})
    elif event.type in {"image", "document"}:
        _receive_receipt(db, to, message, event)
    elif event.type in UNPLAYABLE_TYPES:
        _reply_text(db, to, "unsupported_media")
        _owner_task(db, to, tenant_unplayable_task(to.tenant_name, event.type))
    elif event.type == "text":
        _owner_task(db, to, tenant_message_task(to.tenant_name, event.text or ""))
        _ack_free_text(db, to)
    else:  # a button we don't know (an old template, for example)
        _reply(db, to, "menu", menu_content(to))


# -- replies -----------------------------------------------------------------------------------


def _reply(
    db: Session,
    to: Recipient,
    text_key: str,
    content: SessionContent,
    kind: MessageKind = MessageKind.REPLY,
) -> Message | None:
    try:
        return messaging.send(db, to, kind, session=content, text_key=text_key)
    except SendBlockedError as blocked:
        logger.info("WhatsApp reply %s blocked: %s", text_key, blocked)
        return None


def _reply_text(
    db: Session, to: Recipient, key: str, params: dict[str, str] | None = None
) -> Message | None:
    return _reply(db, to, key, TextSend(catalog().message(key).render(to.tone, params)))


def menu_content(to: Recipient) -> ListSend:
    definition = catalog().message("menu")
    lease_id = to.lease.id if to.lease else None
    return ListSend(
        body=definition.render(to.tone, {"nombre": to.first_name}),
        button=definition.list_button or "",
        rows=tuple(
            ListRowOut(button_payload(row.action, lease_id), row.title, row.description)
            for row in definition.rows
        ),
    )


def next_rent_due(lease: Lease, today: dt.date) -> dt.date:
    this_month = due_date(period_of(today), lease.due_day)
    if this_month > today:
        return this_month
    return due_date(add_months(period_of(today), 1), lease.due_day)


def _reply_balance(db: Session, to: Recipient) -> None:
    assert to.lease is not None
    today = local_today(to.org.timezone)
    state = load_lease_state(db, to.lease, today, to.org.timezone)
    if state.status.amount_due > 0:
        _reply_text(
            db,
            to,
            "balance_due",
            {"propietario": to.org.name, "monto": format_dop(state.status.amount_due)},
        )
        return
    _reply_text(
        db,
        to,
        "balance_ok",
        {
            "propietario": to.org.name,
            "monto": format_dop(to.lease.rent_amount),
            "fecha": format_long_date(next_rent_due(to.lease, today)),
        },
    )


def pay_info_content(db: Session, to: Recipient) -> TextSend:
    cat = catalog()
    accounts = db.scalars(
        select(BankAccount)
        .where(BankAccount.organization_id == to.org.id, BankAccount.is_active)
        .order_by(BankAccount.created_at, BankAccount.id)
    ).all()
    if not accounts:
        return TextSend(cat.message("pay_info_none").render(to.tone, {"propietario": to.org.name}))
    labels = {
        AccountType.SAVINGS: cat.label("account_savings"),
        AccountType.CHECKING: cat.label("account_checking"),
    }
    blocks = [
        cat.message("pay_info_account").render(
            to.tone,
            {
                "banco": account.bank_name,
                "tipo": labels[account.account_type],
                "numero": account.account_number,
                "titular": account.holder_name,
            },
        )
        for account in accounts
    ]
    parts = [
        cat.message("pay_info_intro").render(to.tone, {"propietario": to.org.name}),
        *blocks,
        cat.message("pay_info_outro").render(to.tone),
    ]
    return TextSend("\n\n".join(parts))


def _ack_free_text(db: Session, to: Recipient) -> None:
    assert to.lease is not None
    since = utc_now() - FREE_TEXT_ACK_EVERY
    recent = db.scalar(
        select(Message.id)
        .where(
            Message.organization_id == to.org.id,
            Message.lease_id == to.lease.id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.payload["text_key"].astext == "free_text_ack",
            Message.created_at >= since,
        )
        .limit(1)
    )
    if recent is None:
        _reply_text(db, to, "free_text_ack", {"propietario": to.org.name})


def _opt_out_notice(db: Session, to: Recipient, conversation: WhatsAppConversation) -> None:
    now = utc_now()
    last = conversation.last_opt_out_notice_at
    if last is not None and last > now - NOTICE_EVERY:
        return
    conversation.last_opt_out_notice_at = now
    _reply(
        db,
        to,
        "opt_out_notice",
        TextSend(catalog().message("opt_out_notice").render(to.tone)),
        MessageKind.OPT_OUT_NOTICE,
    )


# -- receipts ----------------------------------------------------------------------------------


def _receive_receipt(db: Session, to: Recipient, message: Message, event: InboundEvent) -> None:
    check = MediaCheck(None, RejectReason.EMPTY)
    content = b""
    if event.media_id:
        try:
            media = get_whatsapp_channel().fetch_media(event.media_id)
            content = media.content
            check = check_receipt_file(content, event.media_mime)
        except MediaTooLargeError:
            check = MediaCheck(None, RejectReason.TOO_LARGE)
        # Any other WhatsAppError propagates: the job fails and RQ retries it.
    if not check.accepted:
        assert check.reason is not None
        message.payload = {
            **message.payload,
            "media_rejected": check.reason.value,
            "detected_mime": check.detected,
        }
        db.flush()
        record_event(
            db,
            to.org.id,
            "message.media_rejected",
            lease=to.lease,
            actor=ActorType.SYSTEM,
            message_id=message.id,
            reason=check.reason.value,
        )
        _reply_text(db, to, "media_rejected")
        return
    assert check.detected is not None
    key = (
        f"{organization_prefix(to.org.id)}/messages/{message.id}/"
        f"{uuid.uuid4().hex}.{EXTENSIONS[check.detected]}"
    )
    get_storage().save(key, content, check.detected)
    message.media_key = key
    message.media_mime = check.detected
    db.flush()
    try:
        messaging.send(
            db,
            to,
            MessageKind.REPLY,
            template="comprobante_recibido",
            params={"nombre": to.first_name, "propietario": to.org.name},
            text_key="comprobante_recibido",
        )
    except SendBlockedError as blocked:
        logger.info("Receipt confirmation blocked: %s", blocked)
    _owner_task(db, to, tenant_receipt_task(to.tenant_name))


# -- owner's pending items ---------------------------------------------------------------------


def _owner_task(db: Session, to: Recipient, task: OwnerTask) -> InboxItem:
    """One open "the tenant wrote" item per lease, updated with the latest message."""
    assert to.lease is not None
    item = db.scalar(
        select(InboxItem).where(
            InboxItem.organization_id == to.org.id,
            InboxItem.lease_id == to.lease.id,
            InboxItem.type == InboxItemType(task.inbox_type),
            InboxItem.status == InboxItemStatus.OPEN,
        )
    )
    if item is not None:
        item.summary = task.summary
        item.updated_at = utc_now()
        db.flush()
        return item
    item = InboxItem(
        organization_id=to.org.id,
        lease_id=to.lease.id,
        type=InboxItemType(task.inbox_type),
        summary=task.summary,
        priority=task.priority,
    )
    db.add(item)
    db.flush()
    record_event(
        db,
        to.org.id,
        "inbox.created",
        lease=to.lease,
        actor=ActorType.SYSTEM,
        inbox_item_id=item.id,
        type=item.type.value,
    )
    return item


# -- BAJA / ALTA -------------------------------------------------------------------------------


def is_opted_out(db: Session, lease: Lease) -> bool:
    """The tenant replied BAJA for this lease and hasn't replied ALTA since."""
    consent = latest_consent(db, lease.id)
    return consent is not None and consent.revoked_at is not None


def _group_by_org(targets: list[Candidate]) -> list[list[Candidate]]:
    groups: dict[uuid.UUID, list[Candidate]] = {}
    for target in targets:
        groups.setdefault(target.org.id, []).append(target)
    return list(groups.values())


def _opt_out(
    db: Session, record: WhatsAppInboundEvent, targets: list[Candidate], event: InboundEvent
) -> None:
    """BAJA: stop at once, tell each owner, confirm to the tenant (once per organization)."""
    _store_inbound(db, record, targets[0], event)
    now = utc_now()
    for target in targets:
        assert target.lease is not None and target.tenant is not None
        current = db.scalar(
            select(ConsentRecord).where(
                ConsentRecord.organization_id == target.org.id,
                ConsentRecord.lease_id == target.lease.id,
                ConsentRecord.revoked_at.is_(None),
            )
        )
        if current is None:
            continue  # already off: only the confirmation is repeated
        current.revoked_at = now
        db.flush()
        record_event(
            db,
            target.org.id,
            "consent.revoked",
            lease=target.lease,
            actor=ActorType.TENANT,
            method="whatsapp_baja",
            provider_message_id=event.provider_message_id,
        )
        to = recipient_for_lease(db, target.lease)
        _owner_task(db, to, consent_revoked_task(target.tenant.full_name))
        messaging.cancel_queued(db, target.lease, "consent_revoked")
    for group in _group_by_org(targets):
        assert group[0].lease is not None
        to = recipient_for_lease(db, group[0].lease)
        try:
            messaging.send(
                db,
                to,
                MessageKind.OPT_OUT_CONFIRMATION,
                template="baja_confirmada",
                params={"nombre": to.first_name, "propietario": to.org.name},
                text_key="baja_confirmada",
            )
        except SendBlockedError as blocked:
            logger.warning("BAJA confirmation blocked: %s", blocked)


def _opt_in(
    db: Session, record: WhatsAppInboundEvent, targets: list[Candidate], event: InboundEvent
) -> None:
    """ALTA: consent again (method whatsapp_reply), close the BAJA item, confirm."""
    _store_inbound(db, record, targets[0], event)
    now = utc_now()
    for target in targets:
        assert target.lease is not None and target.tenant is not None
        if not is_opted_out(db, target.lease):
            continue
        db.add(
            ConsentRecord(
                organization_id=target.org.id,
                lease_id=target.lease.id,
                tenant_id=target.tenant.id,
                method=ConsentMethod.WHATSAPP_REPLY,
                evidence=f"Respuesta ALTA por WhatsApp, mensaje {event.provider_message_id}",
                granted_at=now,
            )
        )
        db.flush()
        record_event(
            db,
            target.org.id,
            "consent.granted",
            lease=target.lease,
            actor=ActorType.TENANT,
            method=ConsentMethod.WHATSAPP_REPLY.value,
            provider_message_id=event.provider_message_id,
        )
        for item in db.scalars(
            select(InboxItem).where(
                InboxItem.organization_id == target.org.id,
                InboxItem.lease_id == target.lease.id,
                InboxItem.type == InboxItemType.CONSENT_REVOKED,
                InboxItem.status == InboxItemStatus.OPEN,
            )
        ).all():
            item.status = InboxItemStatus.RESOLVED
            item.resolved_at = now
            db.flush()
            record_event(
                db,
                target.org.id,
                "inbox.resolved",
                lease=target.lease,
                actor=ActorType.TENANT,
                inbox_item_id=item.id,
                status=item.status.value,
                reason="alta",
            )
    for group in _group_by_org(targets):
        assert group[0].lease is not None
        to = recipient_for_lease(db, group[0].lease)
        _reply_text(db, to, "alta_confirmed", {"nombre": to.first_name, "propietario": to.org.name})


# -- platform answers (no organization) --------------------------------------------------------


def _send_platform(phone: str, content: SessionContent) -> None:
    """Messages that belong to no organization: unknown number, "which contract?"."""
    try:
        get_whatsapp_channel().send(phone, content)
    except WhatsAppError as exc:
        logger.warning("WhatsApp platform message to %s failed: %s", phone, exc)


def _unknown(
    db: Session,
    record: WhatsAppInboundEvent,
    event: InboundEvent,
    conversation: WhatsAppConversation,
    now: dt.datetime,
) -> None:
    record.routing = "unknown"
    last = conversation.last_unknown_reply_at
    if last is not None and last > now - NOTICE_EVERY:
        return
    conversation.last_unknown_reply_at = now
    text = (
        catalog()
        .message("unknown_number")
        .render("tu", {"plataforma": get_settings().PLATFORM_NAME})
    )
    _send_platform(event.phone, TextSend(text))


def _choice_row(candidate: Candidate) -> ListRowOut:
    cat = catalog()
    if candidate.role == ContactType.OWNER:
        title, description = cat.label("owner_row"), candidate.org.name
    else:
        assert candidate.unit is not None
        title = apartment_label(candidate.unit, None)
        description = candidate.org.name
        if candidate.building is not None:
            description = f"{description} · {candidate.building.name}"
    return ListRowOut(
        f"v1:pick:{candidate.key}",
        title[:24],
        description[:72],
    )


def _ask_which(
    db: Session,
    record: WhatsAppInboundEvent,
    event: InboundEvent,
    conversation: WhatsAppConversation,
    candidates: list[Candidate],
) -> None:
    record.routing = "awaiting_choice"
    offered = candidates[:MAX_CHOICES]
    conversation.choice_options = [c.key for c in offered]
    conversation.pending_event_ids = [*conversation.pending_event_ids, str(record.id)]
    definition = catalog().message("choose_lease")
    _send_platform(
        event.phone,
        ListSend(
            body=definition.render("tu"),
            button=definition.list_button or "",
            rows=tuple(_choice_row(c) for c in offered),
        ),
    )


def _lease_inactive(
    db: Session, record: WhatsAppInboundEvent, lease: Lease, event: InboundEvent
) -> None:
    to = recipient_for_lease(db, lease)
    target = Candidate(
        key=f"lease:{lease.id}",
        role=ContactType.TENANT,
        org=to.org,
        lease=lease,
        tenant=to.tenant,
    )
    _store_inbound(db, record, target, event)
    _reply_text(db, to, "lease_inactive")
