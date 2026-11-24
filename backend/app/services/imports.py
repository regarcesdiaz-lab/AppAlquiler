"""Import buildings, units, tenants and leases from the Excel template. All or nothing.

Every row is validated first. If any row has an error nothing is written, and the owner gets
the list of errors by row and column. Formulas are never evaluated (cached values only).
"""

import datetime as dt
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import BytesIO
from typing import Any

from email_validator import EmailNotValidError, validate_email
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timezone import utc_now
from app.domain.deposit import deposit_warning
from app.domain.ledger import money
from app.domain.phones import InvalidPhoneError, normalize_phone, phone_warning
from app.models import Building, ConsentRecord, Lease, Tenant, Unit
from app.models.enums import ConsentMethod
from app.schemas.properties import DEFAULT_CONSENT_EVIDENCE
from app.services.exports import IMPORT_COLUMNS, TEMPLATE_SHEET
from app.services.timeline import record_event

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500
YES = {"si", "s", "yes", "y", "true", "1", "x"}
NO = {"no", "n", "false", "0"}
TITLES = dict(IMPORT_COLUMNS)


class UnreadableFileError(Exception):
    pass


@dataclass
class RowIssue:
    row: int
    column: str | None
    message: str
    code: str | None = None


@dataclass
class ParsedRow:
    row: int
    building: str | None
    unit: str
    address: str | None
    tenant_name: str
    phone: str
    email: str | None
    rent: Decimal
    due_day: int
    deposit: Decimal
    start_date: dt.date
    end_date: dt.date | None
    late_fee_agreed: bool


@dataclass
class ImportReport:
    rows_total: int = 0
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[RowIssue] = field(default_factory=list)
    warnings: list[RowIssue] = field(default_factory=list)
    created: dict[str, int] = field(
        default_factory=lambda: {"buildings": 0, "units": 0, "tenants": 0, "leases": 0}
    )


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(stripped.lower().replace("¿", "").replace("?", "").split())


_HEADER_KEYS = {_norm(title): key for key, title in IMPORT_COLUMNS}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return text or None


def _decimal(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, int | float):
        # Excel stores numbers as floats; go through str() so no binary float reaches money.
        return money(str(value))
    text = (_text(value) or "").replace("RD$", "").replace("$", "").replace(",", "").strip()
    try:
        return money(text)
    except InvalidOperation:
        raise ValueError from None


def _date(value: Any) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = _text(value)
    if text is None:
        raise ValueError
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()  # noqa: DTZ007 (plain date)
        except ValueError:
            continue
    raise ValueError


def _yes_no(value: Any) -> bool:
    text = _norm(_text(value) or "")
    if text in YES:
        return True
    if text in NO:
        return False
    raise ValueError


def read_rows(content: bytes) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
    """Rows as {column key: raw value}, plus the template columns missing from the header."""
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise UnreadableFileError(str(exc)) from exc
    try:
        sheet = (
            workbook[TEMPLATE_SHEET] if TEMPLATE_SHEET in workbook.sheetnames else workbook.active
        )
        iterator = sheet.iter_rows(values_only=True)
        header = next(iterator, None) or ()
        positions: dict[str, int] = {}
        for index, title in enumerate(header):
            key = _HEADER_KEYS.get(_norm(str(title))) if title is not None else None
            if key and key not in positions:
                positions[key] = index
        missing = [title for key, title in IMPORT_COLUMNS if key not in positions]
        rows: list[tuple[int, dict[str, Any]]] = []
        for number, values in enumerate(iterator, start=2):
            if values is None or all(_text(v) is None for v in values):
                continue
            rows.append(
                (
                    number,
                    {key: values[i] if i < len(values) else None for key, i in positions.items()},
                )
            )
            if len(rows) > MAX_ROWS:
                break
        return rows, missing
    finally:
        workbook.close()


