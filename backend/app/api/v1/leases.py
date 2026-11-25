"""Contracts: create (consent + late fee question + deposit warning), detail screen (ficha),
timeline, and the owner's actions on one lease (cash payment, reminder now)."""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAuth, DbSession, decode_cursor, encode_cursor, get_owned
from app.api.errors import COMMON_RESPONSES, ErrorResponse, api_error, conflict
from app.api.v1.properties import phone_warnings
from app.core.timezone import local_today, utc_now
from app.domain.deposit import deposit_warning
from app.models import (
    Building,
    ConsentRecord,
    InboxItem,
    Lease,
    Organization,
    RuleSet,
    Tenant,
    TimelineEvent,
    Unit,
)
from app.models.enums import ConsentMethod, InboxItemStatus
from app.schemas.common import ApiWarning, PositiveMoney
from app.schemas.properties import (
    DEFAULT_CONSENT_EVIDENCE,
    BalanceOut,
    BuildingOut,
    ConsentOut,
    InboxItemOut,
    LeaseCreate,
    LeaseDetail,
    LeaseEnd,
    LeaseOut,
    LeaseSummary,
    LeaseUpdate,
    LeaseWriteOut,
    PeriodBalanceOut,
    PromiseOut,
    PunctualityMonth,
    PunctualityOut,
    StatusOut,
    TenantOut,
    TimelineEventOut,
    TimelinePage,
    UnitOut,
)
from app.services import payments as payment_service
from app.services import reminders as reminder_service
from app.services.lease_state import load_lease_state, on_time_percent
from app.services.messaging import send_welcome
from app.services.timeline import record_event

router = APIRouter(prefix="/leases", tags=["leases"], responses=COMMON_RESPONSES)
CONFLICT = {409: {"model": ErrorResponse}}
TIMELINE_PAGE_SIZE = 30


def _deposit_warnings(lease: Lease) -> list[ApiWarning]:
    warning = deposit_warning(lease.deposit_amount, lease.rent_amount)
    if warning is None:
        return []
    return [
        ApiWarning(code="deposit_above_legal_max", message=warning.message, field="deposit_amount")
    ]


def _check_rule_set(db: DbSession, rule_set_id: uuid.UUID | None, org_id: uuid.UUID) -> None:
    if rule_set_id is not None:
        get_owned(db, RuleSet, rule_set_id, org_id)


