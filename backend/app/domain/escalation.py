"""When a case goes up to the owner, and the text of the pending item (pendiente) they see.

Agents work in the background and only bring the owner what needs their decision. A case is
escalated when:

* the oldest open charge reaches the rule set's ``escalation_days`` after its due date, with no
  active promise (``OVERDUE``);
* a promise date passes without the payment (``PROMISE_BROKEN``);
* the tenant asks for an extension outside the limits (``EXTENSION_OUT_OF_LIMIT``);
* the tenant replies BAJA (``CONSENT_REVOKED``): the platform stops writing to them at once,
  so the owner has to reach the tenant directly.
"""

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.domain.extensions import ExtensionDecision, ExtensionIssue
from app.domain.formatting import format_day_month, format_dop


class EscalationReason(StrEnum):
    OVERDUE = "overdue"
    PROMISE_BROKEN = "promise_broken"
    EXTENSION_OUT_OF_LIMIT = "extension_out_of_limit"
    CONSENT_REVOKED = "consent_revoked"
    # The tenant wrote, or sent a file, and no assistant can answer it yet.
    TENANT_MESSAGE = "tenant_message"


# Values of app.models.enums.InboxItemType (kept as text so the domain doesn't import models).
INBOX_TYPE = {
    EscalationReason.OVERDUE: "overdue_escalation",
    EscalationReason.PROMISE_BROKEN: "promise_broken",
    EscalationReason.EXTENSION_OUT_OF_LIMIT: "promise_to_approve",
    EscalationReason.CONSENT_REVOKED: "consent_revoked",
    EscalationReason.TENANT_MESSAGE: "tenant_message",
}

# 1 = most urgent (inbox_items.priority).
PRIORITY = {
    EscalationReason.OVERDUE: 1,
    EscalationReason.PROMISE_BROKEN: 1,
    EscalationReason.EXTENSION_OUT_OF_LIMIT: 2,
    EscalationReason.CONSENT_REVOKED: 2,
    EscalationReason.TENANT_MESSAGE: 3,
}

SNIPPET_MAX = 280
UNPLAYABLE = {
    "audio": "una nota de voz",
    "video": "un video",
    "sticker": "un sticker",
    "location": "una ubicación",
}


@dataclass(frozen=True)
class OwnerTask:
    reason: EscalationReason
    inbox_type: str
    priority: int
    summary: str


def _task(reason: EscalationReason, summary: str) -> OwnerTask:
    return OwnerTask(reason, INBOX_TYPE[reason], PRIORITY[reason], summary)


def _first_name(full_name: str) -> str:
    return full_name.split()[0] if full_name.split() else full_name


def overdue_needs_escalation(
    *,
    oldest_due_on: dt.date | None,
    today: dt.date,
    escalation_days: int,
    active_promise_date: dt.date | None = None,
) -> bool:
    if oldest_due_on is None:
        return False
    if active_promise_date is not None and active_promise_date >= today:
        return False
    return (today - oldest_due_on).days >= escalation_days


def overdue_task(tenant_name: str, amount_due: Decimal, days_late: int) -> OwnerTask:
    days = "1 día" if days_late == 1 else f"{days_late} días"
    return _task(
        EscalationReason.OVERDUE,
        f"{tenant_name} lleva {days} de atraso y debe {format_dop(amount_due)}. "
        "Ya recibió todos los avisos. Decide cómo seguir.",
    )


def promise_broken_task(tenant_name: str, promised_date: dt.date, amount_due: Decimal) -> OwnerTask:
    return _task(
        EscalationReason.PROMISE_BROKEN,
        f"{tenant_name} no pagó el {format_day_month(promised_date)} como prometió. "
        f"Debe {format_dop(amount_due)}.",
    )


def extension_task(
    tenant_name: str,
    requested_date: dt.date,
    decision: ExtensionDecision,
    *,
    max_days: int,
    max_per_year: int,
) -> OwnerTask:
    parts = [f"{tenant_name} pide pagar el {format_day_month(requested_date)}."]
    if ExtensionIssue.DATE_IN_PAST in decision.issues:
        parts.append("Esa fecha ya pasó.")
    if ExtensionIssue.TOO_MANY_DAYS in decision.issues:
        parts.append(f"Son {decision.days} días de prórroga y tu límite es {max_days}.")
    if ExtensionIssue.YEARLY_LIMIT_REACHED in decision.issues:
        parts.append(f"Ya usó {decision.used_this_year} de {max_per_year} prórrogas este año.")
    parts.append("¿La apruebas?")
    return _task(EscalationReason.EXTENSION_OUT_OF_LIMIT, " ".join(parts))


def tenant_message_task(tenant_name: str, text: str) -> OwnerTask:
    snippet = " ".join(text.split())
    if len(snippet) > SNIPPET_MAX:
        snippet = snippet[: SNIPPET_MAX - 1].rstrip() + "…"
    return _task(
        EscalationReason.TENANT_MESSAGE, f"{tenant_name} te escribió por WhatsApp: “{snippet}”"
    )


def tenant_receipt_task(tenant_name: str) -> OwnerTask:
    return _task(
        EscalationReason.TENANT_MESSAGE,
        f"{tenant_name} envió un comprobante por WhatsApp. Revísalo en su ficha.",
    )


def tenant_unplayable_task(tenant_name: str, media_type: str) -> OwnerTask:
    what = UNPLAYABLE.get(media_type, "un mensaje")
    return _task(
        EscalationReason.TENANT_MESSAGE,
        f"{tenant_name} envió {what} por WhatsApp que no podemos mostrar. "
        f"Comunícate con {_first_name(tenant_name)}.",
    )


def consent_revoked_task(tenant_name: str) -> OwnerTask:
    return _task(
        EscalationReason.CONSENT_REVOKED,
        f"{tenant_name} respondió BAJA y ya no recibirá mensajes de la plataforma. "
        f"Comunícate directamente con {_first_name(tenant_name)}.",
    )
