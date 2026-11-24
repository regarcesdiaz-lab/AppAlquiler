"""Auth, account, devices and organization."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.models.enums import ClientType, DevicePlatform, UserRole
from app.schemas.common import ApiModel, EmailAddress, Name, Phone

EXPO_TOKEN_PATTERN = r"^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_\-]+\]$"


class UserOut(ApiModel):
    id: uuid.UUID
    email: str
    full_name: str
    whatsapp_phone: str | None
    role: UserRole
    has_password: bool


class OrganizationOut(ApiModel):
    id: uuid.UUID
    name: str
    timezone: str
    currency: str


class RegisterIn(BaseModel):
    email: EmailAddress
    password: str = Field(min_length=1, max_length=256)
    full_name: Name
    organization_name: Name
    accepted_terms: bool
    client: ClientType
    device_label: str | None = Field(default=None, max_length=120)

    @field_validator("accepted_terms")
    @classmethod
    def _must_accept(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Debes aceptar los términos y la política de privacidad.")
        return value


class LoginIn(BaseModel):
    email: EmailAddress
    password: str = Field(min_length=1, max_length=256)
    client: ClientType
    device_label: str | None = Field(default=None, max_length=120)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class MagicLinkIn(BaseModel):
    email: EmailAddress
    client: ClientType


class MagicLinkVerifyIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    client: ClientType
    device_label: str | None = Field(default=None, max_length=120)


class AuthOut(BaseModel):
    """App: access + refresh tokens. Web: the session goes in an httpOnly cookie and the CSRF
    token comes here (keep it in memory, never in localStorage)."""

    user: UserOut
    organization: OrganizationOut
    client: ClientType
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: Literal["bearer"] | None = None
    expires_in: int | None = None
    csrf_token: str | None = None


class SessionOut(BaseModel):
    user: UserOut
    organization: OrganizationOut
    csrf_token: str | None


class MessageOut(BaseModel):
    message: str


class MeUpdate(BaseModel):
    full_name: Name | None = None
    whatsapp_phone: Phone | None = None


class PasswordChangeIn(BaseModel):
    current_password: str | None = Field(default=None, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AccountDeleteIn(BaseModel):
    password: str | None = Field(default=None, max_length=256)
    confirm: Literal["ELIMINAR"]


class AccountDeletedOut(BaseModel):
    deleted: Literal[True]
    request_id: uuid.UUID
    completed_at: dt.datetime


class OrganizationUpdate(BaseModel):
    name: Name


class DeviceIn(BaseModel):
    expo_push_token: str = Field(pattern=EXPO_TOKEN_PATTERN, max_length=255)
    platform: DevicePlatform


class DeviceOut(ApiModel):
    id: uuid.UUID
    expo_push_token: str
    platform: DevicePlatform
    last_used_at: dt.datetime
