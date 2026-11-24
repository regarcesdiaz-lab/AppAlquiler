"""Password hashing, access tokens (JWT), opaque tokens and CSRF tokens.

* Passwords: Argon2id (RFC 9106 low-memory profile: t=3, m=64 MiB, p=4). Tests use a cheap
  profile so the suite stays fast; the algorithm is the same.
* Access tokens: JWT HS256 signed with SECRET_KEY, short lived (15 minutes by default).
* Opaque tokens (refresh, web session, magic link): 256 random bits; only SHA-256 is stored.
* CSRF: HMAC-SHA256(SECRET_KEY, session token), compared in constant time.
"""

import datetime as dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from functools import lru_cache

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TYPE = "access"
PASSWORD_MIN_LENGTH = 10
PASSWORD_MAX_LENGTH = 128


@lru_cache
def _hasher() -> PasswordHasher:
    if get_settings().APP_ENV == "test":
        return PasswordHasher(time_cost=1, memory_cost=8 * 1024, parallelism=1)
    return PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4)


@lru_cache
def _dummy_hash() -> str:
    # Verified when the email is unknown, so both paths cost the same (no user enumeration).
    return _hasher().hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-cost check; a missing hash still runs a full verification and fails."""
    try:
        return _hasher().verify(password_hash or _dummy_hash(), password) and bool(password_hash)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher().check_needs_rehash(password_hash)


def password_problem(password: str, email: str | None = None) -> str | None:
    """Spanish message when a password is not acceptable (NIST 800-63B style rules)."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"La contraseña debe tener al menos {PASSWORD_MIN_LENGTH} caracteres."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"La contraseña no puede tener más de {PASSWORD_MAX_LENGTH} caracteres."
    if email and email.split("@")[0].lower() in password.lower() and len(email.split("@")[0]) > 2:
        return "La contraseña no puede contener tu correo."
    return None


# -- opaque tokens -----------------------------------------------------------------------------


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# -- access tokens -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    family_id: uuid.UUID
    issued_at: dt.datetime


class InvalidTokenError(Exception):
    pass


def create_access_token(
    *, user_id: uuid.UUID, organization_id: uuid.UUID, family_id: uuid.UUID, now: dt.datetime
) -> tuple[str, int]:
    """Returns the token and its lifetime in seconds."""
    ttl = get_settings().ACCESS_TOKEN_TTL_MINUTES * 60
    payload = {
        "sub": str(user_id),
        "org": str(organization_id),
        "sid": str(family_id),
        "typ": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(now.timestamp()) + ttl,
    }
    return jwt.encode(payload, get_settings().signing_key, algorithm=JWT_ALGORITHM), ttl


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            get_settings().signing_key,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "org", "sid", "typ", "iat", "exp"]},
        )
        if payload["typ"] != ACCESS_TOKEN_TYPE:
            raise InvalidTokenError("wrong token type")
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            family_id=uuid.UUID(payload["sid"]),
            issued_at=dt.datetime.fromtimestamp(payload["iat"], tz=dt.UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError(str(exc)) from exc


# -- CSRF --------------------------------------------------------------------------------------


def csrf_token_for(session_token: str) -> str:
    key = get_settings().signing_key.encode()
    return hmac.new(key, f"csrf:{session_token}".encode(), hashlib.sha256).hexdigest()


def csrf_token_matches(session_token: str, provided: str | None) -> bool:
    if not provided:
        return False
    return hmac.compare_digest(csrf_token_for(session_token), provided)
