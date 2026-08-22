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
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from rasp.application.imports import (
    validate_activation_invariants,
    validate_and_activate_workbook,
    validate_curriculum_readiness,
)
from rasp.application.manual_data import validate_and_activate_manual_batch
from rasp.application.readiness import (
    ReadinessIssue,
    ReadinessSeverity,
    analyze_room_supply,
    analyze_schedule_readiness,
)
from rasp.domain.models import ImportBatch
from rasp.imports.excel import (
    MAX_FILE_SIZE,
    ImportValidationError,
    read_import_workbook,
)
from rasp.solver import (
    MAX_SOLVER_SEED,
    SolverMode,
    SolverOptions,
    build_solver_problem,
    solver_problem_payload,
    solver_result_payload,
    solve_schedule_batch,
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


class SolverRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    mode: SolverMode = SolverMode.COMPLETE
    seed: int = Field(default=0, ge=0, le=MAX_SOLVER_SEED)
    time_limit_seconds: int = Field(
        default=30,
        ge=1,
        le=300,
        alias="timeLimitSeconds",
    )


class ManualDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    batch: ImportBatch
    source_name: str = Field(
        default="Ручной ввод",
        min_length=1,
        max_length=255,
        alias="sourceName",
    )


def _counts(batch: ImportBatch | None) -> dict[str, int]:
    return {
        "teachers": len(batch.teachers) if batch else 0,
        "groups": len(batch.groups) if batch else 0,
        "workloads": len(batch.workloads) if batch else 0,
        "specialties": len(batch.specialties) if batch else 0,
        "curricula": len(batch.curricula) if batch else 0,
        "disciplines": len(batch.disciplines) if batch else 0,
        "students": len(batch.students) if batch else 0,
        "buildings": len(batch.buildings) if batch else 0,
        "roomTypes": len(batch.room_types) if batch else 0,
        "equipment": len(batch.equipment) if batch else 0,
        "rooms": len(batch.rooms) if batch else 0,
        "academicYears": len(batch.academic_years) if batch else 0,
        "academicCycles": len(batch.academic_cycles) if batch else 0,
        "cycleCommissions": len(batch.cycle_commissions) if batch else 0,
        "teacherReplacements": len(batch.teacher_replacements) if batch else 0,
        "calendarPeriods": len(batch.calendar_periods) if batch else 0,
        "bellSlots": len(batch.bell_slots) if batch else 0,
        "calendarExceptions": len(batch.calendar_exceptions) if batch else 0,
        "resourceUnavailability": len(batch.resource_unavailability) if batch else 0,
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


def _readiness_issue_payload(issue: ReadinessIssue) -> dict[str, object]:
    return {
        "severity": issue.severity.value,
        "code": issue.code,
        "message": issue.message,
        "section": issue.section,
        "objectCode": issue.object_code,
        "groupCode": issue.group_code,
        "curriculumCode": issue.curriculum_code,
        "disciplineCode": issue.discipline_code,
        "semester": issue.semester,
        "lessonType": issue.lesson_type.value if issue.lesson_type else None,
        "differenceHours": issue.difference_hours,
        "remediation": issue.remediation,
    }


def _readiness_payload(batch: ImportBatch) -> dict[str, object]:
    report = analyze_schedule_readiness(batch)
    return {
        "isReady": report.is_ready,
        "errorCount": report.error_count,
        "warningCount": report.warning_count,
        "issues": [_readiness_issue_payload(issue) for issue in report.issues],
    }


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

    @app.get("/api/readiness")
    def readiness() -> JSONResponse:
        try:
            repository.initialize()
            batch = repository.get_active_batch()
        except StorageError as error:
            raise ApiError(500, "storage_error", str(error)) from error
        if batch is None:
            raise ApiError(409, "no_active_import", "Нет активной версии данных")
        return JSONResponse(_readiness_payload(batch))

    @app.get("/api/solver/problem")
    def solver_problem() -> JSONResponse:
        try:
            repository.initialize()
            batch = repository.get_active_batch()
        except StorageError as error:
            raise ApiError(500, "storage_error", str(error)) from error
        if batch is None:
            raise ApiError(409, "no_active_import", "Нет активной версии данных")
        return JSONResponse(solver_problem_payload(build_solver_problem(batch)))

    @app.post("/api/solver/runs")
    def run_solver(request: SolverRunRequest) -> JSONResponse:
        try:
            repository.initialize()
            batch = repository.get_active_batch()
        except StorageError as error:
            raise ApiError(500, "storage_error", str(error)) from error
        if batch is None:
            raise ApiError(409, "no_active_import", "Нет активной версии данных")
        try:
            problem, result = solve_schedule_batch(
                batch,
                SolverOptions(
                    mode=request.mode,
                    seed=request.seed,
                    time_limit_seconds=request.time_limit_seconds,
                ),
            )
        except Exception as error:
            error_type = type(error).__name__
            raise ApiError(
                500,
                "solver_engine_error",
                f"Не удалось запустить механизм расчёта ({error_type})",
            ) from error
        return JSONResponse(solver_result_payload(result, problem))

    @app.post("/api/imports/preview")
    async def preview(file: UploadFile) -> JSONResponse:
        async with _staged_upload(file) as staged_path:
            try:
                batch = read_import_workbook(staged_path)
                validate_activation_invariants(batch)
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
                "roomDeficits": [
                    {
                        "workloadRowCode": item.workload_row_code,
                        "groupCode": item.group_code,
                        "requiredRoomType": item.required_room_type,
                        "requiredCapacity": item.required_capacity,
                        "requiredEquipmentCodes": item.required_equipment_codes,
                    }
                    for item in analyze_room_supply(batch)
                ],
                "readiness": _readiness_payload(batch),
                "solverProblem": solver_problem_payload(build_solver_problem(batch)),
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
                    "rooms": [
                        {
                            "roomCode": item.room_code,
                            "roomName": item.room_name,
                            "buildingCode": item.building_code,
                            "roomTypeCode": item.room_type_code,
                            "capacity": item.capacity,
                        }
                        for item in batch.rooms[:5]
                    ],
                    "cycleCommissions": [
                        {
                            "commissionCode": item.commission_code,
                            "commissionName": item.commission_name,
                            "department": item.department,
                            "active": item.active,
                        }
                        for item in batch.cycle_commissions[:5]
                    ],
                    "teacherReplacements": [
                        {
                            "replacementCode": item.replacement_code,
                            "originalTeacherCode": item.original_teacher_code,
                            "substituteTeacherCode": item.substitute_teacher_code,
                            "startsOn": item.starts_on.isoformat(),
                            "endsOn": item.ends_on.isoformat(),
                            "workloadRowCode": item.workload_row_code,
                        }
                        for item in batch.teacher_replacements[:5]
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
                    "buildings": receipt.building_count,
                    "roomTypes": receipt.room_type_count,
                    "equipment": receipt.equipment_count,
                    "rooms": receipt.room_count,
                    "academicYears": receipt.academic_year_count,
                    "academicCycles": receipt.academic_cycle_count,
                    "cycleCommissions": receipt.cycle_commission_count,
                    "teacherReplacements": receipt.teacher_replacement_count,
                    "calendarPeriods": receipt.calendar_period_count,
                    "bellSlots": receipt.bell_slot_count,
                    "calendarExceptions": receipt.calendar_exception_count,
                    "resourceUnavailability": receipt.resource_unavailability_count,
                },
            },
        )

    @app.post("/api/manual-data/activate", status_code=201)
    def activate_manual_data(request: ManualDataRequest) -> JSONResponse:
        try:
            receipt = validate_and_activate_manual_batch(
                request.batch,
                repository,
                source_name=request.source_name,
            )
        except ImportValidationError as error:
            return JSONResponse(status_code=422, content=_issues_payload(error))
        except StorageError as error:
            raise ApiError(500, "storage_error", str(error)) from error
        return JSONResponse(
            status_code=201,
            content={
                "valid": True,
                "versionId": receipt.version_id,
                "sourceName": request.source_name,
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
                    "buildings": receipt.building_count,
                    "roomTypes": receipt.room_type_count,
                    "equipment": receipt.equipment_count,
                    "rooms": receipt.room_count,
                    "academicYears": receipt.academic_year_count,
                    "academicCycles": receipt.academic_cycle_count,
                    "cycleCommissions": receipt.cycle_commission_count,
                    "teacherReplacements": receipt.teacher_replacement_count,
                    "calendarPeriods": receipt.calendar_period_count,
                    "bellSlots": receipt.bell_slot_count,
                    "calendarExceptions": receipt.calendar_exception_count,
                    "resourceUnavailability": receipt.resource_unavailability_count,
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