@router.get("", response_model=list[LeaseSummary], operation_id="listLeases")
def list_leases(
    auth: CurrentAuth,
    db: DbSession,
    active: bool | None = None,
    building_id: uuid.UUID | None = None,
    unit_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> list[LeaseSummary]:
    stmt = (
        select(Lease, Tenant, Unit, Building)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .join(Unit, Unit.id == Lease.unit_id)
        .outerjoin(Building, Building.id == Unit.building_id)
        .where(Lease.organization_id == auth.org_id)
    )
    if active is not None:
        stmt = stmt.where(Lease.is_active == active)
    if building_id is not None:
        stmt = stmt.where(Unit.building_id == building_id)
    if unit_id is not None:
        stmt = stmt.where(Lease.unit_id == unit_id)
    if tenant_id is not None:
        stmt = stmt.where(Lease.tenant_id == tenant_id)
    rows = db.execute(stmt.order_by(Lease.start_date.desc(), Lease.id)).all()
    return [
        LeaseSummary(
            **LeaseOut.model_validate(lease).model_dump(),
            tenant_name=tenant.full_name,
            tenant_phone=tenant.phone,
            unit_code=unit.code,
            building_id=unit.building_id,
            building_name=building.name if building else None,
        )
        for lease, tenant, unit, building in rows
    ]


@router.post(
    "",
    response_model=LeaseWriteOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createLease",
    responses=CONFLICT,
)
def create_lease(data: LeaseCreate, auth: CurrentAuth, db: DbSession) -> LeaseWriteOut:
    unit = get_owned(db, Unit, data.unit_id, auth.org_id)
    _check_rule_set(db, data.rule_set_id, auth.org_id)
    if db.scalar(select(Lease.id).where(Lease.unit_id == unit.id, Lease.is_active)):
        raise conflict("unit_occupied", "Este apartamento ya tiene un contrato activo.")
    warnings: list[ApiWarning] = []
    if data.tenant_id is not None:
        tenant = get_owned(db, Tenant, data.tenant_id, auth.org_id)
    else:
        assert data.tenant is not None
        tenant = db.scalar(
            select(Tenant).where(
                Tenant.organization_id == auth.org_id, Tenant.phone == data.tenant.phone
            )
        )
        if tenant is None:
            tenant = Tenant(organization_id=auth.org_id, **data.tenant.model_dump())
            db.add(tenant)
            db.flush()
            record_event(
                db, auth.org_id, "tenant.created", user_id=auth.user.id, tenant_id=tenant.id
            )
        warnings += phone_warnings(tenant.phone, "tenant.phone")

    lease = Lease(
        organization_id=auth.org_id,
        unit_id=unit.id,
        tenant_id=tenant.id,
        rule_set_id=data.rule_set_id,
        rent_amount=data.rent_amount,
        due_day=data.due_day,
        deposit_amount=data.deposit_amount,
        late_fee_agreed=data.late_fee_agreed,
        start_date=data.start_date,
        end_date=data.end_date,
        is_active=True,
    )
    db.add(lease)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise conflict("unit_occupied", "Este apartamento ya tiene un contrato activo.") from None
    now = utc_now()
    db.add(
        ConsentRecord(
            organization_id=auth.org_id,
            lease_id=lease.id,
            tenant_id=tenant.id,
            method=ConsentMethod.CONTRACT_CLAUSE,
            evidence=data.consent_evidence or DEFAULT_CONSENT_EVIDENCE,
            granted_at=now,
        )
    )
    db.flush()
    record_event(
        db,
        auth.org_id,
        "lease.created",
        lease=lease,
        user_id=auth.user.id,
        rent=lease.rent_amount,
        due_day=lease.due_day,
        deposit=lease.deposit_amount,
        late_fee_agreed=lease.late_fee_agreed,
    )
    record_event(
        db,
        auth.org_id,
        "consent.granted",
        lease=lease,
        user_id=auth.user.id,
        method=ConsentMethod.CONTRACT_CLAUSE.value,
    )
    # Queued with the lease; the job is enqueued once this commit succeeds.
    send_welcome(db, lease, auth.user.id)
    db.commit()
    return LeaseWriteOut(
        lease=LeaseOut.model_validate(lease), warnings=warnings + _deposit_warnings(lease)
    )


def _timeline_page(
    db: DbSession, lease: Lease, cursor: str | None, limit: int = TIMELINE_PAGE_SIZE
) -> TimelinePage:
    stmt = select(TimelineEvent).where(
        TimelineEvent.organization_id == lease.organization_id,
        TimelineEvent.lease_id == lease.id,
    )
    if cursor:
        at, event_id = decode_cursor(cursor, 2)
        try:
            key = (dt.datetime.fromisoformat(at), uuid.UUID(event_id))
        except ValueError:
            raise api_error(422, "invalid_cursor", "Cursor inválido.") from None
        stmt = stmt.where(tuple_(TimelineEvent.occurred_at, TimelineEvent.id) < key)
    rows = db.scalars(
        stmt.order_by(TimelineEvent.occurred_at.desc(), TimelineEvent.id.desc()).limit(limit + 1)
    ).all()
    items = rows[:limit]
    next_cursor = encode_cursor(items[-1].occurred_at, items[-1].id) if len(rows) > limit else None
    return TimelinePage(
        items=[TimelineEventOut.model_validate(e) for e in items], next_cursor=next_cursor
    )


@router.get("/{lease_id}", response_model=LeaseDetail, operation_id="getLease")
def get_lease(lease_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> LeaseDetail:
    lease = get_owned(db, Lease, lease_id, auth.org_id)
    org = db.get(Organization, auth.org_id)
    assert org is not None
    tenant = db.get(Tenant, lease.tenant_id)
    unit = db.get(Unit, lease.unit_id)
    assert tenant is not None and unit is not None
    building = db.get(Building, unit.building_id) if unit.building_id else None
    state = load_lease_state(db, lease, local_today(org.timezone), org.timezone)
    months = state.punctuality()
    pending = db.scalar(
        select(InboxItem)
        .where(InboxItem.lease_id == lease.id, InboxItem.status == InboxItemStatus.OPEN)
        .order_by(InboxItem.priority, InboxItem.created_at)
        .limit(1)
    )
    consent = db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.lease_id == lease.id)
        .order_by(ConsentRecord.revoked_at.is_(None).desc(), ConsentRecord.granted_at.desc())
        .limit(1)
    )
    return LeaseDetail(
        lease=LeaseOut.model_validate(lease),
        tenant=TenantOut.model_validate(tenant),
        unit=UnitOut(
            id=unit.id,
            building_id=unit.building_id,
            code=unit.code,
            address=unit.address,
            active_lease_id=lease.id if lease.is_active else None,
        ),
        building=BuildingOut(id=building.id, name=building.name, address=building.address)
        if building
        else None,
        status=StatusOut(
            state=state.status.state,
            amount_due=state.status.amount_due,
            days_late=state.status.days_late,
            in_grace=state.status.in_grace,
        ),
        balance=BalanceOut(
            total=state.balance.total,
            credit_in_favour=state.balance.credit_in_favour,
            by_period=[
                PeriodBalanceOut(
                    period=p.period, charged=p.charged, paid=p.paid, outstanding=p.outstanding
                )
                for p in state.balance.by_period
            ],
        ),
        punctuality=PunctualityOut(
            months=[PunctualityMonth(period=m.period, result=m.result.value) for m in months],
            on_time_percent=on_time_percent(months),
        ),
        pending_action=InboxItemOut.model_validate(pending) if pending else None,
        consent=ConsentOut.model_validate(consent) if consent else None,
        active_promise=PromiseOut.model_validate(state.active_promise)
        if state.active_promise
        else None,
        next_due_on=state.next_due_on(),
        timeline=_timeline_page(db, lease, None),
    )


