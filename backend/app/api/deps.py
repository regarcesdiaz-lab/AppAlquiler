"""Request dependencies: database session, the signed-in user and organization scoping.

Two ways to sign in, never both on the same request:

* ``Authorization: Bearer <access JWT>`` (mobile app). No cookies, so no CSRF.
* Session cookie (web panel). Every unsafe method (POST/PUT/PATCH/DELETE) must carry the
  ``X-CSRF-Token`` header and, when the browser sends ``Origin``, come from an allowed origin.
"""

import base64
import datetime as dt
import json
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from fastapi import Depends, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import api_error, not_found, unauthenticated
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import (
    InvalidTokenError,
    csrf_token_matches,
    decode_access_token,
    hash_token,
    verify_password,
)
from app.core.timezone import utc_now
from app.models import RefreshToken, User, UserSession
from app.models.enums import AuthMethod
from app.services.auth import session_cookie_name

DbSession = Annotated[Session, Depends(get_db)]

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LAST_SEEN_RESOLUTION = dt.timedelta(hours=1)
RECENT_AUTH_WINDOW = dt.timedelta(minutes=15)


@dataclass
class AuthContext:
    user: User
    via: Literal["bearer", "cookie"]
    auth_method: AuthMethod
    authenticated_at: dt.datetime
    session: UserSession | None = None
    family_id: uuid.UUID | None = None

    @property
    def org_id(self) -> uuid.UUID:
        return self.user.organization_id


def _from_bearer(db: Session, header: str) -> AuthContext:
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthenticated()
    try:
        claims = decode_access_token(token.strip())
    except InvalidTokenError:
        raise unauthenticated("Tu sesión venció. Vuelve a entrar.") from None
    user = db.get(User, claims.user_id)
    if user is None or user.organization_id != claims.organization_id:
        raise unauthenticated()
    if user.tokens_valid_after is not None and claims.issued_at < user.tokens_valid_after.replace(
        microsecond=0
    ):
        raise unauthenticated("Tu sesión se cerró. Vuelve a entrar.")
    latest = db.scalar(
        select(RefreshToken)
        .where(RefreshToken.family_id == claims.family_id, RefreshToken.user_id == user.id)
        .order_by(RefreshToken.created_at.desc())
        .limit(1)
    )
    if latest is None or latest.revoked_at is not None:
        raise unauthenticated("Tu sesión se cerró. Vuelve a entrar.")
    return AuthContext(
        user=user,
        via="bearer",
        auth_method=latest.auth_method,
        authenticated_at=latest.authenticated_at,
        family_id=claims.family_id,
    )


def _from_cookie(db: Session, request: Request, token: str) -> AuthContext:
    settings = get_settings()
    now = utc_now()
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if (
        session is None
        or session.revoked_at is not None
        or session.expires_at <= now
        or session.last_seen_at + dt.timedelta(days=settings.SESSION_IDLE_DAYS) <= now
    ):
        raise unauthenticated("Tu sesión venció. Vuelve a entrar.")
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin not in settings.CORS_ORIGINS:
            raise api_error(status.HTTP_403_FORBIDDEN, "origin_not_allowed", "Origen no permitido.")
        if not csrf_token_matches(token, request.headers.get("x-csrf-token")):
            raise api_error(
                status.HTTP_403_FORBIDDEN,
                "csrf_failed",
                "No pudimos verificar la solicitud. Recarga la página e inténtalo de nuevo.",
            )
    user = db.get(User, session.user_id)
    if user is None:
        raise unauthenticated()
    if now - session.last_seen_at > LAST_SEEN_RESOLUTION:
        session.last_seen_at = now
        db.commit()
    return AuthContext(
        user=user,
        via="cookie",
        auth_method=session.auth_method,
        authenticated_at=session.authenticated_at,
        session=session,
    )


def get_auth(request: Request, db: DbSession) -> AuthContext:
    authorization = request.headers.get("authorization")
    cookie_token = request.cookies.get(session_cookie_name())
    if authorization and cookie_token:
        raise api_error(
            status.HTTP_400_BAD_REQUEST,
            "ambiguous_credentials",
            "Usa el token o la cookie de sesión, no los dos.",
        )
    if authorization:
        return _from_bearer(db, authorization)
    if cookie_token:
        return _from_cookie(db, request, cookie_token)
    raise unauthenticated()


CurrentAuth = Annotated[AuthContext, Depends(get_auth)]


def require_recent_auth(auth: AuthContext, password: str | None) -> None:
    """Sensitive actions: the current password, or a sign-in from the last 15 minutes."""
    if password:
        if not verify_password(auth.user.password_hash, password):
            raise api_error(
                status.HTTP_403_FORBIDDEN, "wrong_password", "La contraseña no es correcta."
            )
        return
    if utc_now() - auth.authenticated_at <= RECENT_AUTH_WINDOW:
        return
    raise api_error(
        status.HTTP_403_FORBIDDEN,
        "reauthentication_required",
        "Por seguridad, escribe tu contraseña o vuelve a entrar con un enlace a tu correo.",
    )


def get_owned[T](
    db: Session, model: type[T], obj_id: uuid.UUID, org_id: uuid.UUID, *, for_update: bool = False
) -> T:
    """Load a row of the signed-in organization; anything else is a 404 (never a 403)."""
    stmt = select(model).where(model.id == obj_id, model.organization_id == org_id)  # type: ignore[attr-defined]
    if for_update:
        stmt = stmt.with_for_update()
    obj = db.scalar(stmt)
    if obj is None:
        raise not_found()
    return obj


# -- cursor pagination -------------------------------------------------------------------------


def encode_cursor(*values: Any) -> str:
    raw = json.dumps([v.isoformat() if hasattr(v, "isoformat") else str(v) for v in values])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, size: int) -> list[str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        values = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if not isinstance(values, list) or len(values) != size:
            raise ValueError
        return [str(v) for v in values]
    except ValueError:
        raise api_error(422, "invalid_cursor", "Cursor inválido.") from None
