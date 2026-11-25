"""Monthly outbound WhatsApp usage per organization, template vs session (pricing)."""

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.domain.messaging_rules import MessageKind
from app.models import Message, Organization
from app.models.enums import MessageChannel, MessageContentType, MessageDirection, MessageStatus
from app.services.usage import UsageRow, all_organizations_month, monthly_outbound_counts
from tests.factories import add, make_org

SD = ZoneInfo("America/Santo_Domingo")


def outbound(
    db: Session,
    org: Organization,
    *,
    content_type: MessageContentType,
    status: MessageStatus = MessageStatus.DELIVERED,
    kind: MessageKind = MessageKind.REPLY,
    sent_at: dt.datetime | None = dt.datetime(2026, 11, 10, 12, 0, tzinfo=SD),
) -> None:
    add(
        db,
        Message(
            organization_id=org.id,
            channel=MessageChannel.WHATSAPP,
            direction=MessageDirection.OUTBOUND,
            kind=kind,
            content_type=content_type,
            body="x",
            status=status,
            sent_at=sent_at,
        ),
    )


def test_counts_by_month_delivery_and_kind_for_one_organization(db_session: Session) -> None:
    org, other = make_org(db_session), make_org(db_session)
    template, session = MessageContentType.TEMPLATE, MessageContentType.INTERACTIVE
    outbound(db_session, org, content_type=template, kind=MessageKind.COLLECTION_REMINDER)
    outbound(
        db_session,
        org,
        content_type=template,
        kind=MessageKind.COLLECTION_REMINDER,
        status=MessageStatus.READ,
    )
    outbound(db_session, org, content_type=session)
    outbound(db_session, org, content_type=MessageContentType.TEXT, status=MessageStatus.SENT)
    # December in UTC, but November 30 at 23:00 in Santo Domingo: counts in November.
    outbound(
        db_session, org, content_type=session, sent_at=dt.datetime(2026, 12, 1, 3, 0, tzinfo=dt.UTC)
    )
    # Not counted: never went out, or another organization.
    outbound(db_session, org, content_type=session, status=MessageStatus.FAILED, sent_at=None)
    outbound(db_session, org, content_type=session, status=MessageStatus.QUEUED, sent_at=None)
    outbound(db_session, other, content_type=template)

    rows = monthly_outbound_counts(db_session, org.id, "2026-11")
    assert rows == [
        UsageRow(org.id, "2026-11", "session", "reply", 3),
        UsageRow(org.id, "2026-11", "template", "collection_reminder", 2),
    ]
    assert monthly_outbound_counts(db_session, org.id, "2026-12") == []
    report = all_organizations_month(db_session, "2026-11")
    assert {row.organization_id for _, row in report} == {org.id, other.id}
