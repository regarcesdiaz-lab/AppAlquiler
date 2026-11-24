"""Propiedades: buildings, units (apartments) and tenants."""

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentAuth, DbSession, get_owned
from app.api.errors import COMMON_RESPONSES, ErrorResponse, conflict
from app.domain.phones import phone_warning
from app.models import Building, Lease, Tenant, Unit
from app.schemas.common import ApiWarning
from app.schemas.properties import (
    BuildingIn,
    BuildingOut,
    BuildingUpdate,
    TenantIn,
    TenantOut,
    TenantUpdate,
    TenantWriteOut,
    UnitIn,
    UnitOut,
    UnitUpdate,
)
from app.services.timeline import record_event

router = APIRouter(tags=["properties"], responses=COMMON_RESPONSES)
CONFLICT = {409: {"model": ErrorResponse}}


def phone_warnings(phone: str, field: str = "phone") -> list[ApiWarning]:
    warning = phone_warning(phone)
    return [ApiWarning(code=warning.code, message=warning.message, field=field)] if warning else []


# -- buildings ---------------------------------------------------------------------------------


def _building_out(db: DbSession, building: Building) -> BuildingOut:
    count = db.scalar(select(func.count()).where(Unit.building_id == building.id)) or 0
    return BuildingOut(
        id=building.id, name=building.name, address=building.address, unit_count=count
    )


def _building_conflict() -> Exception:
    return conflict("building_name_taken", "Ya tienes un edificio con ese nombre.")


@router.get("/buildings", response_model=list[BuildingOut], operation_id="listBuildings")
def list_buildings(auth: CurrentAuth, db: DbSession) -> list[BuildingOut]:
    rows = db.execute(
        select(Building, func.count(Unit.id))
        .outerjoin(Unit, Unit.building_id == Building.id)
        .where(Building.organization_id == auth.org_id)
        .group_by(Building.id)
        .order_by(Building.name)
    ).all()
    return [
        BuildingOut(id=b.id, name=b.name, address=b.address, unit_count=count) for b, count in rows
    ]


@router.post(
    "/buildings",
    response_model=BuildingOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBuilding",
    responses=CONFLICT,
)
def create_building(data: BuildingIn, auth: CurrentAuth, db: DbSession) -> BuildingOut:
    building = Building(organization_id=auth.org_id, **data.model_dump())
    db.add(building)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _building_conflict() from None
    record_event(
        db,
        auth.org_id,
        "building.created",
        user_id=auth.user.id,
        building_id=building.id,
        name=building.name,
    )
    db.commit()
    return _building_out(db, building)


