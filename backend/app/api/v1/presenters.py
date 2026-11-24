"""Model → schema conversions shared by several routers."""

from app.models import Organization, User
from app.schemas.account import OrganizationOut, UserOut


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        whatsapp_phone=user.whatsapp_phone,
        role=user.role,
        has_password=user.password_hash is not None,
    )


def organization_out(organization: Organization | None) -> OrganizationOut:
    assert organization is not None
    return OrganizationOut.model_validate(organization)
