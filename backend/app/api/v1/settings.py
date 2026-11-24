"""Ajustes: the default rule set, the assistant persona and bank accounts (also used by
onboarding)."""

import uuid
from typing import Any

from fastapi import APIRouter, Response, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAuth, DbSession, get_owned
from app.api.errors import COMMON_RESPONSES, ErrorResponse, conflict, not_found
from app.domain.rule_templates import PRESETS, RuleTemplate
from app.models import AssistantProfile, BankAccount, RuleSet
from app.schemas.settings import (
    AssistantIn,
    AssistantOut,
    BankAccountIn,
    BankAccountOut,
    BankAccountUpdate,
    RuleSetOut,
    RuleSetUpdate,
    RuleSetValues,
)
from app.services.timeline import record_event

router = APIRouter(prefix="/settings", tags=["settings"], responses=COMMON_RESPONSES)

TEMPLATE_NAMES = {
    RuleTemplate.FLEXIBLE: "Flexible",
    RuleTemplate.STANDARD: "Estándar",
    RuleTemplate.STRICT: "Estricta",
}
RULE_FIELDS = tuple(RuleSetValues.model_fields)


def _preset_values(template: RuleTemplate) -> dict[str, Any]:
    preset = PRESETS[template]
    return {
        "name": TEMPLATE_NAMES[template],
        "template": template,
        "grace_days": preset.grace_days,
        "late_fee_type": preset.late_fee_type,
        "late_fee_value": preset.late_fee_value,
        "late_fee_frequency": preset.late_fee_frequency,
        "late_fee_cap_percent": preset.late_fee_cap_percent,
        "escalation_days": preset.escalation_days,
        "max_reminders_per_day": preset.max_reminders_per_day,
        "max_reminders_per_week": preset.max_reminders_per_week,
        "max_extension_days": preset.max_extension_days,
        "max_extensions_per_year": preset.max_extensions_per_year,
        "contact_start": preset.contact_start,
        "contact_end": preset.contact_end,
        "contact_days": list(preset.contact_days),
        "reminder_sequence": preset.reminder_sequence_json(),
    }


def _default_rule_set(db: DbSession, org_id: uuid.UUID) -> RuleSet | None:
    return db.scalar(select(RuleSet).where(RuleSet.organization_id == org_id, RuleSet.is_default))


def _rule_set_out(rule_set: RuleSet) -> RuleSetOut:
    return RuleSetOut.model_validate(
        {f: getattr(rule_set, f) for f in RULE_FIELDS}
        | {"id": rule_set.id, "is_default": rule_set.is_default}
    )


@router.get(
    "/rule-set",
    response_model=RuleSetOut,
    operation_id="getRuleSet",
    responses={404: {"model": ErrorResponse, "description": "Not configured yet"}},
)
def get_rule_set(auth: CurrentAuth, db: DbSession) -> RuleSetOut:
    rule_set = _default_rule_set(db, auth.org_id)
    if rule_set is None:
        raise not_found("Todavía no has elegido tus reglas de cobro.")
    return _rule_set_out(rule_set)


@router.put("/rule-set", response_model=RuleSetOut, operation_id="updateRuleSet")
def update_rule_set(data: RuleSetUpdate, auth: CurrentAuth, db: DbSession) -> RuleSetOut:
    rule_set = _default_rule_set(db, auth.org_id)
    if data.template is not None:
        values = _preset_values(data.template)
    elif rule_set is not None:
        values = {f: getattr(rule_set, f) for f in RULE_FIELDS}
    else:
        values = _preset_values(RuleTemplate.STANDARD)
    overrides = data.model_dump(exclude_unset=True, exclude={"template"})
    values.update(overrides)
    try:
        checked = RuleSetValues.model_validate(values)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False)) from None

    stored = checked.model_dump(mode="python")
    stored["reminder_sequence"] = [s.model_dump() for s in checked.reminder_sequence]
    if rule_set is None:
        rule_set = RuleSet(organization_id=auth.org_id, is_default=True, **stored)
        db.add(rule_set)
    else:
        for field, value in stored.items():
            setattr(rule_set, field, value)
    db.flush()
    record_event(
        db,
        auth.org_id,
        "rules.updated",
        user_id=auth.user.id,
        template=checked.template.value,
        fields=sorted(overrides) if data.template is None else ["template", *sorted(overrides)],
    )
    db.commit()
    return _rule_set_out(rule_set)


