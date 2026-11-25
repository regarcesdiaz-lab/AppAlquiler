"""Outbound WhatsApp usage per month, for pricing the plans.

Since October 2026 Meta charges session messages inside the 24-hour window too, not only
templates. The ``monthly_outbound_message_counts`` view (migration 0004) counts messages that
actually went out (sent, delivered or read) by organization, local month, delivery (template or
session) and kind. Internal: not exposed by the owner API.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class UsageRow:
    organization_id: uuid.UUID
    month: str  # "2026-11"
    delivery: str  # "template" | "session"
    kind: str | None
    messages: int


def monthly_outbound_counts(db: Session, organization_id: uuid.UUID, month: str) -> list[UsageRow]:
    """One organization's outbound messages in a month (always scoped to the organization)."""
    rows = db.execute(
        text(
            "SELECT organization_id, month, delivery, kind, messages"
            " FROM monthly_outbound_message_counts"
            " WHERE organization_id = :org AND month = :month"
            " ORDER BY delivery, kind"
        ),
        {"org": organization_id, "month": month},
    ).all()
    return [UsageRow(*row) for row in rows]


def all_organizations_month(db: Session, month: str) -> list[tuple[str, UsageRow]]:
    """Platform report (script only): every organization's usage in a month, with its name."""
    rows = db.execute(
        text(
            "SELECT o.name, v.organization_id, v.month, v.delivery, v.kind, v.messages"
            " FROM monthly_outbound_message_counts v"
            " JOIN organizations o ON o.id = v.organization_id"
            " WHERE v.month = :month"
            " ORDER BY o.name, v.delivery, v.kind"
        ),
        {"month": month},
    ).all()
    return [(row[0], UsageRow(*row[1:])) for row in rows]
