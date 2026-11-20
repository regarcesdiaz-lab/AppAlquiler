"""Security deposit check (Ley 85-25): a warning, never a block.

For housing leases the law allows a deposit of up to 2 months of rent. The app warns the owner
when the deposit is higher and lets them save it anyway; holding the deposit is out of v1.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.formatting import format_dop

MAX_HOUSING_DEPOSIT_MONTHS = Decimal(2)


@dataclass(frozen=True)
class DepositWarning:
    months: Decimal  # deposit / rent, rounded to 2 decimals
    max_allowed: Decimal
    message: str


def deposit_warning(
    deposit_amount: Decimal, rent_amount: Decimal, *, is_housing: bool = True
) -> DepositWarning | None:
    if rent_amount <= 0:
        raise ValueError("Rent must be positive")
    if not is_housing:
        return None
    max_allowed = rent_amount * MAX_HOUSING_DEPOSIT_MONTHS
    if deposit_amount <= max_allowed:
        return None
    months = (deposit_amount / rent_amount).quantize(Decimal("0.01"))
    months_text = f"{months.normalize():f}".replace(".", ",")
    return DepositWarning(
        months=months,
        max_allowed=max_allowed,
        message=(
            f"El depósito equivale a {months_text} meses de renta. La Ley 85-25 permite hasta "
            f"2 meses ({format_dop(max_allowed)}) en contratos de vivienda. "
            "Puedes guardarlo así, pero te recomendamos revisarlo."
        ),
    )
