from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, TypeVar
from zipfile import BadZipFile, ZipFile, is_zipfile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import BaseModel, ValidationError

from rasp.domain.models import (
    Group,
    ImportBatch,
    ReferenceDataBatch,
    Teacher,
    WorkloadItem,
)
from rasp.imports.reference_data import (
    ReferenceDataValidationError,
    build_reference_data_batch,
)


@dataclass(frozen=True, slots=True)
class ImportIssue:
    section: str
    row: int
    column: str | None
    code: str
    message: str


class ImportValidationError(ValueError):
    def __init__(self, issues: Iterable[ImportIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"Import contains {len(self.issues)} validation issue(s)")


ModelT = TypeVar("ModelT", bound=BaseModel)

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1_000
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
MAX_DATA_ROWS_PER_SHEET = 100_000

CORE_SHEETS = {
    "Преподаватели": "teachers",
    "Группы": "groups",
    "Нагрузка": "workloads",
}
REFERENCE_SHEETS = {
    "Специальности": "specialties",
    "Учебные планы": "curricula",
    "Дисциплины": "disciplines",
}
SHEETS = CORE_SHEETS | REFERENCE_SHEETS
REQUIRED_HEADERS = {
    "Преподаватели": {"teacher_code", "full_name", "yearly_assigned_hours"},
    "Группы": {"group_code", "course", "headcount"},
    "Нагрузка": {
        "workload_row_code",
        "academic_year",
        "semester",
        "discipline_code",
        "discipline_name",
        "group_code",
        "teacher_code",
        "lesson_type",
        "total_academic_hours",
        "event_duration_hours",
    },
    "Специальности": {
        "specialty_code",
        "specialty_name",
        "program_base",
        "education_form",
    },
    "Учебные планы": {
        "curriculum_code",
        "specialty_code",
        "admission_year",
        "version",
        "valid_from",
        "status",
    },
    "Дисциплины": {
        "curriculum_code",
        "discipline_code",
        "discipline_name",
        "semester",
        "lesson_type",
        "planned_hours",
    },
}


def _parse_rows(
    section: str,
    rows: Iterable[Mapping[str, Any]],
    model: type[ModelT],
) -> tuple[list[ModelT], list[ImportIssue]]:
    parsed: list[ModelT] = []
    issues: list[ImportIssue] = []

    for row_number, row in enumerate(rows, start=2):
        try:
            parsed.append(model.model_validate(dict(row)))
        except ValidationError as error:
            for detail in error.errors(include_url=False, include_input=False):
                location = detail.get("loc", ())
                issues.append(
                    ImportIssue(
                        section=section,
                        row=row_number,
                        column=str(location[0]) if location else None,
                        code="invalid_value",
                        message=str(detail["msg"]),
                    )
                )

    return parsed, issues


def _duplicate_issues(
    section: str,
    records: Iterable[BaseModel],
    field: str,
) -> list[ImportIssue]:
    issues: list[ImportIssue] = []
    seen: set[str] = set()
    for row_number, record in enumerate(records, start=2):
        code = str(getattr(record, field))
        if code in seen:
            issues.append(
                ImportIssue(
                    section=section,
                    row=row_number,
                    column=field,
                    code="duplicate_code",
                    message=f"Duplicate stable code: {code}",
                )
            )
        seen.add(code)
    return issues


def build_import_batch(
    *,
    teacher_rows: Iterable[Mapping[str, Any]],
    group_rows: Iterable[Mapping[str, Any]],
    workload_rows: Iterable[Mapping[str, Any]],
    specialty_rows: Iterable[Mapping[str, Any]] | None = None,
    curriculum_rows: Iterable[Mapping[str, Any]] | None = None,
    discipline_rows: Iterable[Mapping[str, Any]] | None = None,
) -> ImportBatch:
    """Validate all rows and return a complete batch or no batch at all."""

    teachers, issues = _parse_rows("teachers", teacher_rows, Teacher)
    groups, group_issues = _parse_rows("groups", group_rows, Group)
    workloads, workload_issues = _parse_rows(
        "workloads", workload_rows, WorkloadItem
    )
    issues.extend(group_issues)
    issues.extend(workload_issues)

    issues.extend(_duplicate_issues("teachers", teachers, "teacher_code"))
    issues.extend(_duplicate_issues("groups", groups, "group_code"))
    issues.extend(
        _duplicate_issues("workloads", workloads, "workload_row_code")
    )

    teacher_codes = {teacher.teacher_code for teacher in teachers}
    group_codes = {group.group_code for group in groups}
    for row_number, workload in enumerate(workloads, start=2):
        if workload.teacher_code not in teacher_codes:
            issues.append(
                ImportIssue(
                    section="workloads",
                    row=row_number,
                    column="teacher_code",
                    code="unknown_teacher",
                    message=f"Unknown teacher code: {workload.teacher_code}",
                )
            )
        if workload.group_code not in group_codes:
            issues.append(
                ImportIssue(
                    section="workloads",
                    row=row_number,
                    column="group_code",
                    code="unknown_group",
                    message=f"Unknown group code: {workload.group_code}",
                )
            )

    references = ReferenceDataBatch(specialties=(), curricula=(), disciplines=())
    reference_inputs = (specialty_rows, curriculum_rows, discipline_rows)
    if any(rows is not None for rows in reference_inputs):
        if not all(rows is not None for rows in reference_inputs):
            issues.append(
                ImportIssue(
                    section="file",
                    row=0,
                    column=None,
                    code="incomplete_reference_data",
                    message="All curriculum reference sections must be provided together",
                )
            )
        else:
            try:
                references = build_reference_data_batch(
                    specialty_rows=specialty_rows or (),
                    curriculum_rows=curriculum_rows or (),
                    discipline_rows=discipline_rows or (),
                )
            except ReferenceDataValidationError as error:
                issues.extend(
                    ImportIssue(
                        section=issue.section,
                        row=issue.row,
                        column=issue.column,
                        code=issue.code,
                        message=issue.message,
                    )
                    for issue in error.issues
                )

    operational_batch = ImportBatch(
        teachers=tuple(teachers),
        groups=tuple(groups),
        workloads=tuple(workloads),
    )
    if issues:
        raise ImportValidationError(issues)

    return operational_batch.model_copy(
        update={
            "specialties": references.specialties,
            "curricula": references.curricula,
            "disciplines": references.disciplines,
        }
    )


def preflight_import_workbook(path: str | Path) -> None:
    path = Path(path)
    issues: list[ImportIssue] = []
    if path.suffix.lower() != ".xlsx":
        issues.append(
            ImportIssue("file", 0, None, "invalid_extension", "Expected .xlsx file")
        )
    if not path.is_file():
        issues.append(
            ImportIssue("file", 0, None, "file_not_found", "File does not exist")
        )
    elif path.stat().st_size > MAX_FILE_SIZE:
        issues.append(
            ImportIssue("file", 0, None, "file_too_large", "File exceeds 10 MiB")
        )
    if issues:
        raise ImportValidationError(issues)
    if not is_zipfile(path):
        raise ImportValidationError(
            [ImportIssue("file", 0, None, "invalid_xlsx", "Invalid XLSX container")]
        )

    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                issues.append(
                    ImportIssue(
                        "file", 0, None, "archive_too_large", "Too many ZIP entries"
                    )
                )

            total_size = 0
            for entry in entries:
                archive_path = PurePosixPath(entry.filename)
                if archive_path.is_absolute() or ".." in archive_path.parts:
                    issues.append(
                        ImportIssue(
                            "file",
                            0,
                            None,
                            "unsafe_archive_path",
                            "Unsafe path inside XLSX container",
                        )
                    )
                if entry.flag_bits & 0x1:
                    issues.append(
                        ImportIssue(
                            "file",
                            0,
                            None,
                            "encrypted_content",
                            "Encrypted XLSX content is not supported",
                        )
                    )
                total_size += entry.file_size
                ratio = entry.file_size / max(entry.compress_size, 1)
                if ratio > MAX_COMPRESSION_RATIO:
                    issues.append(
                        ImportIssue(
                            "file",
                            0,
                            None,
                            "suspicious_compression",
                            "Suspicious ZIP compression ratio",
                        )
                    )
                normalized_name = entry.filename.lower()
                if (
                    "vbaproject.bin" in normalized_name
                    or normalized_name.startswith("xl/externallinks/")
                    or normalized_name.startswith("xl/embeddings/")
                ):
                    issues.append(
                        ImportIssue(
                            "file",
                            0,
                            None,
                            "active_content",
                            "Macros, external links, and embedded objects are forbidden",
                        )
                    )
            if total_size > MAX_UNCOMPRESSED_SIZE:
                issues.append(
                    ImportIssue(
                        "file",
                        0,
                        None,
                        "archive_too_large",
                        "Uncompressed XLSX content exceeds 50 MiB",
                    )
                )
    except BadZipFile as error:
        raise ImportValidationError(
            [ImportIssue("file", 0, None, "invalid_xlsx", "Invalid XLSX container")]
        ) from error

    if issues:
        raise ImportValidationError(issues)


def _read_sheet_rows(worksheet: Any) -> list[dict[str, Any]]:
    issues: list[ImportIssue] = []
    row_iterator = worksheet.iter_rows(values_only=False)
    header_cells = next(row_iterator, None)
    if header_cells is None:
        raise ImportValidationError(
            [
                ImportIssue(
                    worksheet.title, 1, None, "empty_sheet", "Sheet is empty"
                )
            ]
        )

    headers: list[str] = []
    for cell in header_cells:
        if cell.data_type == "f":
            issues.append(
                ImportIssue(
                    worksheet.title,
                    1,
                    cell.coordinate,
                    "formula_forbidden",
                    "Formulas are forbidden in import files",
                )
            )
        value = cell.value
        headers.append(str(value).strip() if value is not None else "")

    while headers and not headers[-1]:
        headers.pop()
    if not headers or any(not header for header in headers):
        issues.append(
            ImportIssue(
                worksheet.title,
                1,
                None,
                "invalid_header",
                "Header cells must be non-empty",
            )
        )
    duplicate_headers = {header for header in headers if headers.count(header) > 1}
    if duplicate_headers:
        issues.append(
            ImportIssue(
                worksheet.title,
                1,
                None,
                "duplicate_header",
                f"Duplicate headers: {', '.join(sorted(duplicate_headers))}",
            )
        )
    missing = REQUIRED_HEADERS[worksheet.title] - set(headers)
    if missing:
        issues.append(
            ImportIssue(
                worksheet.title,
                1,
                None,
                "missing_header",
                f"Missing headers: {', '.join(sorted(missing))}",
            )
        )

    rows: list[dict[str, Any]] = []
    for row_number, cells in enumerate(row_iterator, start=2):
        if row_number > MAX_DATA_ROWS_PER_SHEET + 1:
            issues.append(
                ImportIssue(
                    worksheet.title,
                    row_number,
                    None,
                    "too_many_rows",
                    "Sheet exceeds 100000 data rows",
                )
            )
            break
        values = []
        for cell in cells[: len(headers)]:
            if cell.data_type == "f":
                issues.append(
                    ImportIssue(
                        worksheet.title,
                        row_number,
                        cell.coordinate,
                        "formula_forbidden",
                        "Formulas are forbidden in import files",
                    )
                )
            values.append(cell.value)
        values.extend([None] * (len(headers) - len(values)))
        if any(value is not None and value != "" for value in values):
            rows.append(dict(zip(headers, values, strict=True)))

    if issues:
        raise ImportValidationError(issues)
    return rows


def read_import_workbook(path: str | Path) -> ImportBatch:
    """Read the required sheets and return a fully validated, in-memory batch."""

    source = Path(path)
    preflight_import_workbook(source)
    try:
        workbook = load_workbook(
            source,
            read_only=True,
            data_only=False,
            keep_links=False,
        )
    except (InvalidFileException, OSError, ValueError) as error:
        raise ImportValidationError(
            [ImportIssue("file", 0, None, "invalid_xlsx", "Cannot read XLSX file")]
        ) from error

    try:
        available_sheets = set(workbook.sheetnames)
        missing_sheets = set(CORE_SHEETS) - available_sheets
        reference_sheets_present = set(REFERENCE_SHEETS) & available_sheets
        if reference_sheets_present and reference_sheets_present != set(
            REFERENCE_SHEETS
        ):
            missing_sheets |= set(REFERENCE_SHEETS) - available_sheets
        if missing_sheets:
            raise ImportValidationError(
                [
                    ImportIssue(
                        "file",
                        0,
                        None,
                        "missing_sheet",
                        f"Missing sheets: {', '.join(sorted(missing_sheets))}",
                    )
                ]
            )

        rows: dict[str, list[dict[str, Any]]] = {}
        sheet_issues: list[ImportIssue] = []
        selected_sheets = dict(CORE_SHEETS)
        if reference_sheets_present:
            selected_sheets.update(REFERENCE_SHEETS)
        for sheet_name, section in selected_sheets.items():
            try:
                rows[section] = _read_sheet_rows(workbook[sheet_name])
            except ImportValidationError as error:
                sheet_issues.extend(error.issues)
        if sheet_issues:
            raise ImportValidationError(sheet_issues)

        return build_import_batch(
            teacher_rows=rows["teachers"],
            group_rows=rows["groups"],
            workload_rows=rows["workloads"],
            specialty_rows=rows.get("specialties"),
            curriculum_rows=rows.get("curricula"),
            discipline_rows=rows.get("disciplines"),
        )
    finally:
        workbook.close()