@router.get("/{lease_id}/timeline", response_model=TimelinePage, operation_id="getLeaseTimeline")
def get_lease_timeline(
    lease_id: uuid.UUID,
    auth: CurrentAuth,
    db: DbSession,
    cursor: str | None = None,
    limit: int = Query(default=TIMELINE_PAGE_SIZE, ge=1, le=100),
) -> TimelinePage:
    lease = get_owned(db, Lease, lease_id, auth.org_id)
    return _timeline_page(db, lease, cursor, limit)


@router.patch("/{lease_id}", response_model=LeaseWriteOut, operation_id="updateLease")
def update_lease(
    lease_id: uuid.UUID, data: LeaseUpdate, auth: CurrentAuth, db: DbSession
) -> LeaseWriteOut:
    lease = get_owned(db, Lease, lease_id, auth.org_id)
    changes = data.model_dump(exclude_unset=True)
    if "rule_set_id" in changes:
        _check_rule_set(db, changes["rule_set_id"], auth.org_id)
    before: dict[str, object] = {}
    for field, value in changes.items():
        if value is None and field not in {"end_date", "rule_set_id"}:
            continue
        before[field] = getattr(lease, field)
        setattr(lease, field, value)
    if lease.end_date is not None and lease.end_date < lease.start_date:
        raise api_error(422, "invalid_end_date", "La fecha de fin no puede ser antes del inicio.")
    db.flush()
    record_event(
        db,
        auth.org_id,
        "lease.updated",
        lease=lease,
        user_id=auth.user.id,
        changes={f: {"from": before[f], "to": getattr(lease, f)} for f in before},
    )
    db.commit()
    return LeaseWriteOut(lease=LeaseOut.model_validate(lease), warnings=_deposit_warnings(lease))


