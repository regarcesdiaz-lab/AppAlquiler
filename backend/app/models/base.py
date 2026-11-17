"""Shared columns and helpers for models.

Multi-tenancy (CLAUDE.md, rule 8) is enforced by the database, not only by queries:

* Every business table has ``organization_id`` and ``UNIQUE (organization_id, id)``.
* References between business tables are composite foreign keys
  ``(organization_id, x_id) -> x (organization_id, id)``, so a row can never point at a row
  of another organization, even if application code has a bug.
* Deleting an organization cascades to all its rows (account deletion, rule 13).
"""

import datetime as dt
import uuid
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, ForeignKeyConstraint, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.sql.schema import SchemaItem


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Text column limited by a CHECK constraint to the enum's values."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


def scoped_fk(column: str, target_table: str, ondelete: str | None = None) -> ForeignKeyConstraint:
    """Composite FK that also guarantees the referenced row belongs to the same organization."""
    return ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target_table}.organization_id", f"{target_table}.id"],
        ondelete=ondelete,
    )


def org_table_args(*items: SchemaItem) -> tuple[SchemaItem, ...]:
    """``__table_args__`` for organization-scoped tables: target for scoped_fk() + extras."""
    return (UniqueConstraint("organization_id", "id"), *items)


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        sort_order=-100,
    )


class CreatedAtMixin:
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now(), sort_order=100)


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[dt.datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), sort_order=101
    )


class OrgScopedMixin(IdMixin):
    @declared_attr
    @classmethod
    def organization_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, sort_order=-99
        )


class AppendOnlyMixin:
    """Rows are immutable once written.

    Enforced twice: an ORM guard (app.models.guards) and a PostgreSQL trigger created in the
    migration, which also blocks raw SQL UPDATE, DELETE and TRUNCATE.
    """