@router.get("/buildings/{building_id}", response_model=BuildingOut, operation_id="getBuilding")
def get_building(building_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> BuildingOut:
    return _building_out(db, get_owned(db, Building, building_id, auth.org_id))


@router.patch(
    "/buildings/{building_id}",
    response_model=BuildingOut,
    operation_id="updateBuilding",
    responses=CONFLICT,
)
def update_building(
    building_id: uuid.UUID, data: BuildingUpdate, auth: CurrentAuth, db: DbSession
) -> BuildingOut:
    building = get_owned(db, Building, building_id, auth.org_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") is not None:
        building.name = changes["name"]
    if "address" in changes:
        building.address = changes["address"]
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _building_conflict() from None
    record_event(
        db,
        auth.org_id,
        "building.updated",
        user_id=auth.user.id,
        building_id=building.id,
        fields=sorted(changes),
    )
    db.commit()
    return _building_out(db, building)


@router.delete(
    "/buildings/{building_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteBuilding",
    responses=CONFLICT,
)
def delete_building(building_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> Response:
    building = get_owned(db, Building, building_id, auth.org_id)
    if db.scalar(select(func.count()).where(Unit.building_id == building.id)):
        raise conflict(
            "building_has_units", "Este edificio tiene apartamentos. Muévelos o bórralos antes."
        )
    record_event(
        db,
        auth.org_id,
        "building.deleted",
        user_id=auth.user.id,
        building_id=building.id,
        name=building.name,
    )
    db.delete(building)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- units -------------------------------------------------------------------------------------


def _unit_out(db: DbSession, unit: Unit) -> UnitOut:
    lease_id = db.scalar(select(Lease.id).where(Lease.unit_id == unit.id, Lease.is_active))
    return UnitOut(
        id=unit.id,
        building_id=unit.building_id,
        code=unit.code,
        address=unit.address,
        active_lease_id=lease_id,
    )


def _unit_conflict() -> Exception:
    return conflict("unit_code_taken", "Ya existe un apartamento con ese código en ese edificio.")


def _check_building(db: DbSession, building_id: uuid.UUID | None, org_id: uuid.UUID) -> None:
    if building_id is not None:
        get_owned(db, Building, building_id, org_id)


@router.get("/units", response_model=list[UnitOut], operation_id="listUnits")
def list_units(
    auth: CurrentAuth, db: DbSession, building_id: uuid.UUID | None = None
) -> list[UnitOut]:
    stmt = (
        select(Unit, Lease.id)
        .outerjoin(Lease, (Lease.unit_id == Unit.id) & Lease.is_active)
        .where(Unit.organization_id == auth.org_id)
    )
    if building_id is not None:
        stmt = stmt.where(Unit.building_id == building_id)
    rows = db.execute(stmt.order_by(Unit.code)).all()
    return [
        UnitOut(
            id=u.id,
            building_id=u.building_id,
            code=u.code,
            address=u.address,
            active_lease_id=lease_id,
        )
        for u, lease_id in rows
    ]


@router.post(
    "/units",
    response_model=UnitOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createUnit",
    responses=CONFLICT,
)
def create_unit(data: UnitIn, auth: CurrentAuth, db: DbSession) -> UnitOut:
    _check_building(db, data.building_id, auth.org_id)
    unit = Unit(organization_id=auth.org_id, **data.model_dump())
    db.add(unit)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _unit_conflict() from None
    record_event(
        db,
        auth.org_id,
        "unit.created",
        target_unit_id=unit.id,
        user_id=auth.user.id,
        code=unit.code,
    )
    db.commit()
    return _unit_out(db, unit)


@router.get("/units/{unit_id}", response_model=UnitOut, operation_id="getUnit")
def get_unit(unit_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> UnitOut:
    return _unit_out(db, get_owned(db, Unit, unit_id, auth.org_id))


@router.patch(
    "/units/{unit_id}", response_model=UnitOut, operation_id="updateUnit", responses=CONFLICT
)
def update_unit(unit_id: uuid.UUID, data: UnitUpdate, auth: CurrentAuth, db: DbSession) -> UnitOut:
    unit = get_owned(db, Unit, unit_id, auth.org_id)
    changes = data.model_dump(exclude_unset=True)
    if "building_id" in changes:
        _check_building(db, changes["building_id"], auth.org_id)
        unit.building_id = changes["building_id"]
    if changes.get("code") is not None:
        unit.code = changes["code"]
    if "address" in changes:
        unit.address = changes["address"]
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _unit_conflict() from None
    record_event(
        db,
        auth.org_id,
        "unit.updated",
        target_unit_id=unit.id,
        user_id=auth.user.id,
        fields=sorted(changes),
    )
    db.commit()
    return _unit_out(db, unit)


@router.delete(
    "/units/{unit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteUnit",
    responses=CONFLICT,
)
def delete_unit(unit_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> Response:
    unit = get_owned(db, Unit, unit_id, auth.org_id)
    if db.scalar(select(func.count()).where(Lease.unit_id == unit.id)):
        raise conflict(
            "unit_has_leases",
            "Este apartamento tiene contratos y su historial se conserva. No se puede borrar.",
        )
    db.delete(unit)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- tenants -----------------------------------------------------------------------------------


def _tenant_conflict() -> Exception:
    return conflict("phone_taken", "Ya tienes un inquilino con ese teléfono.")


@router.get("/tenants", response_model=list[TenantOut], operation_id="listTenants")
def list_tenants(auth: CurrentAuth, db: DbSession, q: str | None = None) -> list[TenantOut]:
    stmt = select(Tenant).where(Tenant.organization_id == auth.org_id)
    if q and q.strip():
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Tenant.full_name.ilike(pattern), Tenant.phone.ilike(pattern)))
    return [TenantOut.model_validate(t) for t in db.scalars(stmt.order_by(Tenant.full_name))]


@router.post(
    "/tenants",
    response_model=TenantWriteOut,
    status_code=status.HTTP_201_CREATED,
    operation_id="createTenant",
    responses=CONFLICT,
)
def create_tenant(data: TenantIn, auth: CurrentAuth, db: DbSession) -> TenantWriteOut:
    tenant = Tenant(organization_id=auth.org_id, **data.model_dump())
    db.add(tenant)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _tenant_conflict() from None
    record_event(db, auth.org_id, "tenant.created", user_id=auth.user.id, tenant_id=tenant.id)
    db.commit()
    return TenantWriteOut(
        tenant=TenantOut.model_validate(tenant), warnings=phone_warnings(tenant.phone)
    )


@router.get("/tenants/{tenant_id}", response_model=TenantOut, operation_id="getTenant")
def get_tenant(tenant_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> TenantOut:
    return TenantOut.model_validate(get_owned(db, Tenant, tenant_id, auth.org_id))


@router.patch(
    "/tenants/{tenant_id}",
    response_model=TenantWriteOut,
    operation_id="updateTenant",
    responses=CONFLICT,
)
def update_tenant(
    tenant_id: uuid.UUID, data: TenantUpdate, auth: CurrentAuth, db: DbSession
) -> TenantWriteOut:
    tenant = get_owned(db, Tenant, tenant_id, auth.org_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("full_name") is not None:
        tenant.full_name = changes["full_name"]
    if changes.get("phone") is not None:
        tenant.phone = changes["phone"]
    if "email" in changes:
        tenant.email = changes["email"]
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise _tenant_conflict() from None
    record_event(
        db,
        auth.org_id,
        "tenant.updated",
        user_id=auth.user.id,
        tenant_id=tenant.id,
        fields=sorted(changes),
    )
    db.commit()
    warnings = phone_warnings(tenant.phone) if "phone" in changes else []
    return TenantWriteOut(tenant=TenantOut.model_validate(tenant), warnings=warnings)


@router.delete(
    "/tenants/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteTenant",
    responses=CONFLICT,
)
def delete_tenant(tenant_id: uuid.UUID, auth: CurrentAuth, db: DbSession) -> Response:
    tenant = get_owned(db, Tenant, tenant_id, auth.org_id)
    if db.scalar(select(func.count()).where(Lease.tenant_id == tenant.id)):
        raise conflict(
            "tenant_has_leases",
            "Este inquilino tiene contratos y su historial se conserva. No se puede borrar.",
        )
    db.delete(tenant)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