@router.post(
    "/{lease_id}/end", response_model=LeaseOut, operation_id="endLease", responses=CONFLICT
)
def end_lease(lease_id: uuid.UUID, data: LeaseEnd, auth: CurrentAuth, db: DbSession) -> LeaseOut:
    lease = get_owned(db, Lease, lease_id, auth.org_id)
    if not lease.is_active:
        raise conflict("lease_inactive", "Este contrato ya terminó.")
    if data.end_date < lease.start_date:
        raise api_error(422, "invalid_end_date", "La fecha de fin no puede ser antes del inicio.")
    lease.is_active = False
    lease.end_date = data.end_date
    db.flush()
    record_event(
        db, auth.org_id, "lease.ended", lease=lease, user_id=auth.user.id, end_date=data.end_date
    )
    db.commit()
    return LeaseOut.model_validate(lease)


class CashPaymentIn(BaseModel):
    amount: PositiveMoney
    paid_on: dt.date
    note: str | None = Field(default=None, max_length=100)


class CashPaymentOut(BaseModel):
    payment_id: uuid.UUID
    amount: Decimal
    paid_on: dt.date


@router.post(
    "/{lease_id}/payments/cash",
    response_model=CashPaymentOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="recordCashPayment",
)
def record_cash_payment(
    lease_id: uuid.UUID, data: CashPaymentIn, auth: CurrentAuth, db: DbSession
) -> CashPaymentOut:
    lease = get_owned(db, Lease, lease_id, auth.org_id, for_update=True)
    org = db.get(Organization, auth.org_id)
    assert org is not None
    if data.paid_on > local_today(org.timezone):
        raise api_error(422, "future_date", "La fecha del pago no puede ser en el futuro.")
    payment = payment_service.record_cash(
        db,
        lease,
        org,
        amount=data.amount,
        paid_on=data.paid_on,
        note=data.note,
        user_id=auth.user.id,
    )
    db.commit()
    return CashPaymentOut(payment_id=payment.id, amount=payment.amount, paid_on=payment.paid_on)


class ReminderOut(BaseModel):
    message_id: uuid.UUID
    status: str
    body: str


class ReminderBlockedDetail(BaseModel):
    code: str
    message: str
    reasons: list[str]
    next_contact_time: dt.datetime | None


class ReminderBlockedResponse(BaseModel):
    detail: ReminderBlockedDetail


@router.post(
    "/{lease_id}/reminders",
    response_model=ReminderOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="sendReminderNow",
    responses={409: {"model": ReminderBlockedResponse}},
)
def send_reminder(lease_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> ReminderOut:
    lease = get_owned(db, Lease, lease_id, auth.org_id, for_update=True)
    if not lease.is_active:
        raise conflict("lease_inactive", "Este contrato ya terminó.")
    org = db.get(Organization, auth.org_id)
    assert org is not None
    try:
        message = reminder_service.send_reminder_now(db, lease, org, auth.user.id)
    except reminder_service.NothingToRemindError:
        raise conflict("nothing_to_remind", "Este inquilino no debe nada ahora mismo.") from None
    except reminder_service.ReminderBlockedError as blocked:
        db.rollback()
        raise conflict(
            "reminder_blocked",
            blocked.message(),
            reasons=[r.value for r in blocked.reasons],
            next_contact_time=blocked.next_contact_time.isoformat()
            if blocked.next_contact_time
            else None,
        ) from None
    db.commit()
    return ReminderOut(message_id=message.id, status=message.status.value, body=message.body)
