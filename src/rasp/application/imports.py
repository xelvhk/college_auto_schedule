from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from rasp.application.readiness import (
    ReadinessReport,
    ReadinessSeverity,
    analyze_curriculum_alignment,
)
from rasp.domain.models import ImportBatch, ReferenceDataBatch
from rasp.imports.excel import (
    ImportIssue,
    ImportValidationError,
    preflight_import_workbook,
    read_import_workbook,
)
from rasp.storage.sqlite import ImportReceipt, SqliteImportRepository


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_curriculum_readiness(batch: ImportBatch) -> ReadinessReport:
    """Block activation on curriculum mismatches and retain warnings for preview."""

    if not batch.curricula:
        return ReadinessReport(issues=())
    report = analyze_curriculum_alignment(
        batch,
        ReferenceDataBatch(
            specialties=batch.specialties,
            curricula=batch.curricula,
            disciplines=batch.disciplines,
        ),
    )
    blocking = [
        ImportIssue(
            section="readiness",
            row=0,
            column=None,
            code=issue.code,
            message=issue.message,
        )
        for issue in report.issues
        if issue.severity is ReadinessSeverity.ERROR
    ]
    if blocking:
        raise ImportValidationError(blocking)
    return report


def validate_and_activate_workbook(
    workbook_path: str | Path,
    repository: SqliteImportRepository,
) -> ImportReceipt:
    """Validate a stable file snapshot, then atomically make it active."""

    source = Path(workbook_path)
    preflight_import_workbook(source)
    fingerprint_before = _sha256_file(source)
    batch = read_import_workbook(source)
    validate_curriculum_readiness(batch)
    fingerprint_after = _sha256_file(source)
    if fingerprint_before != fingerprint_after:
        raise ImportValidationError(
            [
                ImportIssue(
                    section="file",
                    row=0,
                    column=None,
                    code="file_changed",
                    message="File changed while it was being validated; retry the import",
                )
            ]
        )

    repository.initialize()
    return repository.activate_import(
        batch,
        source_name=source.name,
        source_sha256=fingerprint_after,
    )
