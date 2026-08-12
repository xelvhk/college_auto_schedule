from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rasp.application.imports import (
    validate_and_activate_workbook,
    validate_curriculum_readiness,
)
from rasp.application.readiness import ReadinessSeverity
from rasp.domain.models import ImportBatch
from rasp.imports.excel import (
    MAX_FILE_SIZE,
    ImportValidationError,
    read_import_workbook,
)
from rasp.storage.sqlite import (
    SqliteImportRepository,
    StorageError,
    VersionNotFoundError,
)


STATIC_DIR = Path(__file__).parent / "static"
UPLOAD_CHUNK_SIZE = 1024 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _counts(batch: ImportBatch | None) -> dict[str, int]:
    return {
        "teachers": len(batch.teachers) if batch else 0,
        "groups": len(batch.groups) if batch else 0,
        "workloads": len(batch.workloads) if batch else 0,
        "specialties": len(batch.specialties) if batch else 0,
        "curricula": len(batch.curricula) if batch else 0,
        "disciplines": len(batch.disciplines) if batch else 0,
        "students": len(batch.students) if batch else 0,
    }


def _readiness_warnings(batch: ImportBatch) -> list[dict[str, object]]:
    if not batch.curricula:
        return []
    report = validate_curriculum_readiness(batch)
    return [
        {
            "code": issue.code,
            "message": issue.message,
            "groupCode": issue.group_code,
            "curriculumCode": issue.curriculum_code,
            "disciplineCode": issue.discipline_code,
            "differenceHours": issue.difference_hours,
        }
        for issue in report.issues
        if issue.severity is ReadinessSeverity.WARNING
    ]


def _student_changes(
    batch: ImportBatch,
    active_batch: ImportBatch | None,
) -> dict[str, int]:
    incoming = {student.student_code: student for student in batch.students}
    active = {
        student.student_code: student
        for student in (active_batch.students if active_batch else ())
    }
    return {
        "created": len(incoming.keys() - active.keys()),
        "updated": sum(
            incoming[code] != active[code] for code in incoming.keys() & active.keys()
        ),
        "deactivated": len(active.keys() - incoming.keys()),
    }


def _issues_payload(error: ImportValidationError) -> dict[str, object]:
    return {
        "valid": False,
        "issues": [
            {
                "section": issue.section,
                "row": issue.row,
                "column": issue.column,
                "code": issue.code,
                "message": issue.message,
            }
            for issue in error.issues
        ],
    }


@asynccontextmanager
async def _staged_upload(upload: UploadFile) -> AsyncIterator[Path]:
    original_name = upload.filename or ""
    if Path(original_name).suffix.lower() != ".xlsx":
        raise ApiError(422, "invalid_extension", "Выберите файл формата .xlsx")
    if upload.size is not None and upload.size > MAX_FILE_SIZE:
        raise ApiError(413, "file_too_large", "Размер файла превышает 10 МиБ")

    with tempfile.TemporaryDirectory(prefix="rasp-upload-") as directory:
        staged_path = Path(directory) / "upload.xlsx"
        total_size = 0
        try:
            with staged_path.open("wb") as destination:
                while chunk := await upload.read(UPLOAD_CHUNK_SIZE):
                    total_size += len(chunk)
                    if total_size > MAX_FILE_SIZE:
                        raise ApiError(
                            413, "file_too_large", "Размер файла превышает 10 МиБ"
                        )
                    destination.write(chunk)
            yield staged_path
        finally:
            await upload.close()


def _status_payload(repository: SqliteImportRepository) -> dict[str, object]:
    repository.initialize()
    versions = repository.list_versions()
    batch = repository.get_active_batch()
    active = next((version for version in versions if version.is_active), None)
    return {
        "activeVersionId": active.version_id if active else None,
        "counts": _counts(batch),
        "versions": [
            {
                "versionId": version.version_id,
                "sourceName": version.source_name,
                "createdAt": version.created_at,
                "isActive": version.is_active,
            }
            for version in versions
        ],
    }


