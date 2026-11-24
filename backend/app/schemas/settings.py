"""Collection rules, assistant persona and bank accounts."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from app.domain.messaging_rules import reminder_offsets_within_limits
from app.domain.rule_templates import (
    PLATFORM_LATE_FEE_CAP_PERCENT,
    LateFeeFrequency,
    LateFeeType,
    RuleTemplate,
)
from app.models.enums import AccountType, AssistantTone
from app.schemas.common import ApiModel

ContactDay = Annotated[int, Field(ge=1, le=6)]  # ISO weekday; Sunday (7) is never allowed


class ReminderStepModel(BaseModel):
    offset_days: int = Field(ge=-15, le=60)
    template: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_]+$")


class RuleSetValues(BaseModel):
    """Every field of a rule set, validated together (the same rules as the table CHECKs)."""

    name: str = Field(min_length=1, max_length=80)
    template: RuleTemplate
    grace_days: int = Field(ge=0, le=31)
    late_fee_type: LateFeeType
    late_fee_value: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    late_fee_frequency: LateFeeFrequency
    late_fee_cap_percent: Decimal | None = Field(
        default=None, gt=0, le=PLATFORM_LATE_FEE_CAP_PERCENT, max_digits=12, decimal_places=2
    )
    escalation_days: int = Field(ge=0, le=90)
    max_reminders_per_day: int = Field(ge=1, le=10)
    max_reminders_per_week: int = Field(ge=1, le=30)
    max_extension_days: int = Field(ge=0, le=90)
    max_extensions_per_year: int = Field(ge=0, le=12)
    contact_start: dt.time
    contact_end: dt.time
    contact_days: list[ContactDay] = Field(min_length=1, max_length=6)
    reminder_sequence: list[ReminderStepModel] = Field(max_length=10)

    @model_validator(mode="after")
    def _consistent(self) -> "RuleSetValues":
        if self.late_fee_type == LateFeeType.NONE and self.late_fee_value != 0:
            raise ValueError("Sin mora, el valor de la mora debe ser 0.")
        if self.late_fee_type != LateFeeType.NONE and self.late_fee_value <= 0:
            raise ValueError("La mora necesita un valor mayor que 0.")
        if self.late_fee_type == LateFeeType.PERCENT and self.late_fee_value > 100:
            raise ValueError("El porcentaje de mora no puede pasar de 100.")
        if self.contact_start >= self.contact_end:
            raise ValueError("La hora de inicio debe ser antes de la hora de fin.")
        if self.max_reminders_per_week < self.max_reminders_per_day:
            raise ValueError("El límite semanal no puede ser menor que el diario.")
        if len(set(self.contact_days)) != len(self.contact_days):
            raise ValueError("Hay días de contacto repetidos.")
        offsets = [s.offset_days for s in self.reminder_sequence]
        if not reminder_offsets_within_limits(
            offsets, self.max_reminders_per_day, self.max_reminders_per_week
        ):
            raise ValueError("La secuencia de recordatorios pasa los límites por día o por semana.")
        return self


class RuleSetOut(RuleSetValues):
    id: uuid.UUID
    is_default: bool


class RuleSetUpdate(BaseModel):
    """``template`` resets every value to that preset first; other fields then override it."""

    template: RuleTemplate | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    grace_days: int | None = None
    late_fee_type: LateFeeType | None = None
    late_fee_value: Decimal | None = None
    late_fee_frequency: LateFeeFrequency | None = None
    late_fee_cap_percent: Decimal | None = None
    escalation_days: int | None = None
    max_reminders_per_day: int | None = None
    max_reminders_per_week: int | None = None
    max_extension_days: int | None = None
    max_extensions_per_year: int | None = None
    contact_start: dt.time | None = None
    contact_end: dt.time | None = None
    contact_days: list[int] | None = None
    reminder_sequence: list[ReminderStepModel] | None = None


class AssistantIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=60)
    tone: AssistantTone


class AssistantOut(ApiModel):
    display_name: str
    tone: AssistantTone


class BankAccountIn(BaseModel):
    bank_name: str = Field(min_length=1, max_length=100)
    account_type: AccountType
    account_number: str = Field(min_length=4, max_length=34, pattern=r"^[0-9A-Za-z\- ]+$")
    holder_name: str = Field(min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=60)


class BankAccountUpdate(BaseModel):
    bank_name: str | None = Field(default=None, min_length=1, max_length=100)
    account_type: AccountType | None = None
    account_number: str | None = Field(
        default=None, min_length=4, max_length=34, pattern=r"^[0-9A-Za-z\- ]+$"
    )
    holder_name: str | None = Field(default=None, min_length=1, max_length=200)
    alias: str | None = Field(default=None, max_length=60)
    is_active: bool | None = None


class BankAccountOut(ApiModel):
    id: uuid.UUID
    bank_name: str
    account_type: AccountType
    account_number: str
    holder_name: str
    alias: str | None
    is_active: bool
