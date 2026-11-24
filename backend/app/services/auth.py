"""Sign-in sessions: refresh tokens for the app, cookie sessions for the web panel, magic links."""

import datetime as dt
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.channels.email import Email
from app.core.config import get_settings
from app.core.security import create_access_token, hash_token, new_opaque_token
from app.models import MagicLinkToken, RefreshToken, User, UserSession
from app.models.enums import AuthMethod, ClientType

SECURE_COOKIE_NAME = "__Host-session"
PLAIN_COOKIE_NAME = "session"


def session_cookie_name() -> str:
    # The __Host- prefix requires Secure (HTTPS), so local http development uses a plain name.
    return SECURE_COOKIE_NAME if get_settings().cookie_secure else PLAIN_COOKIE_NAME


# -- mobile app: access + refresh tokens -------------------------------------------------------


@dataclass(frozen=True)
class AppTokens:
    access_token: str
    refresh_token: str
    expires_in: int


def _new_refresh(
    db: Session,
    user: User,
    *,
    family_id: uuid.UUID,
    auth_method: AuthMethod,
    authenticated_at: dt.datetime,
    device_label: str | None,
    now: dt.datetime,
) -> AppTokens:
    token = new_opaque_token()
    db.add(
        RefreshToken(
            organization_id=user.organization_id,
            user_id=user.id,
            token_hash=hash_token(token),
            family_id=family_id,
            expires_at=now + dt.timedelta(days=get_settings().REFRESH_TOKEN_TTL_DAYS),
            auth_method=auth_method,
            authenticated_at=authenticated_at,
            device_label=device_label,
            created_at=now,
        )
    )
    db.flush()
    access, ttl = create_access_token(
        user_id=user.id, organization_id=user.organization_id, family_id=family_id, now=now
    )
    return AppTokens(access, token, ttl)


def start_app_session(
    db: Session,
    user: User,
    auth_method: AuthMethod,
    now: dt.datetime,
    device_label: str | None = None,
) -> AppTokens:
    return _new_refresh(
        db,
        user,
        family_id=uuid.uuid4(),
        auth_method=auth_method,
        authenticated_at=now,
        device_label=device_label,
        now=now,
    )


class InvalidRefreshTokenError(Exception):
    pass


def rotate_refresh_token(db: Session, token: str, now: dt.datetime) -> tuple[User, AppTokens]:
    """Use a refresh token once. Reusing an already used token revokes its whole family."""
    current = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(token)).with_for_update()
    )
    if current is None:
        raise InvalidRefreshTokenError("unknown")
    if current.revoked_at is not None:
        raise InvalidRefreshTokenError("revoked")
    if current.used_at is not None:
        # A token that was already exchanged comes back: assume it was stolen.
        revoke_family(db, current.family_id, now)
        raise InvalidRefreshTokenError("reused")
    if current.expires_at <= now:
        raise InvalidRefreshTokenError("expired")
    user = db.get(User, current.user_id)
    if user is None:
        raise InvalidRefreshTokenError("no user")
    current.used_at = now
    tokens = _new_refresh(
        db,
        user,
        family_id=current.family_id,
        auth_method=current.auth_method,
        authenticated_at=current.authenticated_at,
        device_label=current.device_label,
        now=now,
    )
    return user, tokens


def revoke_family(db: Session, family_id: uuid.UUID, now: dt.datetime) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


# -- web panel: cookie sessions ----------------------------------------------------------------


def start_web_session(
    db: Session,
    user: User,
    auth_method: AuthMethod,
    now: dt.datetime,
    user_agent: str | None = None,
) -> tuple[str, UserSession]:
    token = new_opaque_token()
    session = UserSession(
        organization_id=user.organization_id,
        user_id=user.id,
        token_hash=hash_token(token),
        user_agent=(user_agent or "")[:512] or None,
        expires_at=now + dt.timedelta(days=get_settings().SESSION_ABSOLUTE_DAYS),
        last_seen_at=now,
        auth_method=auth_method,
        authenticated_at=now,
        created_at=now,
    )
    db.add(session)
    db.flush()
    return token, session


def set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        session_cookie_name(),
        token,
        max_age=settings.SESSION_ABSOLUTE_DAYS * 24 * 60 * 60,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        session_cookie_name(),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


# -- sign out everywhere -----------------------------------------------------------------------


def revoke_all_sessions(db: Session, user: User, now: dt.datetime) -> None:
    db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    user.tokens_valid_after = now
    db.flush()


# -- magic links -------------------------------------------------------------------------------


def create_magic_link(db: Session, user: User, client: ClientType, now: dt.datetime) -> str:
    """New single-use link; any earlier unused link of the user stops working."""
    db.execute(
        delete(MagicLinkToken).where(
            MagicLinkToken.user_id == user.id, MagicLinkToken.used_at.is_(None)
        )
    )
    token = new_opaque_token()
    db.add(
        MagicLinkToken(
            organization_id=user.organization_id,
            user_id=user.id,
            token_hash=hash_token(token),
            client=client,
            expires_at=now + dt.timedelta(minutes=get_settings().MAGIC_LINK_TTL_MINUTES),
            created_at=now,
        )
    )
    db.flush()
    return token


def consume_magic_link(db: Session, token: str, now: dt.datetime) -> User | None:
    link = db.scalar(
        select(MagicLinkToken)
        .where(MagicLinkToken.token_hash == hash_token(token))
        .with_for_update()
    )
    if link is None or link.used_at is not None or link.expires_at <= now:
        return None
    link.used_at = now
    db.flush()
    return db.get(User, link.user_id)


def magic_link_email(user: User, token: str) -> Email:
    settings = get_settings()
    query = urlencode({"token": token})
    web_link = f"{settings.PANEL_URL.rstrip('/')}/auth/magic?{query}"
    lines = [
        f"Hola, {user.full_name.split()[0] if user.full_name.split() else user.full_name}.",
        "",
        "Toca este enlace para entrar a tu cuenta:",
        web_link,
    ]
    if not settings.is_production:
        # Expo Go cannot use universal links: development also gets the custom scheme.
        lines += ["", "En la app de desarrollo:", f"{settings.APP_SCHEME}://auth/magic?{query}"]
    lines += [
        "",
        f"El enlace vence en {settings.MAGIC_LINK_TTL_MINUTES} minutos y sirve una sola vez.",
        "Si no lo pediste, ignora este correo.",
    ]
    return Email(to=user.email, subject="Tu enlace para entrar", text="\n".join(lines))
