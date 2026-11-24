"""Shared schema pieces: money, warnings, phone and email fields."""

from decimal import Decimal
from typing import Annotated

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.domain.phones import InvalidPhoneError, normalize_phone

# Money: Decimal(12, 2), serialized as a string in JSON (never float, CLAUDE.md rule 1).
Money = Annotated[Decimal, Field(max_digits=12, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


def _phone(value: str) -> str:
    try:
        return normalize_phone(value)
    except InvalidPhoneError:
        raise ValueError(
            "Escribe un teléfono válido, por ejemplo 809-555-0101 o +1 809 555 0101."
        ) from None


def _email(value: str) -> str:
    try:
        return validate_email(value.strip(), check_deliverability=False).normalized.lower()
    except EmailNotValidError:
        raise ValueError("Escribe un correo válido.") from None


Phone = Annotated[str, Field(min_length=7, max_length=32), AfterValidator(_phone)]
EmailAddress = Annotated[str, Field(min_length=3, max_length=320), AfterValidator(_email)]
Name = Annotated[str, Field(min_length=1, max_length=200)]


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ApiWarning(BaseModel):
    """Something the owner should review; never blocks the request."""

    code: str
    message: str
    field: str | None = None
