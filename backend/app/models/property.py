"""Buildings, units (apartments) and tenants."""

import uuid

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.account import E164_CHECK
from app.models.base import OrgScopedMixin, TimestampMixin, org_table_args, scoped_fk


class Building(OrgScopedMixin, TimestampMixin, Base):
    """Optional grouping of units at the same address."""

    __tablename__ = "buildings"
    __table_args__ = org_table_args(UniqueConstraint("organization_id", "name"))

    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(String(300))


class Unit(OrgScopedMixin, TimestampMixin, Base):
    """An apartment. Occupied/vacant is derived from having an active lease (not stored)."""

    __tablename__ = "units"
    __table_args__ = org_table_args(
        scoped_fk("building_id", "buildings"),
        # Codes are unique per building; standalone units (no building) share one namespace.
        UniqueConstraint(
            "organization_id", "building_id", "code", postgresql_nulls_not_distinct=True
        ),
    )

    building_id: Mapped[uuid.UUID | None]
    code: Mapped[str] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(300))


class Tenant(OrgScopedMixin, TimestampMixin, Base):
    """A contact, not a user: never signs in. Reached by WhatsApp from the central number."""

    __tablename__ = "tenants"
    __table_args__ = org_table_args(
        CheckConstraint(f"phone {E164_CHECK}", name="phone_e164"),
        UniqueConstraint("organization_id", "phone"),
        # The WhatsApp router looks tenants up by number across all organizations.
        Index("ix_tenants_phone", "phone"),
    )

    full_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(16))
    email: Mapped[str | None] = mapped_column(String(320))
