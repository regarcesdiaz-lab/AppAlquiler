"""The assistant's persona, every message sent or received, and the WhatsApp channel's own
platform tables (idempotency and per-number conversation state)."""

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.account import E164_CHECK
from app.models.base import (
    CreatedAtMixin,
    IdMixin,
    OrgScopedMixin,
    TimestampMixin,
    org_table_args,
    scoped_fk,
    str_enum,
)
from app.models.enums import (
    AssistantTone,
    ContactType,
    MessageChannel,
    MessageContentType,
    MessageDirection,
    MessageKind,
    MessageStatus,
)


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
        scoped_fk("user_id", "users"),
        CheckConstraint(
            "(direction = 'inbound' AND status = 'received')"
            " OR (direction = 'outbound' AND status <> 'received')",
            name="status_matches_direction",
        ),
        CheckConstraint(
            f"contact_phone IS NULL OR contact_phone {E164_CHECK}", name="contact_phone_e164"
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
        # Monthly usage per organization (pricing).
        Index("ix_messages_org_sent", "organization_id", "sent_at"),
        # Scheduled and pending sends (the worker re-queues the ones that are due).
        Index(
            "ix_messages_queued_scheduled",
            "scheduled_for",
            postgresql_where=text("status = 'queued'"),
        ),
    )

    lease_id: Mapped[uuid.UUID | None]
    tenant_id: Mapped[uuid.UUID | None]
    # Set when the other side is the owner (owner assistant, phase 8).
    user_id: Mapped[uuid.UUID | None]
    channel: Mapped[MessageChannel] = mapped_column(str_enum(MessageChannel, "message_channel"))
    direction: Mapped[MessageDirection] = mapped_column(
        str_enum(MessageDirection, "message_direction")
    )
    # What the rules engine validated (outbound only): reminder, welcome, reply...
    kind: Mapped[MessageKind | None] = mapped_column(str_enum(MessageKind, "message_kind"))
    contact_type: Mapped[ContactType] = mapped_column(
        str_enum(ContactType, "contact_type"),
        default=ContactType.TENANT,
        server_default=ContactType.TENANT.value,
    )
    contact_phone: Mapped[str | None] = mapped_column(String(16))
    content_type: Mapped[MessageContentType] = mapped_column(
        str_enum(MessageContentType, "message_content_type"),
        default=MessageContentType.TEXT,
        server_default=MessageContentType.TEXT.value,
    )
    # Approved WhatsApp template name (e.g. "aviso_amable_tu") when it went out as a template.
    template: Mapped[str | None] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    # Template key and parameters, session content, button taps. Never secrets.
    payload: Mapped[dict[str, Any]] = mapped_column(
        default=dict, server_default=text("'{}'::jsonb")
    )
    media_key: Mapped[str | None] = mapped_column(String(512))
    media_mime: Mapped[str | None] = mapped_column(String(100))
    # Provider id of the message this one answers (WhatsApp "context").
    context_provider_message_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[MessageStatus] = mapped_column(str_enum(MessageStatus, "message_status"))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(String(500))
    # Outside the contact window a business-initiated message waits until this moment.
    scheduled_for: Mapped[dt.datetime | None]
    sent_at: Mapped[dt.datetime | None]
    status_updated_at: Mapped[dt.datetime | None]


class WhatsAppInboundEvent(IdMixin, CreatedAtMixin, Base):
    """Every inbound WhatsApp message, once (idempotency by provider_message_id).

    Platform table, not scoped to an organization: a message from an unknown number belongs to
    none. When the router resolves the organization it is recorded here, and deleting that
    organization deletes the row.
    """

    __tablename__ = "whatsapp_inbound_events"
    __table_args__ = (
        CheckConstraint(f"phone {E164_CHECK}", name="phone_e164"),
        Index("ix_whatsapp_inbound_events_phone_received", "phone", "received_at"),
    )

    provider_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    phone: Mapped[str] = mapped_column(String(16))
    received_at: Mapped[dt.datetime]
    # The normalized event (app.channels.whatsapp.base.InboundEvent).
    event: Mapped[dict[str, Any]]
    # tenant | owner | unknown | awaiting_choice | ignored (set once processed).
    routing: Mapped[str | None] = mapped_column(String(32))
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[uuid.UUID | None]


class WhatsAppConversation(TimestampMixin, Base):
    """Per phone number: the 24-hour window, the lease the number chose and rate limits.

    The central number is shared by every organization, so Meta's 24-hour customer service
    window is per phone, not per organization. Keys look like "lease:<uuid>" or "owner:<uuid>"
    and are always checked again against the number's current candidates before use.
    """

    __tablename__ = "whatsapp_conversations"
    __table_args__ = (CheckConstraint(f"phone {E164_CHECK}", name="phone_e164"),)

    phone: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_inbound_at: Mapped[dt.datetime | None]
    bound_key: Mapped[str | None] = mapped_column(String(80))
    bound_until: Mapped[dt.datetime | None]
    # Candidates offered in the last "which contract?" question, in order (answers 1, 2...).
    choice_options: Mapped[list[Any]] = mapped_column(
        default=list, server_default=text("'[]'::jsonb")
    )
    # Inbound events waiting for that answer (ids of whatsapp_inbound_events).
    pending_event_ids: Mapped[list[Any]] = mapped_column(
        default=list, server_default=text("'[]'::jsonb")
    )
    last_unknown_reply_at: Mapped[dt.datetime | None]
    last_opt_out_notice_at: Mapped[dt.datetime | None]
