"""The signed-in owner: profile, password, account deletion, organization, push devices and
onboarding progress."""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAuth, DbSession, require_recent_auth
from app.api.errors import COMMON_RESPONSES, ErrorResponse, api_error, conflict, not_found
from app.api.v1.presenters import organization_out, user_out
from app.core.security import hash_password, password_problem, sha256_hex
from app.core.timezone import utc_now
from app.models import (
    AccountDeletionRequest,
    AssistantProfile,
    BankAccount,
    DeviceToken,
    Organization,
    RefreshToken,
    RuleSet,
    Unit,
    UserSession,
)
from app.models.enums import ClientType
from app.schemas.account import (
    AccountDeletedOut,
    AccountDeleteIn,
    DeviceIn,
    DeviceOut,
    MeUpdate,
    OrganizationOut,
    OrganizationUpdate,
    PasswordChangeIn,
    UserOut,
)
from app.services import auth as auth_service
from app.services.account import purge_organization

router = APIRouter(tags=["account"], responses=COMMON_RESPONSES)


@router.get("/me", response_model=UserOut, operation_id="getMe")
def get_me(auth: CurrentAuth) -> UserOut:
    return user_out(auth.user)


@router.patch(
    "/me",
    response_model=UserOut,
    operation_id="updateMe",
    responses={409: {"model": ErrorResponse}},
)
def update_me(data: MeUpdate, auth: CurrentAuth, db: DbSession) -> UserOut:
    user = auth.user
    if data.full_name is not None:
        user.full_name = data.full_name
    if "whatsapp_phone" in data.model_fields_set:
        user.whatsapp_phone = data.whatsapp_phone
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise conflict("phone_taken", "Ese número de WhatsApp ya está en otra cuenta.") from None
    return user_out(user)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="changePassword",
    responses={403: {"model": ErrorResponse}},
)
def change_password(data: PasswordChangeIn, auth: CurrentAuth, db: DbSession) -> Response:
    require_recent_auth(auth, data.current_password)
    if problem := password_problem(data.new_password, auth.user.email):
        raise api_error(422, "weak_password", problem)
    now = utc_now()
    auth.user.password_hash = hash_password(data.new_password)
    # Every other session and app sign-in ends; the current one stays signed in.
    sessions = update(UserSession).where(
        UserSession.user_id == auth.user.id, UserSession.revoked_at.is_(None)
    )
    if auth.session is not None:
        sessions = sessions.where(UserSession.id != auth.session.id)
    db.execute(sessions.values(revoked_at=now))
    families = update(RefreshToken).where(
        RefreshToken.user_id == auth.user.id, RefreshToken.revoked_at.is_(None)
    )
    if auth.family_id is not None:
        families = families.where(RefreshToken.family_id != auth.family_id)
    db.execute(families.values(revoked_at=now))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/me/account",
    response_model=AccountDeletedOut,
    operation_id="deleteAccount",
    responses={403: {"model": ErrorResponse}},
)
def delete_account(data: AccountDeleteIn, auth: CurrentAuth, db: DbSession) -> Response:
    require_recent_auth(auth, data.password)
    request = AccountDeletionRequest(
        organization_id=auth.org_id,
        email_sha256=sha256_hex(auth.user.email),
        client=ClientType.WEB if auth.via == "cookie" else ClientType.APP,
        requested_at=utc_now(),
    )
    db.add(request)
    db.flush()
    # TODO(storage phase): delete the organization's files (receipts, documents, photos).
    purge_organization(db, auth.org_id)
    request.completed_at = utc_now()
    db.commit()
    body = AccountDeletedOut(deleted=True, request_id=request.id, completed_at=request.completed_at)
    response = Response(
        content=body.model_dump_json(), media_type="application/json", status_code=200
    )
    auth_service.clear_session_cookie(response)
    return response


@router.get("/organization", response_model=OrganizationOut, operation_id="getOrganization")
def get_organization(auth: CurrentAuth, db: DbSession) -> OrganizationOut:
    return organization_out(db.get(Organization, auth.org_id))


@router.patch("/organization", response_model=OrganizationOut, operation_id="updateOrganization")
def update_organization(
    data: OrganizationUpdate, auth: CurrentAuth, db: DbSession
) -> OrganizationOut:
    organization = db.get(Organization, auth.org_id)
    assert organization is not None
    organization.name = data.name
    db.commit()
    return organization_out(organization)


@router.post("/devices", response_model=DeviceOut, operation_id="registerDevice")
def register_device(data: DeviceIn, auth: CurrentAuth, db: DbSession) -> DeviceOut:
    device = db.scalar(
        select(DeviceToken).where(DeviceToken.expo_push_token == data.expo_push_token)
    )
    now = utc_now()
    if device is None:
        device = DeviceToken(
            organization_id=auth.org_id,
            user_id=auth.user.id,
            expo_push_token=data.expo_push_token,
            platform=data.platform,
            last_used_at=now,
        )
        db.add(device)
    else:
        # The same phone signed in with another account: the token follows the device.
        device.organization_id = auth.org_id
        device.user_id = auth.user.id
        device.platform = data.platform
        device.last_used_at = now
    db.commit()
    return DeviceOut.model_validate(device)


@router.delete(
    "/devices/{expo_push_token}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteDevice",
)
def delete_device(expo_push_token: str, auth: CurrentAuth, db: DbSession) -> Response:
    device = db.scalar(
        select(DeviceToken).where(
            DeviceToken.expo_push_token == expo_push_token,
            DeviceToken.organization_id == auth.org_id,
        )
    )
    if device is None:
        raise not_found()
    db.delete(device)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class OnboardingStep(BaseModel):
    key: str
    title: str
    done: bool


class OnboardingOut(BaseModel):
    completed: bool
    steps: list[OnboardingStep]


@router.get("/onboarding", response_model=OnboardingOut, operation_id="getOnboarding")
def get_onboarding(auth: CurrentAuth, db: DbSession) -> OnboardingOut:
    org_id = auth.org_id

    def exists(stmt) -> bool:
        return bool(db.scalar(select(func.count()).select_from(stmt.subquery())))

    steps = [
        OnboardingStep(key="organization", title="Tu organización", done=True),
        OnboardingStep(
            key="assistant",
            title="Nombre y tono del asistente",
            done=exists(
                select(AssistantProfile.id).where(AssistantProfile.organization_id == org_id)
            ),
        ),
        OnboardingStep(
            key="rules",
            title="Reglas de cobro",
            done=exists(
                select(RuleSet.id).where(RuleSet.organization_id == org_id, RuleSet.is_default)
            ),
        ),
        OnboardingStep(
            key="bank_accounts",
            title="Cuentas para recibir pagos",
            done=exists(
                select(BankAccount.id).where(
                    BankAccount.organization_id == org_id, BankAccount.is_active
                )
            ),
        ),
        OnboardingStep(
            key="first_property",
            title="Tu primer apartamento",
            done=exists(select(Unit.id).where(Unit.organization_id == org_id)),
        ),
    ]
    return OnboardingOut(completed=all(s.done for s in steps), steps=steps)
