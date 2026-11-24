"""The owner's account: organization, users, sign-in credentials and push devices."""

import datetime as dt
import uuid

from sqlalchemy import CheckConstraint, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.models.base import (
    CreatedAtMixin,
    IdMixin,
    OrgScopedMixin,
    TimestampMixin,
    org_table_args,
    scoped_fk,
    str_enum,
)
from app.models.enums import AuthMethod, ClientType, DevicePlatform, UserRole

E164_CHECK = r"~ '^\+[1-9][0-9]{7,14}$'"


class Organization(IdMixin, TimestampMixin, Base):
    """The owner's account. Everything else belongs to exactly one organization."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_iso"),
        CheckConstraint("next_receipt_number >= 1", name="next_receipt_number_positive"),
    )

    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(
        String(64), default="America/Santo_Domingo", server_default="America/Santo_Domingo"
    )
    currency: Mapped[str] = mapped_column(String(3), default="DOP", server_default="DOP")
    # Next receipt number; taken with SELECT ... FOR UPDATE when a receipt is issued.
    next_receipt_number: Mapped[int] = mapped_column(default=1, server_default=text("1"))


class User(OrgScopedMixin, TimestampMixin, Base):
    """A person who signs in to the app or the web panel (one per organization in v1)."""

    __tablename__ = "users"
    __table_args__ = org_table_args(
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint(f"whatsapp_phone {E164_CHECK}", name="whatsapp_phone_e164"),
        # The owner assistant identifies the owner by WhatsApp number.
        Index(
            "uq_users_whatsapp_phone",
            "whatsapp_phone",
            unique=True,
            postgresql_where=text("whatsapp_phone IS NOT NULL"),
        ),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200))
    whatsapp_phone: Mapped[str | None] = mapped_column(String(16))
    role: Mapped[UserRole] = mapped_column(str_enum(UserRole, "user_role"), default=UserRole.OWNER)
    # Argon2id hash. NULL for an account that only signs in with magic links.
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # "Sign out everywhere": access tokens issued before this moment are rejected.
    tokens_valid_after: Mapped[dt.datetime | None]
    terms_accepted_at: Mapped[dt.datetime | None]


class UserSession(OrgScopedMixin, CreatedAtMixin, Base):
    """Web panel session (httpOnly cookie). Only a hash of the token is stored."""

    __tablename__ = "user_sessions"
    __table_args__ = org_table_args(
        scoped_fk("user_id", "users", ondelete="CASCADE"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index(
            "ix_user_sessions_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")
        ),
    )

    user_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    device_label: Mapped[str | None] = mapped_column(String(120))
    # Absolute expiry; the idle timeout is measured from last_seen_at.
    expires_at: Mapped[dt.datetime]
    revoked_at: Mapped[dt.datetime | None]
    last_seen_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    auth_method: Mapped[AuthMethod] = mapped_column(
        str_enum(AuthMethod, "auth_method"),
        default=AuthMethod.PASSWORD,
        server_default=AuthMethod.PASSWORD.value,
    )
    # When the user proved who they are (password or magic link); used for "recent sign-in".
    authenticated_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class RefreshToken(OrgScopedMixin, CreatedAtMixin, Base):
    """Mobile app refresh token. Rotated on every use; reusing an old one revokes the family."""

    __tablename__ = "refresh_tokens"
    __table_args__ = org_table_args(
        scoped_fk("user_id", "users", ondelete="CASCADE"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index(
            "ix_refresh_tokens_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")
        ),
    )

    user_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    # Every token of one sign-in shares the family; access tokens carry it as "sid".
    family_id: Mapped[uuid.UUID]
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[dt.datetime | None]
    revoked_at: Mapped[dt.datetime | None]
    auth_method: Mapped[AuthMethod] = mapped_column(str_enum(AuthMethod, "auth_method"))
    authenticated_at: Mapped[dt.datetime]
    device_label: Mapped[str | None] = mapped_column(String(120))


class MagicLinkToken(OrgScopedMixin, CreatedAtMixin, Base):
    """Single-use sign-in link sent by email. Only a hash of the token is stored."""

    __tablename__ = "magic_link_tokens"
    __table_args__ = org_table_args(
        scoped_fk("user_id", "users", ondelete="CASCADE"),
        CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        Index("ix_magic_link_tokens_user_id", "user_id"),
    )

    user_id: Mapped[uuid.UUID]
    token_hash: Mapped[str] = mapped_column(String(128), unique=True)
    client: Mapped[ClientType] = mapped_column(str_enum(ClientType, "client_type"))
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[dt.datetime | None]


class DeviceToken(OrgScopedMixin, CreatedAtMixin, Base):
    """Expo push token of one of the user's phones or tablets."""

    __tablename__ = "device_tokens"
    __table_args__ = org_table_args(scoped_fk("user_id", "users", ondelete="CASCADE"))

    user_id: Mapped[uuid.UUID] = mapped_column(index=True)
    expo_push_token: Mapped[str] = mapped_column(String(255), unique=True)
    platform: Mapped[DevicePlatform] = mapped_column(str_enum(DevicePlatform, "device_platform"))
    last_used_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))


class AccountDeletionRequest(IdMixin, Base):
    """Proof that an account deletion was requested and carried out.

    Deliberately NOT linked to organizations: it must survive the purge of the organization.
    It keeps no personal data, only the organization id and a SHA-256 of the email.
    """

    __tablename__ = "account_deletion_requests"
    __table_args__ = (Index("ix_account_deletion_requests_organization_id", "organization_id"),)

    organization_id: Mapped[uuid.UUID]
    email_sha256: Mapped[str] = mapped_column(String(64))
    client: Mapped[ClientType] = mapped_column(str_enum(ClientType, "client_type"))
    requested_at: Mapped[dt.datetime] = mapped_column(server_default=text("now()"))
    completed_at: Mapped[dt.datetime | None]
