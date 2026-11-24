"""Cobros: payment history (filters, cursor pagination), Excel export, confirm and reject."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import Select, select, tuple_

from app.api.deps import CurrentAuth, DbSession, decode_cursor, encode_cursor, get_owned
from app.api.errors import COMMON_RESPONSES, ErrorResponse, api_error, conflict
from app.core.timezone import business_zone, local_today
from app.models import Building, Lease, Organization, Payment, Tenant, Unit
from app.models.enums import PaymentSource, PaymentStatus
from app.services import payments as payment_service
from app.services.exports import XLSX_MEDIA_TYPE, PaymentRow, payments_workbook

router = APIRouter(prefix="/payments", tags=["payments"], responses=COMMON_RESPONSES)
EXPORT_MAX_ROWS = 10_000


class PaymentFilters(BaseModel):
    status: PaymentStatus | None = None
    source: PaymentSource | None = None
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    building_id: uuid.UUID | None = None
    lease_id: uuid.UUID | None = None


def payment_filters(
    status: PaymentStatus | None = None,
    source: PaymentSource | None = None,
    date_from: dt.date | None = None,
    date_to: dt.date | None = None,
    building_id: uuid.UUID | None = None,
    lease_id: uuid.UUID | None = None,
) -> PaymentFilters:
    return PaymentFilters(
        status=status,
        source=source,
        date_from=date_from,
        date_to=date_to,
        building_id=building_id,
        lease_id=lease_id,
    )


Filters = Annotated[PaymentFilters, Depends(payment_filters)]


class PaymentOut(BaseModel):
    id: uuid.UUID
    lease_id: uuid.UUID
    tenant_name: str
    unit_code: str
    building_name: str | None
    amount: Decimal
    paid_on: dt.date
    status: PaymentStatus
    source: PaymentSource
    bank: str | None
    reference: str | None
    confirmed_at: dt.datetime | None
    created_at: dt.datetime
    has_receipt_file: bool


class PaymentPage(BaseModel):
    items: list[PaymentOut]
    next_cursor: str | None


def _query(org_id: uuid.UUID, filters: PaymentFilters) -> Select:
    stmt = (
        select(Payment, Tenant.full_name, Unit.code, Building.name)
        .join(Lease, Lease.id == Payment.lease_id)
        .join(Tenant, Tenant.id == Lease.tenant_id)
        .join(Unit, Unit.id == Lease.unit_id)
        .outerjoin(Building, Building.id == Unit.building_id)
        .where(Payment.organization_id == org_id)
    )
    if filters.status is not None:
        stmt = stmt.where(Payment.status == filters.status)
    if filters.source is not None:
        stmt = stmt.where(Payment.source == filters.source)
    if filters.date_from is not None:
        stmt = stmt.where(Payment.paid_on >= filters.date_from)
    if filters.date_to is not None:
        stmt = stmt.where(Payment.paid_on <= filters.date_to)
    if filters.building_id is not None:
        stmt = stmt.where(Unit.building_id == filters.building_id)
    if filters.lease_id is not None:
        stmt = stmt.where(Payment.lease_id == filters.lease_id)
    return stmt.order_by(Payment.paid_on.desc(), Payment.id.desc())


@router.get("", response_model=PaymentPage, operation_id="listPayments")
def list_payments(
    auth: CurrentAuth,
    db: DbSession,
    filters: Filters,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> PaymentPage:
    stmt = _query(auth.org_id, filters)
    if cursor:
        paid_on, payment_id = decode_cursor(cursor, 2)
        try:
            key = (dt.date.fromisoformat(paid_on), uuid.UUID(payment_id))
        except ValueError:
            raise api_error(422, "invalid_cursor", "Cursor inválido.") from None
        stmt = stmt.where(tuple_(Payment.paid_on, Payment.id) < key)
    rows = db.execute(stmt.limit(limit + 1)).all()
    page = rows[:limit]
    items = [
        PaymentOut(
            id=p.id,
            lease_id=p.lease_id,
            tenant_name=tenant_name,
            unit_code=unit_code,
            building_name=building_name,
            amount=p.amount,
            paid_on=p.paid_on,
            status=p.status,
            source=p.source,
            bank=p.bank,
            reference=p.reference,
            confirmed_at=p.confirmed_at,
            created_at=p.created_at,
            has_receipt_file=p.file_key is not None,
        )
        for p, tenant_name, unit_code, building_name in page
    ]
    next_cursor = encode_cursor(page[-1][0].paid_on, page[-1][0].id) if len(rows) > limit else None
    return PaymentPage(items=items, next_cursor=next_cursor)


@router.get(
    "/export.xlsx",
    operation_id="exportPayments",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "Excel file"}},
)
def export_payments(auth: CurrentAuth, db: DbSession, filters: Filters) -> Response:
    org = db.get(Organization, auth.org_id)
    assert org is not None
    rows = db.execute(_query(auth.org_id, filters).limit(EXPORT_MAX_ROWS)).all()
    content = payments_workbook(
        (
            PaymentRow(
                paid_on=p.paid_on,
                tenant_name=tenant_name,
                building_name=building_name,
                unit_code=unit_code,
                amount=p.amount,
                status=p.status,
                source=p.source,
                bank=p.bank,
                reference=p.reference,
                confirmed_at=p.confirmed_at,
            )
            for p, tenant_name, unit_code, building_name in rows
        ),
        business_zone(org.timezone),
    )
    filename = f"pagos-{local_today(org.timezone).isoformat()}.xlsx"
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class PaymentActionOut(BaseModel):
    id: uuid.UUID
    status: PaymentStatus


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


def _locked_reported(db: DbSession, payment_id: uuid.UUID, org_id: uuid.UUID) -> Payment:
    payment = get_owned(db, Payment, payment_id, org_id, for_update=True)
    if payment.status != PaymentStatus.REPORTED:
        raise conflict("payment_not_reported", "Este pago ya fue confirmado o rechazado.")
    return payment


@router.post(
    "/{payment_id}/confirm",
    response_model=PaymentActionOut,
    operation_id="confirmPayment",
    responses={409: {"model": ErrorResponse}},
)
def confirm_payment(payment_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> PaymentActionOut:
    payment = _locked_reported(db, payment_id, auth.org_id)
    lease = get_owned(db, Lease, payment.lease_id, auth.org_id, for_update=True)
    org = db.get(Organization, auth.org_id)
    assert org is not None
    payment_service.confirm_payment(db, payment, lease, org, auth.user.id)
    db.commit()
    return PaymentActionOut(id=payment.id, status=payment.status)


@router.post(
    "/{payment_id}/reject",
    response_model=PaymentActionOut,
    operation_id="rejectPayment",
    responses={409: {"model": ErrorResponse}},
)
def reject_payment(
    payment_id: uuid.UUID, data: RejectIn, auth: CurrentAuth, db: DbSession
) -> PaymentActionOut:
    payment = _locked_reported(db, payment_id, auth.org_id)
    lease = get_owned(db, Lease, payment.lease_id, auth.org_id)
    payment_service.reject_payment(db, payment, lease, data.reason, auth.user.id)
    db.commit()
    return PaymentActionOut(id=payment.id, status=payment.status)
