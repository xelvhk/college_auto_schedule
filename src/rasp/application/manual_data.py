from __future__ import annotations

import json
from hashlib import sha256

from rasp.application.imports import validate_curriculum_readiness
from rasp.application.readiness import ReadinessSeverity, analyze_schedule_readiness
from rasp.domain.models import ImportBatch
from rasp.imports.excel import ImportIssue, ImportValidationError
from rasp.storage.sqlite import ImportReceipt, SqliteImportRepository


def manual_batch_fingerprint(batch: ImportBatch) -> str:
    """Return a stable fingerprint for a normalized manually entered batch."""

    encoded = json.dumps(
        batch.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def validate_and_activate_manual_batch(
    batch: ImportBatch,
    repository: SqliteImportRepository,
    *,
    source_name: str,
) -> ImportReceipt:
    """Validate a manual snapshot and atomically make it the active version."""

    validate_curriculum_readiness(batch)
    readiness = analyze_schedule_readiness(batch)
    blocking_issues = tuple(
        ImportIssue(
            section=issue.section or "manual_data",
            row=0,
            column=None,
            code=issue.code,
            message=issue.message,
        )
        for issue in readiness.issues
        if issue.severity is ReadinessSeverity.ERROR
    )
    if blocking_issues:
        raise ImportValidationError(blocking_issues)
    repository.initialize()
    return repository.activate_import(
        batch,
        source_name=source_name,
        source_sha256=manual_batch_fingerprint(batch),
    )