def _parse_row(number: int, raw: dict[str, Any], report: ImportReport) -> ParsedRow | None:
    errors_before = len(report.errors)

    def error(key: str, message: str) -> None:
        report.errors.append(RowIssue(number, TITLES[key], message))

    def required_text(key: str, max_len: int) -> str:
        text = _text(raw.get(key))
        if text is None:
            error(key, "Falta este dato.")
            return ""
        if len(text) > max_len:
            error(key, f"Máximo {max_len} caracteres.")
        return text

    def optional_text(key: str, max_len: int) -> str | None:
        text = _text(raw.get(key))
        if text is not None and len(text) > max_len:
            error(key, f"Máximo {max_len} caracteres.")
        return text

    building = optional_text("building", 120)
    unit = required_text("unit", 40)
    address = optional_text("address", 300)
    tenant_name = required_text("tenant_name", 200)

    phone = ""
    raw_phone = _text(raw.get("phone"))
    if raw_phone is None:
        error("phone", "Falta este dato.")
    else:
        try:
            phone = normalize_phone(raw_phone)
        except InvalidPhoneError:
            error("phone", "Teléfono no válido. Ejemplo: 809-555-0101.")
        else:
            if warning := phone_warning(phone):
                report.warnings.append(
                    RowIssue(number, TITLES["phone"], warning.message, warning.code)
                )

    email = None
    if (raw_email := _text(raw.get("email"))) is not None:
        try:
            email = validate_email(raw_email, check_deliverability=False).normalized.lower()
        except EmailNotValidError:
            error("email", "Correo no válido.")

    rent = Decimal(0)
    try:
        rent = _decimal(raw.get("rent"))
        if rent <= 0:
            error("rent", "La renta debe ser mayor que 0.")
    except ValueError:
        error("rent", "Escribe la renta como número, por ejemplo 25000.")

    due_day = 0
    try:
        due_value = _decimal(raw.get("due_day"))
        if due_value != due_value.to_integral_value() or not 1 <= due_value <= 31:
            raise ValueError
        due_day = int(due_value)
    except ValueError:
        error("due_day", "El día de pago va del 1 al 31.")

    deposit = Decimal("0.00")
    if _text(raw.get("deposit")) is not None:
        try:
            deposit = _decimal(raw.get("deposit"))
            if deposit < 0:
                error("deposit", "El depósito no puede ser negativo.")
        except ValueError:
            error("deposit", "Escribe el depósito como número, por ejemplo 50000.")

    start = dt.date.min
    try:
        start = _date(raw.get("start_date"))
    except ValueError:
        error("start_date", "Fecha no válida. Usa DD/MM/AAAA.")

    end = None
    if _text(raw.get("end_date")) is not None or isinstance(raw.get("end_date"), dt.date):
        try:
            end = _date(raw.get("end_date"))
            if start != dt.date.min and end < start:
                error("end_date", "La fecha de fin no puede ser antes del inicio.")
        except ValueError:
            error("end_date", "Fecha no válida. Usa DD/MM/AAAA.")

    late_fee = False
    try:
        late_fee = _yes_no(raw.get("late_fee_agreed"))
    except ValueError:
        error("late_fee_agreed", "Escribe Sí o No.")

    try:
        consent = _yes_no(raw.get("consent"))
    except ValueError:
        consent = False
    if not consent:
        error(
            "consent",
            "Escribe Sí: el contrato debe incluir el consentimiento para recibir mensajes "
            "por WhatsApp.",
        )

    if len(report.errors) > errors_before:
        return None
    if warning := deposit_warning(deposit, rent):
        report.warnings.append(
            RowIssue(number, TITLES["deposit"], warning.message, "deposit_above_legal_max")
        )
    return ParsedRow(
        number,
        building,
        unit,
        address,
        tenant_name,
        phone,
        email,
        rent,
        due_day,
        deposit,
        start,
        end,
        late_fee,
    )


