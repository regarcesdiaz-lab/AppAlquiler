"""Payment history, Excel export (formula injection) and the Excel import."""

import datetime as dt
from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ConsentRecord, Lease, Payment, Tenant, TimelineEvent, Unit
from app.models.enums import PaymentSource, PaymentStatus
from app.services.exports import IMPORT_COLUMNS, XLSX_MEDIA_TYPE, safe_cell_text
from tests.api_utils import make_owner, make_portfolio, ok
from tests.factories import add

# -- history -----------------------------------------------------------------------------------


def _payments(db: Session, lease: Lease, count: int) -> None:
    for i in range(count):
        add(
            db,
            Payment(
                organization_id=lease.organization_id,
                lease_id=lease.id,
                amount=Decimal("100.00") + i,
                paid_on=dt.date(2026, 1, 1) + dt.timedelta(days=i),
                status=PaymentStatus.REJECTED,
                source=PaymentSource.MANUAL_CASH,
            ),
        )


def test_history_filters_and_cursor(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    _payments(db_session, p.lease, 5)
    page = ok(client, "GET", "/v1/payments?limit=4", owner.headers).json()
    assert len(page["items"]) == 4 and page["next_cursor"]
    rest = ok(
        client, "GET", f"/v1/payments?limit=4&cursor={page['next_cursor']}", owner.headers
    ).json()
    assert len(rest["items"]) == 2 and rest["next_cursor"] is None
    dates = [i["paid_on"] for i in page["items"] + rest["items"]]
    assert dates == sorted(dates, reverse=True)

    reported = ok(client, "GET", "/v1/payments?status=reported", owner.headers).json()["items"]
    assert [i["id"] for i in reported] == [str(p.payment.id)]
    assert reported[0]["tenant_name"] == "María Fernández" and reported[0]["has_receipt_file"]
    ranged = ok(
        client, "GET", "/v1/payments?date_from=2026-01-02&date_to=2026-01-03", owner.headers
    ).json()["items"]
    assert len(ranged) == 2


# -- export ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1+1", "'+1+1"),
        ("-2", "'-2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("\rcmd", "'\rcmd"),
        ("María", "María"),
        ("", ""),
        (None, None),
    ],
)
def test_safe_cell_text(value, expected) -> None:
    assert safe_cell_text(value) == expected


