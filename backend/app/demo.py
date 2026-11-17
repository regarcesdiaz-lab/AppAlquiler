"""Demo data: one organization with 2 buildings, 7 units (1 vacant) and 6 tenants.

Dates are relative to "today" in the business time zone, so the demo always shows the same
situation whenever it is loaded:

* paid (x2)             every charge paid and confirmed
* pending_confirmation  due 2 days ago; the tenant sent a receipt the AI read, not confirmed
* promise               due 6 days ago; active promise to pay in 4 days
* upcoming              due in 5 days; charge already posted, not paid yet
* overdue               due 12 days ago (past the 3-day grace); late fee posted

Run inside Docker:  docker compose exec api python -m scripts.seed_demo [--reset]
"""

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import business_zone
from app.domain.periods import add_months, due_date, period_of
from app.domain.rule_templates import PRESETS, RulePreset, RuleTemplate
from app.models import (
    AssistantProfile,
    BankAccount,
    Building,
    ConsentRecord,
    Document,
    InboxItem,
    Issue,
    Lease,
    LedgerEntry,
    Message,
    Organization,
    Payment,
    PaymentPromise,
    Receipt,
    RuleSet,
    Tenant,
    TimelineEvent,
    Unit,
    User,
)
from app.models.enums import (
    AccountType,
    ActorType,
    AssistantTone,
    ConsentMethod,
    DocumentType,
    InboxItemType,
    IssueStatus,
    LedgerEntryType,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    PaymentSource,
    PaymentStatus,
    PromiseCreator,
    UserRole,
)

DEMO_OWNER_EMAIL = "carmen.demo@example.com"
HISTORY_MONTHS = 3


class DemoAlreadySeededError(RuntimeError):
    pass


class Scenario(StrEnum):
    PAID = "paid"
    PENDING_CONFIRMATION = "pending_confirmation"
    PROMISE = "promise"
    UPCOMING = "upcoming"
    OVERDUE = "overdue"


@dataclass(frozen=True)
class _TenantSpec:
    building: str
    unit: str
    name: str
    phone: str
    rent: Decimal
    scenario: Scenario
    # Days from today to the due date of the scenario's current charge (None: fixed due_day).
    due_offset: int | None = None
    due_day: int | None = None
    flexible_rules: bool = False


_BUILDINGS = {
    "palmas": ("Residencial Las Palmas", "Calle Las Palmas 12, Naco, Santo Domingo"),
    "mirador": ("Torre Mirador", "Av. Juan Pablo Duarte 85, Santiago"),
}

_UNITS = [
    ("palmas", "1A"),
    ("palmas", "1B"),
    ("palmas", "2A"),
    ("palmas", "2B"),
    ("mirador", "301"),
    ("mirador", "302"),
    ("mirador", "401"),  # vacant
]

# 555 numbers are fictional in the North American Numbering Plan (+1 809/829/849).
_TENANTS = [
    _TenantSpec(
        "palmas",
        "1A",
        "María Fernández",
        "+18095550101",
        Decimal("25000.00"),
        Scenario.PAID,
        due_day=1,
    ),
    _TenantSpec(
        "palmas",
        "1B",
        "José Martínez",
        "+18295550102",
        Decimal("22000.00"),
        Scenario.PAID,
        due_day=5,
        flexible_rules=True,
    ),
    _TenantSpec(
        "palmas",
        "2A",
        "Ana Peña",
        "+18495550103",
        Decimal("28000.00"),
        Scenario.PENDING_CONFIRMATION,
        due_offset=-2,
    ),
    _TenantSpec(
        "palmas",
        "2B",
        "Luis Castillo",
        "+18095550104",
        Decimal("24000.00"),
        Scenario.PROMISE,
        due_offset=-6,
    ),
    _TenantSpec(
        "mirador",
        "301",
        "Rosa Jiménez",
        "+18295550105",
        Decimal("35000.00"),
        Scenario.UPCOMING,
        due_offset=5,
    ),
    _TenantSpec(
        "mirador",
        "302",
        "Pedro Almonte",
        "+18095550106",
        Decimal("30000.00"),
        Scenario.OVERDUE,
        due_offset=-12,
    ),
]


@dataclass
class DemoResult:
    organization_id: uuid.UUID
    owner_id: uuid.UUID
    vacant_unit_id: uuid.UUID
    leases: dict[uuid.UUID, Scenario] = field(default_factory=dict)


def _format_dop(amount: Decimal) -> str:
    """RD$25,000 (no decimals when they are ,00), as in docs/BRAND.md."""
    if amount == amount.to_integral_value():
        return f"RD${amount:,.0f}"
    return f"RD${amount:,.2f}"


