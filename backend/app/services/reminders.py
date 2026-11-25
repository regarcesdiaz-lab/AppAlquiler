"""'Enviar recordatorio ahora': the owner asks, the send validator decides (rule 4).

The text is an approved template (whatsapp_templates.toml): the friendly notice before the due
date, the due-day notice on the day, or the firm notice when the rent is overdue. Unlike the
automatic sequence, a manual reminder never waits for the next contact window: outside it, the
owner gets the reason and the next valid time.
"""

import datetime as dt
import uuid

from sqlalchemy.orm import Session

from app.core.timezone import local_today
from app.domain.formatting import format_days, format_dop, format_long_date
from app.domain.messaging_rules import BlockReason, MessageKind
from app.models import Lease, Message, Organization
from app.models.enums import ActorType
from app.services.lease_state import LeaseState, load_lease_state
from app.services.messaging import SendBlockedError, lease_params, recipient_for_lease, send

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
    BlockReason.PENDING_PAYMENT: "envió un comprobante que falta confirmar",
}

# rule_sets.reminder_sequence step → template (the sequence data keeps its own names).
SEQUENCE_TEMPLATES = {
    "reminder_upcoming": "aviso_amable",
    "reminder_due_today": "aviso_dia_pago",
    "reminder_overdue": "aviso_firme",
    "reminder_overdue_final": "aviso_firme",
}


class NothingToRemindError(Exception):
    pass


class ReminderBlockedError(Exception):
    def __init__(
        self,
        reasons: tuple[BlockReason, ...],
        next_contact_time: dt.datetime | None,
        tenant_name: str = "",
    ) -> None:
        super().__init__(", ".join(reasons))
        self.reasons = reasons
        self.next_contact_time = next_contact_time
        self.tenant_name = tenant_name

    def message(self) -> str:
        if BlockReason.PENDING_PAYMENT in self.reasons:
            # The one block the owner fixes right away: say what to do.
            return (
                f"{self.tenant_name} envió un comprobante. "
                "Confírmalo o recházalo antes de enviarle un recordatorio."
            )
        reasons = ", ".join(REASON_TEXT[r] for r in self.reasons)
        return f"No podemos enviar el recordatorio ahora: {reasons}."


def reminder_template(state: LeaseState, today: dt.date) -> tuple[str, dict[str, str]]:
    """Which notice fits the lease today, and its own variables."""
    amount = format_dop(state.status.amount_due)
    # Past due: the firm notice, never "vence el" with a date that already passed.
    if state.status.days_late > 0:
        return "aviso_firme", {"dias": format_days(state.status.days_late), "monto": amount}
    due = state.next_due_on() or today
    if due == today:
        return "aviso_dia_pago", {"monto": amount}
    return "aviso_amable", {"monto": amount, "fecha": format_long_date(due)}


def send_reminder_now(db: Session, lease: Lease, org: Organization, user_id: uuid.UUID) -> Message:
    today = local_today(org.timezone)
    state = load_lease_state(db, lease, today, org.timezone)
    if state.status.amount_due <= 0:
        raise NothingToRemindError
    to = recipient_for_lease(db, lease)
    if state.has_reported_payment:
        # A receipt waits for the owner's decision: reminding the tenant now would be unfair.
        raise ReminderBlockedError((BlockReason.PENDING_PAYMENT,), None, to.tenant_name)
    template, own_params = reminder_template(state, today)
    try:
        return send(
            db,
            to,
            MessageKind.COLLECTION_REMINDER,
            template=template,
            params=lease_params(db, to) | own_params,
            actor=ActorType.OWNER,
            user_id=user_id,
            wait_for_window=False,
        )
    except SendBlockedError as blocked:
        raise ReminderBlockedError(
            blocked.reasons, blocked.next_contact_time, to.tenant_name
        ) from None
