"""'Enviar recordatorio ahora': the owner asks, the send validator decides (rule 4)."""

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.channels.whatsapp import get_whatsapp_channel
from app.core.timezone import business_zone, local_today, utc_now
from app.domain.formatting import format_day_month, format_dop
from app.domain.holidays import load_holidays
from app.domain.messaging_rules import (
    BlockReason,
    Consent,
    MessageKind,
    SendRequest,
    next_contact_time,
    validate_send,
)
from app.domain.status import TenantState
from app.models import (
    AssistantProfile,
    ConsentRecord,
    Lease,
    Message,
    Organization,
    Tenant,
)
from app.models.enums import AssistantTone, MessageChannel, MessageDirection, MessageStatus
from app.services.lease_state import contact_rules, load_lease_state
from app.services.timeline import record_event

MANUAL_REMINDER_TEMPLATE = "reminder_manual"
REMINDER_TEMPLATE_PREFIX = "reminder_"
WEEK = dt.timedelta(days=7)

REASON_TEXT = {
    BlockReason.NO_CONSENT: "no hay consentimiento vigente para escribirle",
    BlockReason.CONSENT_REVOKED: "el inquilino respondió BAJA",
    BlockReason.SUNDAY: "hoy es domingo",
    BlockReason.HOLIDAY: "hoy es feriado",
    BlockReason.DAY_OFF: "hoy no es día de contacto en tus reglas",
    BlockReason.OUTSIDE_HOURS: "estamos fuera del horario de contacto",
    BlockReason.DAILY_LIMIT: "ya recibió el máximo de recordatorios de hoy",
    BlockReason.WEEKLY_LIMIT: "ya recibió el máximo de recordatorios de esta semana",
    BlockReason.ACTIVE_PROMISE: "tiene una promesa de pago vigente",
}


class NothingToRemindError(Exception):
    pass


class ReminderBlockedError(Exception):
    def __init__(
        self, reasons: tuple[BlockReason, ...], next_contact_time: dt.datetime | None
    ) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons
        self.next_contact_time = next_contact_time

    def message(self) -> str:
        reasons = ", ".join(REASON_TEXT[r] for r in self.reasons)
        return f"No podemos enviar el recordatorio ahora: {reasons}."


def _body(
    tenant: Tenant, org: Organization, assistant: AssistantProfile | None, state, today: dt.date
) -> str:
    first = tenant.full_name.split()[0] if tenant.full_name.split() else tenant.full_name
    name = assistant.display_name if assistant else "el asistente"
    formal = assistant is not None and assistant.tone == AssistantTone.FORMAL
    greeting = f"Saludos, {first}." if formal else f"Hola, {first}."
    intro = f"Le escribe {name}, de {org.name}." if formal else f"Te escribe {name}, de {org.name}."
    amount = format_dop(state.status.amount_due)
    if state.status.state == TenantState.OVERDUE:
        detail = (
            f"Le recordamos que tiene un balance pendiente de {amount}."
            if formal
            else f"Te recordamos que tienes un balance pendiente de {amount}."
        )
    else:
        due = state.next_due_on() or today
        detail = (
            f"Le recordamos que su renta de {amount} vence el {format_day_month(due)}."
            if formal
            else f"Te recordamos que tu renta de {amount} vence el {format_day_month(due)}."
        )
    return f"{greeting} {intro} {detail} Si ya pagaste, envíanos el comprobante por aquí."


def send_reminder_now(db: Session, lease: Lease, org: Organization, user_id: uuid.UUID) -> Message:
    now = utc_now()
    today = local_today(org.timezone)
    state = load_lease_state(db, lease, today, org.timezone)
    if state.status.amount_due <= 0:
        raise NothingToRemindError

    consent_row = db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.lease_id == lease.id)
        .order_by(ConsentRecord.revoked_at.is_(None).desc(), ConsentRecord.granted_at.desc())
        .limit(1)
    )
    consent = (
        Consent(granted_at=consent_row.granted_at, revoked_at=consent_row.revoked_at)
        if consent_row
        else None
    )
    reminders_sent = db.scalars(
        select(Message.created_at).where(
            Message.lease_id == lease.id,
            Message.direction == MessageDirection.OUTBOUND,
            Message.template.like(f"{REMINDER_TEMPLATE_PREFIX}%"),
            Message.status != MessageStatus.FAILED,
            Message.created_at >= now - WEEK,
        )
    ).all()
    rules = contact_rules(state.rules)
    holidays = load_holidays()
    zone = business_zone(org.timezone)
    decision = validate_send(
        SendRequest(
            kind=MessageKind.COLLECTION_REMINDER,
            at=now,
            consent=consent,
            reminders_sent=list(reminders_sent),
            active_promise_date=state.active_promise.promised_date
            if state.active_promise
            else None,
        ),
        rules,
        holidays,
        zone,
    )
    if not decision.allowed:
        next_time = None
        if not ({BlockReason.NO_CONSENT, BlockReason.CONSENT_REVOKED} & set(decision.reasons)):
            try:
                next_time = next_contact_time(now, rules, holidays, zone)
            except ValueError:
                next_time = None
        raise ReminderBlockedError(decision.reasons, next_time)

    tenant = db.get(Tenant, lease.tenant_id)
    assert tenant is not None
    assistant = db.scalar(
        select(AssistantProfile).where(AssistantProfile.organization_id == org.id)
    )
    message = Message(
        organization_id=org.id,
        lease_id=lease.id,
        tenant_id=tenant.id,
        channel=MessageChannel.WHATSAPP,
        direction=MessageDirection.OUTBOUND,
        template=MANUAL_REMINDER_TEMPLATE,
        body=_body(tenant, org, assistant, state, today),
        status=MessageStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    db.add(message)
    db.flush()
    record_event(
        db,
        org.id,
        "message.queued",
        lease=lease,
        user_id=user_id,
        message_id=message.id,
        template=MANUAL_REMINDER_TEMPLATE,
    )
    message.provider_message_id = get_whatsapp_channel().send(
        to=tenant.phone, body=message.body, template=message.template
    )
    message.status = MessageStatus.SENT
    db.flush()
    record_event(
        db,
        org.id,
        "message.sent",
        lease=lease,
        user_id=user_id,
        message_id=message.id,
        template=MANUAL_REMINDER_TEMPLATE,
    )
    return message