class _Builder:
    def __init__(self, db: Session, today: dt.date) -> None:
        self.db = db
        self.today = today
        self.zone = business_zone()
        self.org: Organization
        self.owner: User
        self._confirmed: list[Payment] = []

    # -- helpers -----------------------------------------------------------------------------

    def add[T](self, obj: T) -> T:
        self.db.add(obj)
        self.db.flush()
        return obj

    def at(self, day: dt.date, hour: int, minute: int = 0) -> dt.datetime:
        """A local wall-clock time in Santo Domingo, as a UTC timestamp."""
        return dt.datetime.combine(day, dt.time(hour, minute), tzinfo=self.zone).astimezone(dt.UTC)

    def event(
        self,
        lease: Lease | None,
        event_type: str,
        actor: ActorType,
        when: dt.datetime,
        **payload: Any,
    ) -> None:
        self.add(
            TimelineEvent(
                organization_id=self.org.id,
                lease_id=lease.id if lease else None,
                unit_id=lease.unit_id if lease else None,
                event_type=event_type,
                actor_type=actor,
                payload=payload,
                occurred_at=when,
                created_at=when,
            )
        )

    def message(
        self,
        lease: Lease,
        direction: MessageDirection,
        body: str,
        when: dt.datetime,
        template: str | None = None,
    ) -> Message:
        inbound = direction == MessageDirection.INBOUND
        msg = self.add(
            Message(
                organization_id=self.org.id,
                lease_id=lease.id,
                tenant_id=lease.tenant_id,
                channel=MessageChannel.WHATSAPP,
                direction=direction,
                template=template,
                body=body,
                status=MessageStatus.RECEIVED if inbound else MessageStatus.READ,
                provider_message_id=f"wamid.DEMO{uuid.uuid4().hex.upper()}",
                created_at=when,
                updated_at=when,
            )
        )
        self.event(
            lease,
            "message.received" if inbound else "message.sent",
            ActorType.TENANT if inbound else ActorType.AGENT,
            when,
            message_id=str(msg.id),
            template=template,
        )
        return msg

    def charge(self, lease: Lease, period: str) -> None:
        when = self.at(due_date(period, lease.due_day) - dt.timedelta(days=7), 9)
        self.add(
            LedgerEntry(
                organization_id=self.org.id,
                lease_id=lease.id,
                type=LedgerEntryType.CHARGE,
                amount=lease.rent_amount,
                period=period,
                reference=f"Renta {period}",
                created_at=when,
            )
        )
        self.event(
            lease,
            "charge.posted",
            ActorType.SYSTEM,
            when,
            period=period,
            amount=str(lease.rent_amount),
        )

    def confirmed_payment(
        self, lease: Lease, period: str, source: PaymentSource, bank_account: BankAccount
    ) -> None:
        paid_on = min(
            due_date(period, lease.due_day) - dt.timedelta(days=1),
            self.today - dt.timedelta(days=1),
        )
        reported_at = self.at(paid_on, 10, 30)
        confirmed_at = self.at(paid_on, 18)
        by_receipt = source == PaymentSource.WHATSAPP_RECEIPT
        payment = self.add(
            Payment(
                organization_id=self.org.id,
                lease_id=lease.id,
                destination_bank_account_id=bank_account.id if by_receipt else None,
                amount=lease.rent_amount,
                paid_on=paid_on,
                bank="Banco Popular" if by_receipt else None,
                reference=f"{uuid.uuid4().int % 10**10:010d}" if by_receipt else None,
                status=PaymentStatus.CONFIRMED,
                source=source,
                file_key=f"demo/receipts/{uuid.uuid4()}.jpg" if by_receipt else None,
                confirmed_by_user_id=self.owner.id,
                confirmed_at=confirmed_at,
                created_at=reported_at,
                updated_at=confirmed_at,
            )
        )
        self.add(
            LedgerEntry(
                organization_id=self.org.id,
                lease_id=lease.id,
                payment_id=payment.id,
                type=LedgerEntryType.PAYMENT,
                amount=-lease.rent_amount,
                period=period,
                reference=f"Pago {period}",
                created_at=confirmed_at,
            )
        )
        if by_receipt:
            self.event(
                lease,
                "payment.reported",
                ActorType.AGENT,
                reported_at,
                payment_id=str(payment.id),
                amount=str(payment.amount),
            )
        self.event(
            lease,
            "payment.confirmed",
            ActorType.OWNER,
            confirmed_at,
            payment_id=str(payment.id),
            amount=str(payment.amount),
            period=period,
        )
        self._confirmed.append(payment)

    def issue_receipts(self) -> None:
        """Number receipts in the order payments were confirmed, as production would."""
        number = self.org.next_receipt_number
        for payment in sorted(self._confirmed, key=lambda p: (p.confirmed_at, str(p.id))):
            issued_at = payment.confirmed_at
            receipt = self.add(
                Receipt(
                    organization_id=self.org.id,
                    payment_id=payment.id,
                    number=number,
                    pdf_key=f"demo/receipts/recibo-{number:05d}.pdf",
                    issued_at=issued_at,
                    created_at=issued_at,
                )
            )
            lease = self.db.get(Lease, payment.lease_id)
            self.event(
                lease,
                "receipt.issued",
                ActorType.SYSTEM,
                issued_at,
                receipt_id=str(receipt.id),
                number=number,
            )
            number += 1
        self.org.next_receipt_number = number
        self.db.flush()

    def rule_set(self, name: str, preset: RulePreset, *, is_default: bool) -> RuleSet:
        return self.add(
            RuleSet(
                organization_id=self.org.id,
                name=name,
                template=preset.template,
                is_default=is_default,
                grace_days=preset.grace_days,
                late_fee_type=preset.late_fee_type,
                late_fee_value=preset.late_fee_value,
                max_extension_days=preset.max_extension_days,
                max_extensions_per_year=preset.max_extensions_per_year,
                contact_start=preset.contact_start,
                contact_end=preset.contact_end,
                contact_days=list(preset.contact_days),
                reminder_sequence=preset.reminder_sequence_json(),
            )
        )

    # -- the demo ----------------------------------------------------------------------------

    def build(self) -> DemoResult:
        today = self.today
        account_start = due_date(add_months(period_of(today), -HISTORY_MONTHS - 1), 1)

        self.org = self.add(Organization(name="Inversiones Rodríguez"))
        self.owner = self.add(
            User(
                organization_id=self.org.id,
                email=DEMO_OWNER_EMAIL,
                full_name="Carmen Rodríguez",
                whatsapp_phone="+18095550100",
                role=UserRole.OWNER,
            )
        )
        self.add(
            AssistantProfile(
                organization_id=self.org.id, display_name="Lucía", tone=AssistantTone.CORDIAL
            )
        )
        standard = PRESETS[RuleTemplate.STANDARD]
        # Leases without rule_set_id use the organization's default rule set.
        self.rule_set("Estándar", standard, is_default=True)
        flexible_rules = self.rule_set("Flexible", PRESETS[RuleTemplate.FLEXIBLE], is_default=False)
        bank_account = self.add(
            BankAccount(
                organization_id=self.org.id,
                bank_name="Banco Popular",
                account_type=AccountType.SAVINGS,
                account_number="7801234567",
                holder_name="Carmen Rodríguez",
                alias="Popular ahorros",
            )
        )

        buildings = {
            key: self.add(Building(organization_id=self.org.id, name=name, address=address))
            for key, (name, address) in _BUILDINGS.items()
        }
        units = {
            (b, code): self.add(
                Unit(organization_id=self.org.id, building_id=buildings[b].id, code=code)
            )
            for b, code in _UNITS
        }

        result = DemoResult(
            organization_id=self.org.id,
            owner_id=self.owner.id,
            vacant_unit_id=units[("mirador", "401")].id,
        )

        for spec in _TENANTS:
            lease = self._tenant_with_lease(
                spec,
                units[(spec.building, spec.unit)],
                account_start,
                flexible_rules if spec.flexible_rules else None,
            )
            result.leases[lease.id] = spec.scenario

            if spec.due_offset is None:
                current_period = period_of(today)
            else:
                current_period = period_of(today + dt.timedelta(days=spec.due_offset))

            period = period_of(lease.start_date)
            history_index = 0
            while period <= current_period:
                self.charge(lease, period)
                is_current = period == current_period
                if spec.scenario == Scenario.PAID or not is_current:
                    source = (
                        PaymentSource.WHATSAPP_RECEIPT
                        if history_index % 3 != 2
                        else PaymentSource.MANUAL_CASH
                    )
                    self.confirmed_payment(lease, period, source, bank_account)
                history_index += 1
                period = add_months(period, 1)

            self._scenario_details(spec, lease, current_period, bank_account, standard)

        self.issue_receipts()
        return result

    def _tenant_with_lease(
        self,
        spec: _TenantSpec,
        unit: Unit,
        account_start: dt.date,
        rule_set: RuleSet | None,
    ) -> Lease:
        tenant = self.add(
            Tenant(organization_id=self.org.id, full_name=spec.name, phone=spec.phone)
        )
        if spec.due_offset is None:
            assert spec.due_day is not None
            due_day = spec.due_day
        else:
            due_day = (self.today + dt.timedelta(days=spec.due_offset)).day
        start = due_date(add_months(period_of(self.today), -HISTORY_MONTHS), 1)
        lease = self.add(
            Lease(
                organization_id=self.org.id,
                unit_id=unit.id,
                tenant_id=tenant.id,
                rule_set_id=rule_set.id if rule_set else None,
                rent_amount=spec.rent,
                due_day=due_day,
                deposit_amount=spec.rent * 2,
                start_date=start,
                end_date=add_years(start, 1),
                is_active=True,
            )
        )
        signed_at = self.at(account_start + dt.timedelta(days=20), 11)
        self.event(
            lease, "lease.created", ActorType.OWNER, signed_at, rent=str(spec.rent), due_day=due_day
        )
        self.add(
            ConsentRecord(
                organization_id=self.org.id,
                lease_id=lease.id,
                tenant_id=tenant.id,
                method=ConsentMethod.CONTRACT_CLAUSE,
                evidence=(
                    "Cláusula 12: el inquilino acepta recibir recordatorios de pago y recibos "
                    "por WhatsApp. Puede darse de baja respondiendo BAJA."
                ),
                granted_at=signed_at,
            )
        )
        self.event(
            lease,
            "consent.granted",
            ActorType.OWNER,
            signed_at,
            method=ConsentMethod.CONTRACT_CLAUSE.value,
        )
        self.add(
            Document(
                organization_id=self.org.id,
                lease_id=lease.id,
                type=DocumentType.LEASE_CONTRACT,
                file_key=f"demo/documents/{uuid.uuid4()}.pdf",
                original_filename=f"Contrato {unit.code} - {spec.name}.pdf",
                content_type="application/pdf",
                uploaded_by_user_id=self.owner.id,
                uploaded_at=signed_at,
                created_at=signed_at,
            )
        )
        return lease

    def _scenario_details(
        self,
        spec: _TenantSpec,
        lease: Lease,
        period: str,
        bank_account: BankAccount,
        standard: RulePreset,
    ) -> None:
        today = self.today
        first_name = spec.name.split()[0]
        rent = _format_dop(lease.rent_amount)
        due = due_date(period, lease.due_day)

        if spec.scenario == Scenario.PENDING_CONFIRMATION:
            sent_at = self.at(today - dt.timedelta(days=1), 16, 40)
            self.message(
                lease,
                MessageDirection.INBOUND,
                "Buenas tardes, le envío el comprobante de la renta.",
                sent_at,
            )
            payment = self.add(
                Payment(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    destination_bank_account_id=bank_account.id,
                    amount=lease.rent_amount,
                    paid_on=today - dt.timedelta(days=1),
                    bank="Banreservas",
                    reference="4589012367",
                    status=PaymentStatus.REPORTED,
                    source=PaymentSource.WHATSAPP_RECEIPT,
                    file_key=f"demo/receipts/{uuid.uuid4()}.jpg",
                    ai_extracted={
                        "amount": str(lease.rent_amount),
                        "currency": "DOP",
                        "paid_on": (today - dt.timedelta(days=1)).isoformat(),
                        "origin_bank": "Banreservas",
                        "destination_bank": "Banco Popular",
                        "destination_account_last4": bank_account.account_number[-4:],
                        "reference": "4589012367",
                        "confidence": 0.94,
                    },
                    created_at=sent_at,
                    updated_at=sent_at,
                )
            )
            self.event(
                lease,
                "payment.reported",
                ActorType.AGENT,
                sent_at,
                payment_id=str(payment.id),
                amount=str(payment.amount),
            )
            self.message(
                lease,
                MessageDirection.OUTBOUND,
                f"Gracias, {first_name}. Recibimos tu comprobante y se lo pasamos a Carmen "
                "para que lo confirme.",
                sent_at + dt.timedelta(minutes=1),
            )
            self.add(
                InboxItem(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    payment_id=payment.id,
                    type=InboxItemType.PAYMENT_TO_CONFIRM,
                    summary=(
                        f"{spec.name} reportó un pago de {_format_dop(payment.amount)}. "
                        "Revisa el comprobante y confírmalo."
                    ),
                    priority=1,
                    created_at=sent_at,
                    updated_at=sent_at,
                )
            )

        elif spec.scenario == Scenario.PROMISE:
            reminder_at = self.at(due + dt.timedelta(days=1), 9)
            self.message(
                lease,
                MessageDirection.OUTBOUND,
                f"Hola, {first_name}. Te recordamos que la renta de {rent} "
                f"venció el día {due.day}.",
                reminder_at,
                template="reminder_overdue",
            )
            asked_at = reminder_at + dt.timedelta(hours=2)
            promised = today + dt.timedelta(days=4)
            self.message(
                lease,
                MessageDirection.INBOUND,
                f"Buenos días. Me pagan el {promised.day}, ¿puedo pagar ese día?",
                asked_at,
            )
            promise = self.add(
                PaymentPromise(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    promised_date=promised,
                    amount=None,
                    created_by=PromiseCreator.TENANT,
                    created_at=asked_at,
                    updated_at=asked_at,
                )
            )
            self.event(
                lease,
                "promise.created",
                ActorType.TENANT,
                asked_at,
                promise_id=str(promise.id),
                promised_date=promised.isoformat(),
            )
            self.message(
                lease,
                MessageDirection.OUTBOUND,
                f"Perfecto, {first_name}. Queda anotado para el {promised.day}. "
                "Te escribimos ese día.",
                asked_at + dt.timedelta(minutes=1),
            )

        elif spec.scenario == Scenario.OVERDUE:
            for days_after, template in ((4, "reminder_overdue"), (8, "reminder_overdue_final")):
                self.message(
                    lease,
                    MessageDirection.OUTBOUND,
                    f"Hola, {first_name}. La renta de {rent} venció el día "
                    f"{due.day} y aún no la hemos recibido.",
                    self.at(due + dt.timedelta(days=days_after), 9),
                    template=template,
                )
            fee_at = self.at(due + dt.timedelta(days=standard.grace_days + 1), 8)
            fee = (lease.rent_amount * standard.late_fee_value / Decimal(100)).quantize(
                Decimal("0.01")
            )
            self.add(
                LedgerEntry(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    type=LedgerEntryType.LATE_FEE,
                    amount=fee,
                    period=period,
                    reference=f"Mora {period}",
                    created_at=fee_at,
                )
            )
            self.event(
                lease, "late_fee.posted", ActorType.SYSTEM, fee_at, period=period, amount=str(fee)
            )
            asked_at = self.at(today - dt.timedelta(days=1), 20, 15)
            self.message(
                lease,
                MessageDirection.INBOUND,
                "Buenas. ¿Puedo pagar la mitad ahora y la otra mitad a fin de mes?",
                asked_at,
            )
            self.add(
                InboxItem(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    type=InboxItemType.TENANT_MESSAGE,
                    summary=(
                        f"{spec.name} pregunta si puede pagar la mitad ahora y la otra mitad "
                        "a fin de mes."
                    ),
                    priority=2,
                    created_at=asked_at,
                    updated_at=asked_at,
                )
            )

        elif spec.scenario == Scenario.UPCOMING:
            reported_at = self.at(today - dt.timedelta(days=3), 13)
            issue = self.add(
                Issue(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    description="Se está filtrando agua debajo del fregadero de la cocina.",
                    photo_key=f"demo/issues/{uuid.uuid4()}.jpg",
                    status=IssueStatus.OPEN,
                    created_at=reported_at,
                    updated_at=reported_at,
                )
            )
            self.event(
                lease, "issue.reported", ActorType.TENANT, reported_at, issue_id=str(issue.id)
            )
            self.add(
                InboxItem(
                    organization_id=self.org.id,
                    lease_id=lease.id,
                    issue_id=issue.id,
                    type=InboxItemType.ISSUE_REPORTED,
                    summary=f"{spec.name} reportó una avería: filtración debajo del fregadero.",
                    priority=3,
                    created_at=reported_at,
                    updated_at=reported_at,
                )
            )


def add_years(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def seed_demo(db: Session, today: dt.date) -> DemoResult:
    """Create the demo organization. The caller commits."""
    if db.scalar(select(User.id).where(User.email == DEMO_OWNER_EMAIL)) is not None:
        raise DemoAlreadySeededError(
            f"Demo data already exists ({DEMO_OWNER_EMAIL}); use --reset to recreate it."
        )
    return _Builder(db, today).build()


def demo_organization_id(db: Session) -> uuid.UUID | None:
    return db.scalar(select(User.organization_id).where(User.email == DEMO_OWNER_EMAIL))
