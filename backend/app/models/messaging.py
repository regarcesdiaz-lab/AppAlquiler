"""The assistant's persona and every message sent or received."""

import uuid

from sqlalchemy import CheckConstraint, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import OrgScopedMixin, TimestampMixin, org_table_args, scoped_fk, str_enum
from app.models.enums import AssistantTone, MessageChannel, MessageDirection, MessageStatus


class AssistantProfile(OrgScopedMixin, TimestampMixin, Base):
    """Name and tone the assistant uses when writing to tenants (one per organization)."""

    __tablename__ = "assistant_profiles"
    __table_args__ = org_table_args(UniqueConstraint("organization_id"))

    display_name: Mapped[str] = mapped_column(String(60))
    tone: Mapped[AssistantTone] = mapped_column(
        str_enum(AssistantTone, "assistant_tone"), default=AssistantTone.CORDIAL
    )


class Message(OrgScopedMixin, TimestampMixin, Base):
    """A WhatsApp, email or push message, inbound or outbound, with its delivery status."""

    __tablename__ = "messages"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("tenant_id", "tenants"),
        CheckConstraint(
            "(direction = 'inbound' AND status = 'received')"
            " OR (direction = 'outbound' AND status <> 'received')",
            name="status_matches_direction",
        ),
        # Each provider message (e.g. a WhatsApp wamid) is processed only once. Outbound
        # messages have no provider id until the provider accepts them.
        Index(
            "uq_messages_provider_message_id",
            "provider_message_id",
            unique=True,
            postgresql_where=text("provider_message_id IS NOT NULL"),
        ),
        Index("ix_messages_lease_created", "lease_id", "created_at"),
        Index("ix_messages_tenant_created", "tenant_id", "created_at"),
    )

    lease_id: Mapped[uuid.UUID | None]
    tenant_id: Mapped[uuid.UUID | None]
    channel: Mapped[MessageChannel] = mapped_column(str_enum(MessageChannel, "message_channel"))
    direction: Mapped[MessageDirection] = mapped_column(
        str_enum(MessageDirection, "message_direction")
    )
    # Approved WhatsApp template name for business-initiated messages.
    template: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus, "message_status"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(String(500))
