"""Timeline (evidence), maintenance issues, lease documents and the owner's inbox."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, Index, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import (
    AppendOnlyMixin,
    CreatedAtMixin,
    OrgScopedMixin,
    TimestampMixin,
    org_table_args,
    scoped_fk,
    str_enum,
)
from app.models.enums import ActorType, DocumentType, InboxItemStatus, InboxItemType, IssueStatus


class TimelineEvent(OrgScopedMixin, AppendOnlyMixin, CreatedAtMixin, Base):
    """Immutable record of everything that happened: messages, payments, promises, receipts,
    issues. It is the evidence trail and feeds the tenant's detail screen (rule 5)."""

    __tablename__ = "timeline_events"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("unit_id", "units"),
        Index("ix_timeline_events_lease_occurred", "lease_id", "occurred_at"),
        Index("ix_timeline_events_org_occurred", "organization_id", "occurred_at"),
    )

    lease_id: Mapped[uuid.UUID | None]
    unit_id: Mapped[uuid.UUID | None]
    # Dotted name, e.g. "payment.reported", "message.sent", "promise.created".
    event_type: Mapped[str] = mapped_column(String(60))
    actor_type: Mapped[ActorType] = mapped_column(str_enum(ActorType, "actor_type"))
    payload: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class Issue(OrgScopedMixin, TimestampMixin, Base):
    """A maintenance issue (avería) reported by the tenant."""

    __tablename__ = "issues"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        CheckConstraint(
            "(status = 'resolved') = (resolved_at IS NOT NULL)", name="resolved_at_when_resolved"
        ),
        Index(
            "ix_issues_org_open", "organization_id", postgresql_where=text("status <> 'resolved'")
        ),
        Index("ix_issues_lease_id", "lease_id"),
    )

    lease_id: Mapped[uuid.UUID]
    description: Mapped[str] = mapped_column(Text)
    photo_key: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[IssueStatus] = mapped_column(
        str_enum(IssueStatus, "issue_status"), default=IssueStatus.OPEN
    )
    resolved_at: Mapped[dt.datetime | None]


class Document(OrgScopedMixin, CreatedAtMixin, Base):
    """A file attached to a lease (signed contract, ID card, other)."""

    __tablename__ = "documents"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("uploaded_by_user_id", "users"),
        Index("ix_documents_lease_uploaded", "lease_id", "uploaded_at"),
    )

    lease_id: Mapped[uuid.UUID]
    type: Mapped[DocumentType] = mapped_column(str_enum(DocumentType, "document_type"))
    file_key: Mapped[str] = mapped_column(String(512))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    # NULL when the file arrived from the tenant over WhatsApp.
    uploaded_by_user_id: Mapped[uuid.UUID | None]
    uploaded_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class InboxItem(OrgScopedMixin, TimestampMixin, Base):
    """Something the owner has to decide. Points at the entity its action opens."""

    __tablename__ = "inbox_items"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("payment_id", "payments"),
        scoped_fk("payment_promise_id", "payment_promises"),
        scoped_fk("issue_id", "issues"),
        CheckConstraint(
            "num_nonnulls(payment_id, payment_promise_id, issue_id) <= 1",
            name="at_most_one_related_entity",
        ),
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        CheckConstraint(
            "(status = 'open') = (resolved_at IS NULL)", name="resolved_at_when_closed"
        ),
        # Home screen: open items, most urgent first (1 = most urgent).
        Index(
            "ix_inbox_items_org_open",
            "organization_id",
            "priority",
            "created_at",
            postgresql_where=text("status = 'open'"),
        ),
        # No duplicate open items for the same payment, promise or issue.
        Index(
            "uq_inbox_items_open_payment",
            "payment_id",
            unique=True,
            postgresql_where=text("status = 'open' AND payment_id IS NOT NULL"),
        ),
        Index(
            "uq_inbox_items_open_promise",
            "payment_promise_id",
            unique=True,
            postgresql_where=text("status = 'open' AND payment_promise_id IS NOT NULL"),
        ),
        Index(
            "uq_inbox_items_open_issue",
            "issue_id",
            unique=True,
            postgresql_where=text("status = 'open' AND issue_id IS NOT NULL"),
        ),
    )

    lease_id: Mapped[uuid.UUID | None]
    payment_id: Mapped[uuid.UUID | None]
    payment_promise_id: Mapped[uuid.UUID | None]
    issue_id: Mapped[uuid.UUID | None]
    type: Mapped[InboxItemType] = mapped_column(str_enum(InboxItemType, "inbox_item_type"))
    # Short description shown on the card, in Spanish.
    summary: Mapped[str] = mapped_column(String(500))
    status: Mapped[InboxItemStatus] = mapped_column(
        str_enum(InboxItemStatus, "inbox_item_status"), default=InboxItemStatus.OPEN
    )
    priority: Mapped[int] = mapped_column(SmallInteger, default=3)
    resolved_at: Mapped[dt.datetime | None]
