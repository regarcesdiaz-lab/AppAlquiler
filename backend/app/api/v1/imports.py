"""Excel import: downloadable template and all-or-nothing upload with a per-row report."""

from fastapi import APIRouter, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.api.deps import CurrentAuth, DbSession
from app.api.errors import COMMON_RESPONSES, ErrorResponse, api_error
from app.services.exports import XLSX_MEDIA_TYPE, import_template_workbook
from app.services.imports import (
    MAX_FILE_BYTES,
    UnreadableFileError,
    apply_import,
    validate_import,
)

router = APIRouter(prefix="/imports", tags=["imports"], responses=COMMON_RESPONSES)


class ImportIssue(BaseModel):
    row: int
    column: str | None
    message: str
    code: str | None = None


class ImportCounts(BaseModel):
    buildings: int
    units: int
    tenants: int
    leases: int


class ImportResult(BaseModel):
    dry_run: bool
    imported: bool
    rows_total: int
    rows_ok: int
    created: ImportCounts
    errors: list[ImportIssue]
    warnings: list[ImportIssue]


@router.get(
    "/template.xlsx",
    operation_id="downloadImportTemplate",
    response_class=Response,
    responses={200: {"content": {XLSX_MEDIA_TYPE: {}}, "description": "Excel template"}},
)
def download_template(auth: CurrentAuth) -> Response:
    del auth  # signed-in owners only
    return Response(
        content=import_template_workbook(),
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": 'attachment; filename="plantilla-contratos.xlsx"'},
    )


@router.post(
    "",
    response_model=ImportResult,
    operation_id="importLeases",
    responses={413: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
)
def import_leases(
    file: UploadFile, auth: CurrentAuth, db: DbSession, dry_run: bool = True
) -> ImportResult:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise api_error(422, "not_xlsx", "Sube el archivo en formato Excel (.xlsx).")
    # Sync endpoint (runs in the threadpool): parsing and the database work are blocking.
    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise api_error(
            413,
            "file_too_large",
            "El archivo pasa de 2 MB. Divídelo en partes.",
        )
    try:
        report = validate_import(db, auth.org_id, content)
    except UnreadableFileError:
        raise api_error(
            422, "unreadable_file", "No pudimos leer el archivo. Usa la plantilla en .xlsx."
        ) from None
    imported = False
    if not report.errors and not dry_run:
        apply_import(db, auth.org_id, report, auth.user.id)
        db.commit()
        imported = True
    return ImportResult(
        dry_run=dry_run,
        imported=imported,
        rows_total=report.rows_total,
        rows_ok=report.rows_total - len({e.row for e in report.errors if e.row >= 2}),
        created=ImportCounts(**report.created),
        errors=[ImportIssue(**vars(e)) for e in report.errors],
        warnings=[ImportIssue(**vars(w)) for w in report.warnings],
    )