def validate_import(db: Session, org_id: uuid.UUID, content: bytes) -> ImportReport:
    report = ImportReport()
    rows, missing = read_rows(content)
    if missing:
        report.errors.append(
            RowIssue(1, None, "Faltan columnas de la plantilla: " + ", ".join(missing) + ".")
        )
        return report
    if len(rows) > MAX_ROWS:
        report.errors.append(
            RowIssue(1, None, f"El archivo tiene más de {MAX_ROWS} filas. Divídelo en partes.")
        )
        return report
    report.rows_total = len(rows)
    for number, raw in rows:
        parsed = _parse_row(number, raw, report)
        if parsed is not None:
            report.rows.append(parsed)

    # Each unit at most once in the file, and only if it has no active lease already.
    seen: dict[tuple[str, str], int] = {}
    buildings = {
        _norm(b.name): b
        for b in db.scalars(select(Building).where(Building.organization_id == org_id))
    }
    for row in report.rows:
        key = (_norm(row.building or ""), _norm(row.unit))
        if key in seen:
            report.errors.append(
                RowIssue(
                    row.row, TITLES["unit"], f"Este apartamento ya aparece en la fila {seen[key]}."
                )
            )
            continue
        seen[key] = row.row
        building = buildings.get(key[0]) if row.building else None
        if row.building and building is None:
            continue
        unit = db.scalar(
            select(Unit).where(
                Unit.organization_id == org_id,
                Unit.building_id == building.id if building else Unit.building_id.is_(None),
                Unit.code == row.unit,
            )
        )
        if unit is not None and db.scalar(
            select(Lease.id).where(Lease.unit_id == unit.id, Lease.is_active)
        ):
            report.errors.append(
                RowIssue(row.row, TITLES["unit"], "Este apartamento ya tiene un contrato activo.")
            )
    report.errors.sort(key=lambda e: (e.row, e.column or ""))
    return report


def apply_import(db: Session, org_id: uuid.UUID, report: ImportReport, user_id: uuid.UUID) -> None:
    """Write a report without errors. The caller commits."""
    assert not report.errors
    now = utc_now()
    buildings = {
        _norm(b.name): b
        for b in db.scalars(select(Building).where(Building.organization_id == org_id))
    }
    tenants = {
        t.phone: t for t in db.scalars(select(Tenant).where(Tenant.organization_id == org_id))
    }
    for row in report.rows:
        building = None
        if row.building:
            building = buildings.get(_norm(row.building))
            if building is None:
                building = Building(organization_id=org_id, name=row.building)
                db.add(building)
                db.flush()
                buildings[_norm(row.building)] = building
                report.created["buildings"] += 1
        unit = db.scalar(
            select(Unit).where(
                Unit.organization_id == org_id,
                Unit.building_id == building.id if building else Unit.building_id.is_(None),
                Unit.code == row.unit,
            )
        )
        if unit is None:
            unit = Unit(
                organization_id=org_id,
                building_id=building.id if building else None,
                code=row.unit,
                address=row.address,
            )
            db.add(unit)
            db.flush()
            report.created["units"] += 1
        tenant = tenants.get(row.phone)
        if tenant is None:
            tenant = Tenant(
                organization_id=org_id, full_name=row.tenant_name, phone=row.phone, email=row.email
            )
            db.add(tenant)
            db.flush()
            tenants[row.phone] = tenant
            report.created["tenants"] += 1
        lease = Lease(
            organization_id=org_id,
            unit_id=unit.id,
            tenant_id=tenant.id,
            rent_amount=row.rent,
            due_day=row.due_day,
            deposit_amount=row.deposit,
            late_fee_agreed=row.late_fee_agreed,
            start_date=row.start_date,
            end_date=row.end_date,
            is_active=True,
        )
        db.add(lease)
        db.flush()
        db.add(
            ConsentRecord(
                organization_id=org_id,
                lease_id=lease.id,
                tenant_id=tenant.id,
                method=ConsentMethod.CONTRACT_CLAUSE,
                evidence=DEFAULT_CONSENT_EVIDENCE,
                granted_at=now,
            )
        )
        db.flush()
        report.created["leases"] += 1
        record_event(
            db,
            org_id,
            "lease.created",
            lease=lease,
            user_id=user_id,
            source="excel_import",
            rent=lease.rent_amount,
            due_day=lease.due_day,
        )
        record_event(
            db,
            org_id,
            "consent.granted",
            lease=lease,
            user_id=user_id,
            method=ConsentMethod.CONTRACT_CLAUSE.value,
            source="excel_import",
        )
    record_event(db, org_id, "import.completed", user_id=user_id, **report.created)