@router.get("/assistant", response_model=AssistantOut, operation_id="getAssistant")
def get_assistant(auth: CurrentAuth, db: DbSession) -> AssistantOut:
    profile = db.scalar(
        select(AssistantProfile).where(AssistantProfile.organization_id == auth.org_id)
    )
    if profile is None:
        raise not_found("Todavía no has configurado tu asistente.")
    return AssistantOut.model_validate(profile)


@router.put("/assistant", response_model=AssistantOut, operation_id="updateAssistant")
def update_assistant(data: AssistantIn, auth: CurrentAuth, db: DbSession) -> AssistantOut:
    profile = db.scalar(
        select(AssistantProfile).where(AssistantProfile.organization_id == auth.org_id)
    )
    if profile is None:
        profile = AssistantProfile(organization_id=auth.org_id, **data.model_dump())
        db.add(profile)
    else:
        profile.display_name = data.display_name
        profile.tone = data.tone
    db.flush()
    record_event(
        db,
        auth.org_id,
        "assistant.updated",
        user_id=auth.user.id,
        display_name=data.display_name,
        tone=data.tone.value,
    )
    db.commit()
    return AssistantOut.model_validate(profile)


@router.get("/bank-accounts", response_model=list[BankAccountOut], operation_id="listBankAccounts")
def list_bank_accounts(
    auth: CurrentAuth, db: DbSession, include_inactive: bool = False
) -> list[BankAccountOut]:
    stmt = select(BankAccount).where(BankAccount.organization_id == auth.org_id)
    if not include_inactive:
        stmt = stmt.where(BankAccount.is_active)
    rows = db.scalars(stmt.order_by(BankAccount.created_at, BankAccount.id)).all()
    return [BankAccountOut.model_validate(r) for r in rows]


def _bank_conflict() -> Exception:
    return conflict("bank_account_exists", "Ya tienes esa cuenta registrada en ese banco.")


@router.post(
    "/bank-accounts",
    response_model=BankAccountOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBankAccount",
    responses={409: {"model": ErrorResponse}},
)
def create_bank_account(data: BankAccountIn, auth: CurrentAuth, db: DbSession) -> BankAccountOut:
    account = BankAccount(organization_id=auth.org_id, **data.model_dump())
    db.add(account)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _bank_conflict() from None
    record_event(
        db,
        auth.org_id,
        "bank_account.created",
        user_id=auth.user.id,
        bank_account_id=account.id,
        bank_name=account.bank_name,
    )
    db.commit()
    return BankAccountOut.model_validate(account)


@router.patch(
    "/bank-accounts/{bank_account_id}",
    response_model=BankAccountOut,
    operation_id="updateBankAccount",
    responses={409: {"model": ErrorResponse}},
)
def update_bank_account(
    bank_account_id: uuid.UUID, data: BankAccountUpdate, auth: CurrentAuth, db: DbSession
) -> BankAccountOut:
    account = get_owned(db, BankAccount, bank_account_id, auth.org_id)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None and field != "alias":
            continue
        setattr(account, field, value)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _bank_conflict() from None
    record_event(
        db,
        auth.org_id,
        "bank_account.updated",
        user_id=auth.user.id,
        bank_account_id=account.id,
        fields=sorted(changes),
    )
    db.commit()
    return BankAccountOut.model_validate(account)


@router.delete(
    "/bank-accounts/{bank_account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteBankAccount",
)
def delete_bank_account(bank_account_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> Response:
    """Deactivates the account: payments may still point at it."""
    account = get_owned(db, BankAccount, bank_account_id, auth.org_id)
    account.is_active = False
    record_event(
        db,
        auth.org_id,
        "bank_account.deactivated",
        user_id=auth.user.id,
        bank_account_id=account.id,
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
