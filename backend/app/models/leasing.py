"""Leases (the hub of all collection data), consent and collection rules."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Index, SmallInteger, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.domain.rule_templates import (
    DEFAULT_CONTACT_DAYS,
    DEFAULT_CONTACT_END,
    DEFAULT_CONTACT_START,
)
from app.models.base import OrgScopedMixin, TimestampMixin, org_table_args, scoped_fk, str_enum
from app.models.enums import ConsentMethod, LateFeeType, RuleTemplate


class RuleSet(OrgScopedMixin, TimestampMixin, Base):
    """Collection rules: grace, late fee, extensions, contact hours and reminder sequence."""

    __tablename__ = "rule_sets"
    __table_args__ = org_table_args(
        CheckConstraint("grace_days BETWEEN 0 AND 31", name="grace_days_range"),
        CheckConstraint(
            "(late_fee_type = 'none' AND late_fee_value = 0)"
            " OR (late_fee_type = 'fixed' AND late_fee_value > 0)"
            " OR (late_fee_type = 'percent' AND late_fee_value > 0 AND late_fee_value <= 100)",
            name="late_fee_consistent",
        ),
        CheckConstraint("max_extension_days >= 0", name="max_extension_days_non_negative"),
        CheckConstraint(
            "max_extensions_per_year >= 0", name="max_extensions_per_year_non_negative"
        ),
        CheckConstraint("contact_start < contact_end", name="contact_window_order"),
        CheckConstraint("jsonb_typeof(contact_days) = 'array'", name="contact_days_array"),
        CheckConstraint(
            "jsonb_typeof(reminder_sequence) = 'array'", name="reminder_sequence_array"
        ),
        # At most one default rule set per organization.
        Index(
            "uq_rule_sets_default",
            "organization_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    name: Mapped[str] = mapped_column(String(80))
    template: Mapped[RuleTemplate] = mapped_column(str_enum(RuleTemplate, "rule_template"))
    is_default: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    grace_days: Mapped[int] = mapped_column(SmallInteger)
    late_fee_type: Mapped[LateFeeType] = mapped_column(str_enum(LateFeeType, "late_fee_type"))
    # Fixed amount in DOP, or a percentage of the rent when late_fee_type is "percent".
    late_fee_value: Mapped[Decimal] = mapped_column(default=Decimal("0.00"))
    max_extension_days: Mapped[int] = mapped_column(SmallInteger)
    max_extensions_per_year: Mapped[int] = mapped_column(SmallInteger)
    contact_start: Mapped[dt.time] = mapped_column(default=DEFAULT_CONTACT_START)
    contact_end: Mapped[dt.time] = mapped_column(default=DEFAULT_CONTACT_END)
    # ISO weekdays (1 = Monday). Dominican holidays are always skipped by the rules engine.
    contact_days: Mapped[list[Any]] = mapped_column(default=lambda: list(DEFAULT_CONTACT_DAYS))
    # [{"offset_days": -3, "template": "reminder_upcoming"}, ...]; validated with Pydantic.
    reminder_sequence: Mapped[list[Any]] = mapped_column(default=list)


class Lease(OrgScopedMixin, TimestampMixin, Base):
    """A rental contract between a unit and a tenant. Collection data hangs from it."""

    __tablename__ = "leases"
    __table_args__ = org_table_args(
        scoped_fk("unit_id", "units"),
        scoped_fk("tenant_id", "tenants"),
        scoped_fk("rule_set_id", "rule_sets"),
        CheckConstraint("rent_amount > 0", name="rent_amount_positive"),
        CheckConstraint("deposit_amount >= 0", name="deposit_amount_non_negative"),
        CheckConstraint("due_day BETWEEN 1 AND 31", name="due_day_range"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="end_after_start"),
        # A unit has at most one active lease: "occupied" == has an active lease.
        Index("uq_leases_active_unit", "unit_id", unique=True, postgresql_where=text("is_active")),
        # Home screen: active leases of the organization.
        Index("ix_leases_org_active", "organization_id", postgresql_where=text("is_active")),
        Index("ix_leases_tenant_id", "tenant_id"),
    )

    unit_id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID]
    # NULL means the organization's default rule set.
    rule_set_id: Mapped[uuid.UUID | None]
    rent_amount: Mapped[Decimal]
    due_day: Mapped[int] = mapped_column(SmallInteger)
    deposit_amount: Mapped[Decimal] = mapped_column(default=Decimal("0.00"))
    start_date: Mapped[dt.date]
    end_date: Mapped[dt.date | None]
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class ConsentRecord(OrgScopedMixin, TimestampMixin, Base):
    """Proof that the tenant agreed to receive messages; revoked_at is set on "BAJA"."""

    __tablename__ = "consent_records"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("tenant_id", "tenants"),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at", name="revoked_after_granted"
        ),
        # At most one current (not revoked) consent per lease.
        Index(
            "uq_consent_records_current",
            "lease_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    lease_id: Mapped[uuid.UUID]
    tenant_id: Mapped[uuid.UUID]
    method: Mapped[ConsentMethod] = mapped_column(str_enum(ConsentMethod, "consent_method"))
    # Clause text, or a reference to the WhatsApp reply that granted consent.
    evidence: Mapped[str] = mapped_column(Text)
    granted_at: Mapped[dt.datetime]
    revoked_at: Mapped[dt.datetime | None]
