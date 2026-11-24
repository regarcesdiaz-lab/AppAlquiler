"""Excel files the owner downloads: payment history and the import template.

Formula injection: openpyxl stores any string that starts with "=" as a formula, and
spreadsheet apps also run cells starting with "+", "-", "@", tab or carriage return. Every text
cell goes through ``safe_cell_text``, which puts an apostrophe in front of those values.
"""

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.models.enums import PaymentSource, PaymentStatus

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
MONEY_FORMAT = '"RD$"#,##0.00'
DATE_FORMAT = "DD/MM/YYYY"
DATETIME_FORMAT = "DD/MM/YYYY HH:MM"

PAYMENT_STATUS_ES = {
    PaymentStatus.REPORTED: "Por confirmar",
    PaymentStatus.CONFIRMED: "Confirmado",
    PaymentStatus.REJECTED: "Rechazado",
}
PAYMENT_SOURCE_ES = {
    PaymentSource.WHATSAPP_RECEIPT: "Comprobante por WhatsApp",
    PaymentSource.MANUAL_CASH: "Efectivo",
    PaymentSource.CARD_LINK: "Enlace de pago",
}


def safe_cell_text(value: str | None) -> str | None:
    if value and value.startswith(DANGEROUS_PREFIXES):
        return "'" + value
    return value


def _autosize(sheet, widths: Iterable[int]) -> None:
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width


def _header(sheet, titles: list[str]) -> None:
    sheet.append([safe_cell_text(t) for t in titles])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"


def _to_bytes(workbook: Workbook) -> bytes:
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@dataclass(frozen=True)
class PaymentRow:
    paid_on: dt.date
    tenant_name: str
    building_name: str | None
    unit_code: str
    amount: Decimal
    status: PaymentStatus
    source: PaymentSource
    bank: str | None
    reference: str | None
    confirmed_at: dt.datetime | None


def payments_workbook(rows: Iterable[PaymentRow], zone: ZoneInfo) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Pagos"
    _header(
        sheet,
        [
            "Fecha de pago",
            "Inquilino",
            "Edificio",
            "Apartamento",
            "Monto",
            "Estado",
            "Origen",
            "Banco",
            "Referencia",
            "Confirmado el",
        ],
    )
    for row in rows:
        confirmed = (
            row.confirmed_at.astimezone(zone).replace(tzinfo=None) if row.confirmed_at else None
        )
        sheet.append(
            [
                row.paid_on,
                safe_cell_text(row.tenant_name),
                safe_cell_text(row.building_name),
                safe_cell_text(row.unit_code),
                row.amount,  # Decimal: written as an exact number
                PAYMENT_STATUS_ES[row.status],
                PAYMENT_SOURCE_ES[row.source],
                safe_cell_text(row.bank),
                safe_cell_text(row.reference),
                confirmed,
            ]
        )
        current = sheet.max_row
        sheet.cell(current, 1).number_format = DATE_FORMAT
        sheet.cell(current, 5).number_format = MONEY_FORMAT
        sheet.cell(current, 10).number_format = DATETIME_FORMAT
    _autosize(sheet, [14, 28, 22, 14, 14, 15, 26, 18, 18, 18])
    return _to_bytes(workbook)


# -- import template ---------------------------------------------------------------------------

IMPORT_COLUMNS: list[tuple[str, str]] = [
    ("building", "Edificio"),
    ("unit", "Apartamento"),
    ("address", "Dirección"),
    ("tenant_name", "Inquilino"),
    ("phone", "Teléfono"),
    ("email", "Correo"),
    ("rent", "Renta"),
    ("due_day", "Día de pago"),
    ("deposit", "Depósito"),
    ("start_date", "Fecha de inicio"),
    ("end_date", "Fecha de fin"),
    ("late_fee_agreed", "¿Pacta mora?"),
    ("consent", "Consentimiento WhatsApp"),
]
TEMPLATE_SHEET = "Contratos"

_INSTRUCTIONS = [
    "Cómo llenar la plantilla",
    "",
    "Una fila por contrato activo. No cambies los encabezados de la hoja Contratos.",
    "Obligatorios: Apartamento, Inquilino, Teléfono, Renta, Día de pago, Fecha de inicio, "
    "¿Pacta mora? y Consentimiento WhatsApp.",
    "Edificio: déjalo vacío si el apartamento no pertenece a un edificio.",
    "Teléfono: 809-555-0101 o +1 809 555 0101.",
    "Renta y Depósito: solo el número, sin RD$ (ejemplo: 25000).",
    "Día de pago: del 1 al 31. Si el mes es más corto, se cobra el último día.",
    "Fechas: DD/MM/AAAA.",
    "¿Pacta mora?: Sí si el contrato dice que se cobra mora por atraso; si no, No.",
    "Consentimiento WhatsApp: escribe Sí solo si el contrato incluye la cláusula en la que el "
    "inquilino acepta recibir mensajes por WhatsApp. Es obligatorio para importar.",
    "Si una fila tiene un error, no se importa nada y te decimos qué corregir.",
]


def import_template_workbook() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = TEMPLATE_SHEET
    _header(sheet, [title for _, title in IMPORT_COLUMNS])
    sheet.append(
        [
            "Residencial Las Palmas",
            "1A",
            "Calle Las Palmas 12, Naco",
            "María Fernández",
            "809-555-0101",
            "maria@example.com",
            25000,
            5,
            50000,
            dt.date(2026, 1, 1),
            dt.date(2026, 12, 31),
            "Sí",
            "Sí",
        ]
    )
    sheet.cell(2, 10).number_format = DATE_FORMAT
    sheet.cell(2, 11).number_format = DATE_FORMAT
    _autosize(sheet, [24, 12, 30, 24, 16, 24, 12, 11, 12, 15, 15, 13, 24])
    help_sheet = workbook.create_sheet("Instrucciones")
    for line in _INSTRUCTIONS:
        help_sheet.append([safe_cell_text(line)])
    help_sheet["A1"].font = Font(bold=True)
    help_sheet.column_dimensions["A"].width = 110
    return _to_bytes(workbook)