def test_export_escapes_formulas_and_writes_exact_numbers(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    p = make_portfolio(db_session, owner.org)
    p.tenant.full_name = '=HYPERLINK("http://malo.example","Clic")'
    p.payment.reference = "+cmd|' /C calc'!A0"
    db_session.flush()

    response = ok(client, "GET", "/v1/payments/export.xlsx", owner.headers)
    assert response.headers["content-type"] == XLSX_MEDIA_TYPE
    assert "attachment" in response.headers["content-disposition"]
    sheet = load_workbook(BytesIO(response.content)).active
    assert [c.value for c in sheet[1]][:5] == [
        "Fecha de pago",
        "Inquilino",
        "Edificio",
        "Apartamento",
        "Monto",
    ]
    tenant_cell = sheet.cell(2, 2)
    assert tenant_cell.data_type == "s"
    assert tenant_cell.value.startswith("'=")
    assert sheet.cell(2, 9).data_type == "s" and sheet.cell(2, 9).value.startswith("'+")
    assert sheet.cell(2, 5).value == 25000
    assert sheet.cell(2, 6).value == "Por confirmar"
    # No cell anywhere is stored as a formula.
    assert all(c.data_type != "f" for row in sheet.iter_rows() for c in row)


# -- import ------------------------------------------------------------------------------------


def _xlsx(rows: list[list], headers: list[str] | None = None) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Contratos"
    sheet.append(headers or [title for _, title in IMPORT_COLUMNS])
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


GOOD_ROW = [
    "Residencial Sol",
    "1A",
    "Calle 1",
    "Ana Peña",
    "809-555-0201",
    "ana@example.com",
    25000,
    5,
    50000,
    dt.date(2026, 1, 1),
    None,
    "Sí",
    "Sí",
]


def _upload(client: TestClient, headers: dict, content: bytes, dry_run: bool, name="datos.xlsx"):
    return client.post(
        f"/v1/imports?dry_run={str(dry_run).lower()}",
        headers=headers,
        files={"file": (name, content, XLSX_MEDIA_TYPE)},
    )


def test_template_downloads_with_the_expected_headers(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    response = ok(client, "GET", "/v1/imports/template.xlsx", owner.headers)
    workbook = load_workbook(BytesIO(response.content))
    assert workbook.sheetnames == ["Contratos", "Instrucciones"]
    assert [c.value for c in workbook["Contratos"][1]] == [t for _, t in IMPORT_COLUMNS]
    assert client.get("/v1/imports/template.xlsx").status_code == 401


def test_import_dry_run_then_commit(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    second = [*GOOD_ROW]
    second[1], second[3], second[4] = "1B", "Luis Castillo", "305 555 0202"
    second[8] = 75000  # 3 months of rent: warning only
    content = _xlsx([GOOD_ROW, second])

    dry = _upload(client, owner.headers, content, dry_run=True).json()
    assert dry["errors"] == [] and dry["rows_ok"] == 2 and dry["imported"] is False
    assert {w["code"] for w in dry["warnings"]} == {
        "phone_not_do_area_code",
        "deposit_above_legal_max",
    }
    assert db_session.scalar(select(func.count()).select_from(Lease)) == 0

    done = _upload(client, owner.headers, content, dry_run=False).json()
    assert done["imported"] is True
    assert done["created"] == {"buildings": 1, "units": 2, "tenants": 2, "leases": 2}
    org = owner.org.id
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ConsentRecord)
            .where(ConsentRecord.organization_id == org)
        )
        == 2
    )
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(TimelineEvent)
            .where(TimelineEvent.event_type == "import.completed")
        )
        == 1
    )
    # Importing the same file again: both units already have an active lease.
    again = _upload(client, owner.headers, content, dry_run=False).json()
    assert again["imported"] is False and len(again["errors"]) == 2


def test_import_is_all_or_nothing_with_errors_by_row_and_column(
    client: TestClient, db_session: Session
) -> None:
    owner = make_owner(db_session)
    bad = [*GOOD_ROW]
    bad[1] = "2A"
    bad[4] = "555"  # phone
    bad[6] = "mucho"  # rent
    bad[7] = 40  # due day
    bad[12] = "No"  # consent is mandatory
    duplicate = [*GOOD_ROW]
    report = _upload(client, owner.headers, _xlsx([GOOD_ROW, bad, duplicate]), dry_run=False).json()
    assert report["imported"] is False and report["rows_total"] == 3 and report["rows_ok"] == 1
    by_cell = {(e["row"], e["column"]) for e in report["errors"]}
    assert {
        (3, "Teléfono"),
        (3, "Renta"),
        (3, "Día de pago"),
        (3, "Consentimiento WhatsApp"),
        (4, "Apartamento"),
    } == by_cell
    assert db_session.scalar(select(func.count()).select_from(Unit)) == 0
    assert db_session.scalar(select(func.count()).select_from(Tenant)) == 0


def test_import_rejects_wrong_files(client: TestClient, db_session: Session) -> None:
    owner = make_owner(db_session)
    assert _upload(client, owner.headers, b"a,b", True, name="datos.csv").status_code == 422
    assert (
        _upload(client, owner.headers, b"not a zip", True).json()["detail"]["code"]
        == "unreadable_file"
    )
    missing = _upload(client, owner.headers, _xlsx([], headers=["Apartamento"]), True).json()
    assert "Faltan columnas" in missing["errors"][0]["message"]
    big = _upload(client, owner.headers, b"0" * (2 * 1024 * 1024 + 10), True)
    assert big.status_code == 413
    too_many = _xlsx([[*GOOD_ROW[:1], f"U{i}", *GOOD_ROW[2:]] for i in range(501)])
    assert (
        "más de 500 filas"
        in _upload(client, owner.headers, too_many, True).json()["errors"][0]["message"]
    )