def create_app(database_path: str | Path | None = None) -> FastAPI:
    resolved_database = Path(
        database_path
        if database_path is not None
        else os.environ.get("RASP_DATABASE_PATH", "data/rasp.sqlite3")
    )
    repository = SqliteImportRepository(resolved_database)
    app = FastAPI(
        title="College Timetable",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, error: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status_code,
            content={
                "valid": False,
                "error": {"code": error.code, "message": error.message},
            },
        )

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).hostname not in LOOPBACK_HOSTS:
                return JSONResponse(
                    status_code=403,
                    content={
                        "valid": False,
                        "error": {
                            "code": "cross_site_request",
                            "message": "Запрос разрешен только из локального интерфейса",
                        },
                    },
                )
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/status")
    def status() -> JSONResponse:
        try:
            return JSONResponse(_status_payload(repository))
        except StorageError as error:
            raise ApiError(500, "storage_error", str(error)) from error

    @app.post("/api/imports/preview")
    async def preview(file: UploadFile) -> JSONResponse:
        async with _staged_upload(file) as staged_path:
            try:
                batch = read_import_workbook(staged_path)
                validate_curriculum_readiness(batch)
            except ImportValidationError as error:
                return JSONResponse(status_code=422, content=_issues_payload(error))
        try:
            if repository.database_path.exists():
                repository.initialize()
                active_batch = repository.get_active_batch()
            else:
                active_batch = None
        except StorageError as error:
            raise ApiError(500, "storage_error", str(error)) from error
        return JSONResponse(
            {
                "valid": True,
                "fileName": Path(file.filename or "import.xlsx").name,
                "counts": _counts(batch),
                "studentChanges": _student_changes(batch, active_batch),
                "warnings": _readiness_warnings(batch),
                "samples": {
                    "teachers": [
                        {
                            "teacherCode": item.teacher_code,
                            "fullName": item.full_name,
                            "department": item.department,
                        }
                        for item in batch.teachers[:5]
                    ],
                    "groups": [
                        {
                            "groupCode": item.group_code,
                            "course": item.course,
                            "headcount": item.headcount,
                        }
                        for item in batch.groups[:5]
                    ],
                    "workloads": [
                        {
                            "disciplineName": item.discipline_name,
                            "groupCode": item.group_code,
                            "teacherCode": item.teacher_code,
                            "hours": item.total_academic_hours,
                        }
                        for item in batch.workloads[:5]
                    ],
                    "specialties": [
                        {
                            "specialtyCode": item.specialty_code,
                            "specialtyName": item.specialty_name,
                        }
                        for item in batch.specialties[:5]
                    ],
                    "curricula": [
                        {
                            "curriculumCode": item.curriculum_code,
                            "specialtyCode": item.specialty_code,
                            "version": item.version,
                        }
                        for item in batch.curricula[:5]
                    ],
                    "disciplines": [
                        {
                            "disciplineCode": item.discipline_code,
                            "disciplineName": item.discipline_name,
                            "plannedHours": item.planned_hours,
                        }
                        for item in batch.disciplines[:5]
                    ],
                    "students": [
                        {
                            "studentCode": item.student_code,
                            "fullName": item.full_name,
                            "groupCode": item.group_code,
                            "status": item.status.value,
                        }
                        for item in batch.students[:5]
                    ],
                },
            }
        )

    @app.post("/api/imports/activate", status_code=201)
    async def activate(file: UploadFile) -> JSONResponse:
        async with _staged_upload(file) as staged_path:
            try:
                receipt = validate_and_activate_workbook(staged_path, repository)
            except ImportValidationError as error:
                return JSONResponse(status_code=422, content=_issues_payload(error))
            except StorageError as error:
                raise ApiError(500, "storage_error", str(error)) from error
        return JSONResponse(
            status_code=201,
            content={
                "valid": True,
                "versionId": receipt.version_id,
                "createdAt": receipt.created_at,
                "reused": receipt.reused,
                "counts": {
                    "teachers": receipt.teacher_count,
                    "groups": receipt.group_count,
                    "workloads": receipt.workload_count,
                    "specialties": receipt.specialty_count,
                    "curricula": receipt.curriculum_count,
                    "disciplines": receipt.discipline_count,
                    "students": receipt.student_count,
                },
            },
        )

    @app.post("/api/versions/{version_id}/activate")
    def activate_version(version_id: int) -> JSONResponse:
        try:
            repository.initialize()
            version = repository.activate_version(version_id)
        except VersionNotFoundError as error:
            raise ApiError(404, "version_not_found", str(error)) from error
        except (ValueError, StorageError) as error:
            raise ApiError(500, "storage_error", str(error)) from error
        return JSONResponse(
            {
                "valid": True,
                "activeVersionId": version.version_id,
                "sourceName": version.source_name,
                "createdAt": version.created_at,
            }
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
