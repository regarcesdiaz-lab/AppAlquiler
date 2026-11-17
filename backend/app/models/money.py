"""Bank accounts, the ledger, payments, promises and receipts. Money is Decimal(12, 2)."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Index, String, UniqueConstraint, text
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
from app.models.enums import (
    AccountType,
    LedgerEntryType,
    PaymentSource,
    PaymentStatus,
    PromiseCreator,
    PromiseStatus,
)

PERIOD_CHECK = r"period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'"


class BankAccount(OrgScopedMixin, TimestampMixin, Base):
    """An owner's account where tenants deposit; reconciliation checks receipts against it."""

    __tablename__ = "bank_accounts"
    __table_args__ = org_table_args(
        UniqueConstraint("organization_id", "bank_name", "account_number"),
        Index("ix_bank_accounts_org_active", "organization_id", postgresql_where=text("is_active")),
    )

    bank_name: Mapped[str] = mapped_column(String(100))
    account_type: Mapped[AccountType] = mapped_column(str_enum(AccountType, "account_type"))
    account_number: Mapped[str] = mapped_column(String(34))
    holder_name: Mapped[str] = mapped_column(String(200))
    alias: Mapped[str | None] = mapped_column(String(60))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class LedgerEntry(OrgScopedMixin, AppendOnlyMixin, CreatedAtMixin, Base):
    """Append-only ledger. Signed amounts: charges and late fees add, payments subtract.

    The balance of a lease is SUM(amount). Mistakes are corrected with an "adjustment" entry,
    never by editing or deleting a row.
    """

    __tablename__ = "ledger_entries"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("payment_id", "payments"),
        CheckConstraint(PERIOD_CHECK, name="period_format"),
        CheckConstraint(
            "(type IN ('charge', 'late_fee') AND amount > 0)"
            " OR (type = 'payment' AND amount < 0 AND payment_id IS NOT NULL)"
            " OR (type = 'adjustment' AND amount <> 0)",
            name="amount_sign_by_type",
        ),
        CheckConstraint(
            "payment_id IS NULL OR type IN ('payment', 'adjustment')",
            name="payment_only_on_payment_or_adjustment",
        ),
        # One rent charge per lease and period; one ledger payment per payment.
        Index(
            "uq_ledger_entries_charge_period",
            "lease_id",
            "period",
            unique=True,
            postgresql_where=text("type = 'charge'"),
        ),
        Index(
            "uq_ledger_entries_payment",
            "payment_id",
            unique=True,
            postgresql_where=text("type = 'payment'"),
        ),
        # Home screen: collected vs expected for the month.
        Index("ix_ledger_entries_org_period_type", "organization_id", "period", "type"),
        Index("ix_ledger_entries_lease_period", "lease_id", "period"),
    )

    lease_id: Mapped[uuid.UUID]
    payment_id: Mapped[uuid.UUID | None]
    type: Mapped[LedgerEntryType] = mapped_column(str_enum(LedgerEntryType, "ledger_entry_type"))
    amount: Mapped[Decimal]
    period: Mapped[str] = mapped_column(String(7))
    reference: Mapped[str | None] = mapped_column(String(200))


class Payment(OrgScopedMixin, TimestampMixin, Base):
    """A payment reported by the tenant or registered by the owner.

    The AI never confirms a payment (rule 3): reconciliation leaves it "reported" and only a
    user can move it to "confirmed", which the database enforces by requiring
    confirmed_by_user_id.
    """

    __tablename__ = "payments"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        scoped_fk("destination_bank_account_id", "bank_accounts"),
        scoped_fk("confirmed_by_user_id", "users"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "status <> 'confirmed'"
            " OR (confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="confirmed_by_a_user",
        ),
        CheckConstraint(
            "source <> 'whatsapp_receipt' OR file_key IS NOT NULL",
            name="whatsapp_receipt_has_file",
        ),
        # Home screen: payments waiting for the owner's confirmation.
        Index(
            "ix_payments_org_reported",
            "organization_id",
            "created_at",
            postgresql_where=text("status = 'reported'"),
        ),
        Index("ix_payments_lease_paid_on", "lease_id", "paid_on"),
    )

    lease_id: Mapped[uuid.UUID]
    # The owner's account the money reached, when reconciliation could match it.
    destination_bank_account_id: Mapped[uuid.UUID | None]
    amount: Mapped[Decimal]
    paid_on: Mapped[dt.date]
    # Bank the tenant paid from, as written on the receipt.
    bank: Mapped[str | None] = mapped_column(String(100))
    reference: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus, "payment_status"), default=PaymentStatus.REPORTED
    )
    source: Mapped[PaymentSource] = mapped_column(str_enum(PaymentSource, "payment_source"))
    # Storage key of the receipt image or PDF.
    file_key: Mapped[str | None] = mapped_column(String(512))
    # What the AI read from the receipt, validated with Pydantic before saving.
    ai_extracted: Mapped[dict[str, Any] | None]
    confirmed_by_user_id: Mapped[uuid.UUID | None]
    confirmed_at: Mapped[dt.datetime | None]


class PaymentPromise(OrgScopedMixin, TimestampMixin, Base):
    """A payment agreement; collection reminders pause while it is active."""

    __tablename__ = "payment_promises"
    __table_args__ = org_table_args(
        scoped_fk("lease_id", "leases"),
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        # At most one active promise per lease.
        Index(
            "uq_payment_promises_active",
            "lease_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    lease_id: Mapped[uuid.UUID]
    promised_date: Mapped[dt.date]
    # NULL means the full outstanding balance.
    amount: Mapped[Decimal | None]
    status: Mapped[PromiseStatus] = mapped_column(
        str_enum(PromiseStatus, "promise_status"), default=PromiseStatus.ACTIVE
    )
    created_by: Mapped[PromiseCreator] = mapped_column(str_enum(PromiseCreator, "promise_creator"))


class Receipt(OrgScopedMixin, CreatedAtMixin, Base):
    """PDF receipt for a confirmed payment, numbered sequentially per organization."""

    __tablename__ = "receipts"
    __table_args__ = org_table_args(
        scoped_fk("payment_id", "payments"),
        UniqueConstraint("organization_id", "number"),
        CheckConstraint("number >= 1", name="number_positive"),
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(unique=True)
    number: Mapped[int]
    # Storage key of the generated PDF (NULL until the PDF job finishes).
    pdf_key: Mapped[str | None] = mapped_column(String(512))
    issued_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
