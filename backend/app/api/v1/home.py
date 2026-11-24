"""Inicio: collected vs expected this month, counts by state, open items and the tenant list
ordered by urgency. Summary numbers cover the whole organization; filters apply to the list."""

import datetime as dt
import uuid
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentAuth, DbSession, get_owned
from app.api.errors import COMMON_RESPONSES, ErrorResponse, api_error, conflict
from app.core.timezone import local_today, utc_now
from app.domain.ledger import ZERO
from app.domain.periods import period_of
from app.domain.status import HomeRow, TenantState, TenantStatus, sort_by_urgency
from app.models import Building, InboxItem, Lease, Organization, Tenant, Unit
from app.models.enums import InboxItemStatus
from app.schemas.properties import InboxItemOut
from app.services.lease_state import load_lease_states
from app.services.timeline import record_event

router = APIRouter(tags=["home"], responses=COMMON_RESPONSES)


class StateCounts(BaseModel):
    overdue: int = 0
    pending_confirmation: int = 0
    promise: int = 0
    upcoming: int = 0
    paid: int = 0
    vacant: int = 0


class HomeRowOut(BaseModel):
    unit_id: uuid.UUID
    unit_code: str
    building_id: uuid.UUID | None
    building_name: str | None
    lease_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    tenant_phone: str | None
    rent_amount: Decimal | None
    state: TenantState
    in_grace: bool
    amount_due: Decimal
    days_late: int
    next_due_on: dt.date | None


class HomeOut(BaseModel):
    period: str
    expected: Decimal
    collected: Decimal
    counts: StateCounts
    inbox: list[InboxItemOut]
    rows: list[HomeRowOut]


@router.get("/home", response_model=HomeOut, operation_id="getHome")
def get_home(
    auth: CurrentAuth,
    db: DbSession,
    state: TenantState | None = None,
    building_id: uuid.UUID | None = None,
    q: str | None = None,
) -> HomeOut:
    org = db.get(Organization, auth.org_id)
    assert org is not None
    today = local_today(org.timezone)
    period = period_of(today)

    units = db.execute(
        select(Unit, Building)
        .outerjoin(Building, Building.id == Unit.building_id)
        .where(Unit.organization_id == org.id)
    ).all()
    active = db.execute(
        select(Lease, Tenant)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .where(Lease.organization_id == org.id, Lease.is_active)
    ).all()
    lease_by_unit = {lease.unit_id: (lease, tenant) for lease, tenant in active}
    states = load_lease_states(db, org.id, [lease for lease, _ in active], today, org.timezone)

    expected = collected = ZERO
    counts = StateCounts()
    rows: list[HomeRow[HomeRowOut]] = []
    for unit, building in units:
        lease, tenant = lease_by_unit.get(unit.id, (None, None))
        lease_state = states.get(lease.id) if lease else None
        status_ = lease_state.status if lease_state else TenantStatus(TenantState.VACANT)
        if lease_state:
            exp, col = lease_state.expected_and_collected(period)
            expected += exp
            collected += col
        setattr(counts, status_.state.value, getattr(counts, status_.state.value) + 1)
        item = HomeRowOut(
            unit_id=unit.id,
            unit_code=unit.code,
            building_id=unit.building_id,
            building_name=building.name if building else None,
            lease_id=lease.id if lease else None,
            tenant_id=tenant.id if tenant else None,
            tenant_name=tenant.full_name if tenant else None,
            tenant_phone=tenant.phone if tenant else None,
            rent_amount=lease.rent_amount if lease else None,
            state=status_.state,
            in_grace=status_.in_grace,
            amount_due=status_.amount_due,
            days_late=status_.days_late,
            next_due_on=lease_state.next_due_on() if lease_state else None,
        )
        rows.append(HomeRow(status_, item, tiebreak=f"{item.tenant_name or ''}|{unit.code}"))

    def keep(row: HomeRowOut) -> bool:
        if state is not None and row.state != state:
            return False
        if building_id is not None and row.building_id != building_id:
            return False
        if q and q.strip():
            needle = q.strip().lower()
            haystack = " ".join(
                filter(None, [row.tenant_name, row.tenant_phone, row.unit_code, row.building_name])
            ).lower()
            return needle in haystack
        return True

    inbox = db.scalars(
        select(InboxItem)
        .where(InboxItem.organization_id == org.id, InboxItem.status == InboxItemStatus.OPEN)
        .order_by(InboxItem.priority, InboxItem.created_at)
    ).all()
    return HomeOut(
        period=period,
        expected=expected,
        collected=collected,
        counts=counts,
        inbox=[InboxItemOut.model_validate(i) for i in inbox],
        rows=[r.item for r in sort_by_urgency(rows) if keep(r.item)],
    )


class InboxResolveIn(BaseModel):
    status: InboxItemStatus
    note: str | None = Field(default=None, max_length=500)


@router.post(
    "/inbox/{inbox_item_id}/resolve",
    response_model=InboxItemOut,
    operation_id="resolveInboxItem",
    responses={409: {"model": ErrorResponse}},
)
def resolve_inbox_item(
    inbox_item_id: uuid.UUID, data: InboxResolveIn, auth: CurrentAuth, db: DbSession
) -> InboxItemOut:
    if data.status == InboxItemStatus.OPEN:
        raise api_error(422, "invalid_status", "Elige resuelto o descartado.")
    item = get_owned(db, InboxItem, inbox_item_id, auth.org_id, for_update=True)
    if item.status != InboxItemStatus.OPEN:
        raise conflict("inbox_item_closed", "Este pendiente ya estaba cerrado.")
    now = utc_now()
    item.status = data.status
    item.resolved_at = now
    db.flush()
    lease = db.get(Lease, item.lease_id) if item.lease_id else None
    record_event(
        db,
        auth.org_id,
        "inbox.resolved",
        lease=lease,
        user_id=auth.user.id,
        inbox_item_id=item.id,
        type=item.type.value,
        status=data.status.value,
        note=data.note,
    )
    db.commit()
    return InboxItemOut.model_validate(item)
