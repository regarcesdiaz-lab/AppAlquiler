"""Buildings, units, tenants and leases."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.status import TenantState
from app.models.enums import ActorType, ConsentMethod, InboxItemType, PromiseCreator
from app.schemas.common import (
    ApiModel,
    ApiWarning,
    EmailAddress,
    Name,
    NonNegativeMoney,
    Phone,
    PositiveMoney,
)

DEFAULT_CONSENT_EVIDENCE = (
    "El contrato incluye una cláusula en la que el inquilino acepta recibir recordatorios de "
    "pago y recibos por WhatsApp. Puede darse de baja respondiendo BAJA."
)


# -- buildings ---------------------------------------------------------------------------------


class BuildingIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=300)


class BuildingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=300)


class BuildingOut(ApiModel):
    id: uuid.UUID
    name: str
    address: str | None
    unit_count: int = 0


# -- units -------------------------------------------------------------------------------------


class UnitIn(BaseModel):
    building_id: uuid.UUID | None = None
    code: str = Field(min_length=1, max_length=40)
    address: str | None = Field(default=None, max_length=300)


class UnitUpdate(BaseModel):
    building_id: uuid.UUID | None = None
    code: str | None = Field(default=None, min_length=1, max_length=40)
    address: str | None = Field(default=None, max_length=300)


class UnitOut(ApiModel):
    id: uuid.UUID
    building_id: uuid.UUID | None
    code: str
    address: str | None
    active_lease_id: uuid.UUID | None = None


# -- tenants -----------------------------------------------------------------------------------


class TenantIn(BaseModel):
    full_name: Name
    phone: Phone
    email: EmailAddress | None = None


class TenantUpdate(BaseModel):
    full_name: Name | None = None
    phone: Phone | None = None
    email: EmailAddress | None = None


class TenantOut(ApiModel):
    id: uuid.UUID
    full_name: str
    phone: str
    email: str | None


class TenantWriteOut(BaseModel):
    tenant: TenantOut
    warnings: list[ApiWarning] = []


# -- leases ------------------------------------------------------------------------------------


class LeaseCreate(BaseModel):
    unit_id: uuid.UUID
    # An existing tenant, or the data of a new one (exactly one of the two).
    tenant_id: uuid.UUID | None = None
    tenant: TenantIn | None = None
    rent_amount: PositiveMoney
    due_day: int = Field(ge=1, le=31)
    deposit_amount: NonNegativeMoney = Decimal("0.00")
    # No default on purpose: the interface must ask "¿El contrato pacta mora?".
    late_fee_agreed: bool
    start_date: dt.date
    end_date: dt.date | None = None
    rule_set_id: uuid.UUID | None = None
    # The owner confirms the contract has the WhatsApp consent clause (CLAUDE.md, rule 6).
    consent_confirmed: bool
    consent_evidence: str | None = Field(default=None, max_length=2000)

    @field_validator("consent_confirmed")
    @classmethod
    def _consent_required(cls, value: bool) -> bool:
        if not value:
            raise ValueError(
                "Para crear el contrato, confirma que el inquilino aceptó recibir mensajes "
                "por WhatsApp."
            )
        return value

    @model_validator(mode="after")
    def _check(self) -> "LeaseCreate":
        if (self.tenant_id is None) == (self.tenant is None):
            raise ValueError("Indica un inquilino existente o los datos de uno nuevo, no ambos.")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("La fecha de fin no puede ser antes del inicio.")
        return self


class LeaseUpdate(BaseModel):
    rent_amount: PositiveMoney | None = None
    due_day: int | None = Field(default=None, ge=1, le=31)
    deposit_amount: NonNegativeMoney | None = None
    late_fee_agreed: bool | None = None
    end_date: dt.date | None = None
    rule_set_id: uuid.UUID | None = None


class LeaseEnd(BaseModel):
    end_date: dt.date


class LeaseOut(ApiModel):
    id: uuid.UUID
    unit_id: uuid.UUID
    tenant_id: uuid.UUID
    rule_set_id: uuid.UUID | None
    rent_amount: Decimal
    due_day: int
    deposit_amount: Decimal
    late_fee_agreed: bool
    start_date: dt.date
    end_date: dt.date | None
    is_active: bool


class LeaseSummary(LeaseOut):
    tenant_name: str
    tenant_phone: str
    unit_code: str
    building_id: uuid.UUID | None
    building_name: str | None


class LeaseWriteOut(BaseModel):
    lease: LeaseOut
    warnings: list[ApiWarning] = []


class StatusOut(BaseModel):
    state: TenantState
    amount_due: Decimal
    days_late: int
    in_grace: bool


class PeriodBalanceOut(BaseModel):
    period: str
    charged: Decimal
    paid: Decimal
    outstanding: Decimal


class BalanceOut(BaseModel):
    total: Decimal
    credit_in_favour: Decimal
    by_period: list[PeriodBalanceOut]


class PunctualityMonth(BaseModel):
    period: str
    result: str


class PunctualityOut(BaseModel):
    months: list[PunctualityMonth]
    on_time_percent: int | None


class InboxItemOut(ApiModel):
    id: uuid.UUID
    lease_id: uuid.UUID | None
    payment_id: uuid.UUID | None
    payment_promise_id: uuid.UUID | None
    issue_id: uuid.UUID | None
    type: InboxItemType
    summary: str
    priority: int
    created_at: dt.datetime


class ConsentOut(ApiModel):
    method: ConsentMethod
    granted_at: dt.datetime
    revoked_at: dt.datetime | None


class PromiseOut(ApiModel):
    id: uuid.UUID
    promised_date: dt.date
    amount: Decimal | None
    created_by: PromiseCreator


class TimelineEventOut(ApiModel):
    id: uuid.UUID
    event_type: str
    actor_type: ActorType
    occurred_at: dt.datetime
    payload: dict[str, Any]


class TimelinePage(BaseModel):
    items: list[TimelineEventOut]
    next_cursor: str | None


class LeaseDetail(BaseModel):
    lease: LeaseOut
    tenant: TenantOut
    unit: UnitOut
    building: BuildingOut | None
    status: StatusOut
    balance: BalanceOut
    punctuality: PunctualityOut
    pending_action: InboxItemOut | None
    consent: ConsentOut | None
    active_promise: PromiseOut | None
    next_due_on: dt.date | None
    timeline: TimelinePage
