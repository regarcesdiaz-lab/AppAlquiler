"""Sign up, sign in (password or magic link), refresh and sign out."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAuth, DbSession
from app.api.errors import ErrorResponse, api_error, too_many_attempts, unauthenticated
from app.api.v1.presenters import organization_out, user_out
from app.channels.email import EmailSender, get_email_sender
from app.core.rate_limit import (
    LOGIN_FAILURES_PER_EMAIL,
    LOGIN_PER_IP,
    MAGIC_LINK_PER_EMAIL,
    MAGIC_LINK_PER_IP,
    REFRESH_PER_IP,
    REGISTER_PER_IP,
    RateLimiter,
    RateLimitExceededError,
    get_rate_limiter,
)
from app.core.security import (
    csrf_token_for,
    hash_password,
    password_needs_rehash,
    password_problem,
    verify_password,
)
from app.core.timezone import utc_now
from app.models import Organization, User
from app.models.enums import AuthMethod, ClientType, UserRole
from app.schemas.account import (
    AuthOut,
    LoginIn,
    MagicLinkIn,
    MagicLinkVerifyIn,
    MessageOut,
    RefreshIn,
    RegisterIn,
    SessionOut,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

Limiter = Annotated[RateLimiter, Depends(get_rate_limiter)]
Mailer = Annotated[EmailSender, Depends(get_email_sender)]

AUTH_ERRORS: dict[int | str, dict] = {
    401: {"model": ErrorResponse},
    429: {"model": ErrorResponse, "description": "Too many attempts"},
}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _guard(limiter: RateLimiter, *checks: tuple) -> None:
    try:
        for kind, limit, identity in checks:
            (limiter.hit if kind == "hit" else limiter.check)(limit, identity)
    except RateLimitExceededError as exc:
        raise too_many_attempts(exc.retry_after) from None


def _signed_in(
    db: DbSession,
    response: Response,
    request: Request,
    user: User,
    client: ClientType,
    method: AuthMethod,
    device_label: str | None,
) -> AuthOut:
    now = utc_now()
    organization = db.get(Organization, user.organization_id)
    out = AuthOut(user=user_out(user), organization=organization_out(organization), client=client)
    if client == ClientType.APP:
        tokens = auth_service.start_app_session(db, user, method, now, device_label)
        out.access_token = tokens.access_token
        out.refresh_token = tokens.refresh_token
        out.token_type = "bearer"
        out.expires_in = tokens.expires_in
    else:
        token, _ = auth_service.start_web_session(
            db, user, method, now, request.headers.get("user-agent")
        )
        auth_service.set_session_cookie(response, token)
        out.csrf_token = csrf_token_for(token)
    db.commit()
    return out


@router.post(
    "/register",
    response_model=AuthOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="register",
    responses={409: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
def register(
    data: RegisterIn, request: Request, response: Response, db: DbSession, limiter: Limiter
) -> AuthOut:
    _guard(limiter, ("hit", REGISTER_PER_IP, _client_ip(request)))
    if problem := password_problem(data.password, data.email):
        raise api_error(422, "weak_password", problem)
    if db.scalar(select(User.id).where(User.email == data.email)) is not None:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "email_taken",
            "Ya existe una cuenta con ese correo. Entra o pide un enlace a tu correo.",
        )
    now = utc_now()
    organization = Organization(name=data.organization_name)
    db.add(organization)
    db.flush()
    user = User(
        organization_id=organization.id,
        email=data.email,
        full_name=data.full_name,
        role=UserRole.OWNER,
        password_hash=hash_password(data.password),
        terms_accepted_at=now,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise api_error(
            status.HTTP_409_CONFLICT, "email_taken", "Ya existe una cuenta con ese correo."
        ) from None
    return _signed_in(
        db, response, request, user, data.client, AuthMethod.PASSWORD, data.device_label
    )


@router.post("/login", response_model=AuthOut, operation_id="login", responses=AUTH_ERRORS)
def login(
    data: LoginIn, request: Request, response: Response, db: DbSession, limiter: Limiter
) -> AuthOut:
    ip = _client_ip(request)
    _guard(
        limiter,
        ("check", LOGIN_FAILURES_PER_EMAIL, data.email),
        ("hit", LOGIN_PER_IP, ip),
    )
    user = db.scalar(select(User).where(User.email == data.email))
    # verify_password costs the same when the user doesn't exist (no account enumeration).
    if not verify_password(user.password_hash if user else None, data.password) or user is None:
        _guard(limiter, ("hit", LOGIN_FAILURES_PER_EMAIL, data.email))
        raise unauthenticated("Correo o contraseña incorrectos.")
    limiter.reset(LOGIN_FAILURES_PER_EMAIL, data.email)
    if user.password_hash and password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(data.password)
    return _signed_in(
        db, response, request, user, data.client, AuthMethod.PASSWORD, data.device_label
    )


@router.post(
    "/refresh", response_model=AuthOut, operation_id="refreshTokens", responses=AUTH_ERRORS
)
def refresh(data: RefreshIn, request: Request, db: DbSession, limiter: Limiter) -> AuthOut:
    _guard(limiter, ("hit", REFRESH_PER_IP, _client_ip(request)))
    try:
        user, tokens = auth_service.rotate_refresh_token(db, data.refresh_token, utc_now())
    except auth_service.InvalidRefreshTokenError:
        db.commit()  # keep a family revocation caused by token reuse
        raise unauthenticated("Tu sesión venció. Vuelve a entrar.") from None
    db.commit()
    return AuthOut(
        user=user_out(user),
        organization=organization_out(db.get(Organization, user.organization_id)),
        client=ClientType.APP,
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        token_type="bearer",
        expires_in=tokens.expires_in,
    )


@router.post(
    "/magic-link",
    response_model=MessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="requestMagicLink",
    responses={429: {"model": ErrorResponse}},
)
def request_magic_link(
    data: MagicLinkIn, request: Request, db: DbSession, limiter: Limiter, mailer: Mailer
) -> MessageOut:
    _guard(
        limiter,
        ("hit", MAGIC_LINK_PER_IP, _client_ip(request)),
        ("hit", MAGIC_LINK_PER_EMAIL, data.email),
    )
    user = db.scalar(select(User).where(User.email == data.email))
    if user is not None:
        token = auth_service.create_magic_link(db, user, data.client, utc_now())
        db.commit()
        mailer.send(auth_service.magic_link_email(user, token))
    # Same answer whether the account exists or not.
    return MessageOut(
        message=(
            "Si el correo tiene una cuenta, te enviamos un enlace para entrar. Revisa tu correo."
        )
    )


@router.post(
    "/magic-link/verify",
    response_model=AuthOut,
    operation_id="verifyMagicLink",
    responses=AUTH_ERRORS,
)
def verify_magic_link(
    data: MagicLinkVerifyIn, request: Request, response: Response, db: DbSession
) -> AuthOut:
    user = auth_service.consume_magic_link(db, data.token, utc_now())
    if user is None:
        db.rollback()
        raise unauthenticated("El enlace venció o ya se usó. Pide uno nuevo.")
    return _signed_in(
        db, response, request, user, data.client, AuthMethod.MAGIC_LINK, data.device_label
    )


@router.get("/session", response_model=SessionOut, operation_id="getSession", responses=AUTH_ERRORS)
def get_session(auth: CurrentAuth, request: Request, db: DbSession) -> SessionOut:
    csrf = None
    if auth.via == "cookie":
        csrf = csrf_token_for(request.cookies[auth_service.session_cookie_name()])
    return SessionOut(
        user=user_out(auth.user),
        organization=organization_out(db.get(Organization, auth.org_id)),
        csrf_token=csrf,
    )


@router.post(
    "/logout", status_code=status.HTTP_204_NO_CONTENT, operation_id="logout", responses=AUTH_ERRORS
)
def logout(auth: CurrentAuth, db: DbSession) -> Response:
    now = utc_now()
    if auth.session is not None:
        auth.session.revoked_at = now
    if auth.family_id is not None:
        auth_service.revoke_family(db, auth.family_id, now)
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    auth_service.clear_session_cookie(response)
    return response


@router.post(
    "/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="logoutAll",
    responses=AUTH_ERRORS,
)
def logout_all(auth: CurrentAuth, db: DbSession) -> Response:
    auth_service.revoke_all_sessions(db, auth.user, utc_now())
    db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    auth_service.clear_session_cookie(response)
    return response
