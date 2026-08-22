from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from rasp.application.imports import (
    validate_activation_invariants,
    validate_and_activate_workbook,
    validate_curriculum_readiness,
)
from rasp.application.readiness import ReadinessIssue, analyze_schedule_readiness
from rasp.domain.models import ImportBatch
from rasp.imports.excel import ImportValidationError, read_import_workbook
from rasp.solver import (
    CpSatScheduleSolver,
    MAX_SOLVER_SEED,
    SolverOptions,
    SolverStatus,
    build_solver_problem,
    solver_problem_payload,
    solver_result_payload,
)
from rasp.storage.sqlite import (
    SqliteImportRepository,
    StorageError,
    VersionNotFoundError,
)


DEFAULT_DATABASE = Path("data/rasp.sqlite3")


def _solver_seed(value: str) -> int:
    seed = int(value)
    if not 0 <= seed <= MAX_SOLVER_SEED:
        raise argparse.ArgumentTypeError(
            f"seed должен быть от 0 до {MAX_SOLVER_SEED}"
        )
    return seed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rasp")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="проверить файл импорта")
    validate.add_argument("file", type=Path)
    activate = subparsers.add_parser(
        "import", help="проверить и активировать новую версию данных"
    )
    activate.add_argument("file", type=Path)
    activate.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    status = subparsers.add_parser("status", help="показать активную версию данных")
    status.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    readiness = subparsers.add_parser(
        "readiness", help="проверить готовность активных данных к расчёту"
    )
    readiness.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    solver_problem = subparsers.add_parser(
        "solver-problem", help="подготовить задачу для расчёта расписания"
    )
    solver_problem.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    solve = subparsers.add_parser("solve", help="рассчитать черновик расписания")
    solve.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    solve.add_argument("--seed", type=_solver_seed, default=0)
    solve.add_argument("--time-limit", type=int, default=30, choices=range(1, 301))
    activate_version = subparsers.add_parser(
        "activate-version", help="вернуться к сохраненной версии данных"
    )
    activate_version.add_argument("version_id", type=int)
    activate_version.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    return parser


def _print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_validation_error(error: ImportValidationError) -> None:
    _print_json(
        {
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
    )


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


def _validate(file_path: Path) -> int:
    try:
        batch = read_import_workbook(file_path)
        validate_activation_invariants(batch)
        validate_curriculum_readiness(batch)
    except ImportValidationError as error:
        _print_validation_error(error)
        return 2
    _print_json(
        {
            "valid": True,
            "counts": {
                "teachers": len(batch.teachers),
                "groups": len(batch.groups),
                "workloads": len(batch.workloads),
                "specialties": len(batch.specialties),
                "curricula": len(batch.curricula),
                "disciplines": len(batch.disciplines),
                "students": len(batch.students),
                "buildings": len(batch.buildings),
                "roomTypes": len(batch.room_types),
                "equipment": len(batch.equipment),
                "rooms": len(batch.rooms),
                "academicYears": len(batch.academic_years),
                "academicCycles": len(batch.academic_cycles),
                "cycleCommissions": len(batch.cycle_commissions),
                "teacherReplacements": len(batch.teacher_replacements),
                "calendarPeriods": len(batch.calendar_periods),
                "bellSlots": len(batch.bell_slots),
                "calendarExceptions": len(batch.calendar_exceptions),
                "resourceUnavailability": len(batch.resource_unavailability),
            },
        }
    )
    return 0


def _activate(file_path: Path, database_path: Path) -> int:
    repository = SqliteImportRepository(database_path)
    try:
        receipt = validate_and_activate_workbook(file_path, repository)
    except ImportValidationError as error:
        _print_validation_error(error)
        return 2
    except (OSError, StorageError) as error:
        _print_json(
            {
                "valid": False,
                "error": {"code": "storage_error", "message": str(error)},
            }
        )
        return 3

    _print_json(
        {
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
        }
    )
    return 0


def _status(database_path: Path) -> int:
    repository = SqliteImportRepository(database_path)
    try:
        repository.initialize()
        versions = repository.list_versions()
        batch = repository.get_active_batch()
    except StorageError as error:
        _print_json(
            {
                "valid": False,
                "error": {"code": "storage_error", "message": str(error)},
            }
        )
        return 3

    active = next((version for version in versions if version.is_active), None)
    _print_json(
        {
            "activeVersionId": active.version_id if active else None,
            "counts": {
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
                "teacherReplacements": len(batch.teacher_replacements)
                if batch
                else 0,
                "calendarPeriods": len(batch.calendar_periods) if batch else 0,
                "bellSlots": len(batch.bell_slots) if batch else 0,
                "calendarExceptions": len(batch.calendar_exceptions) if batch else 0,
                "resourceUnavailability": len(batch.resource_unavailability)
                if batch
                else 0,
            },
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
    )
    return 0


def _activate_version(version_id: int, database_path: Path) -> int:
    repository = SqliteImportRepository(database_path)
    try:
        repository.initialize()
        version = repository.activate_version(version_id)
    except VersionNotFoundError as error:
        _print_json(
            {
                "valid": False,
                "error": {"code": "version_not_found", "message": str(error)},
            }
        )
        return 4
    except (ValueError, StorageError) as error:
        _print_json(
            {
                "valid": False,
                "error": {"code": "storage_error", "message": str(error)},
            }
        )
        return 3

    _print_json(
        {
            "valid": True,
            "activeVersionId": version.version_id,
            "sourceName": version.source_name,
            "createdAt": version.created_at,
        }
    )
    return 0


def _readiness(database_path: Path) -> int:
    repository = SqliteImportRepository(database_path)
    try:
        repository.initialize()
        batch = repository.get_active_batch()
    except StorageError as error:
        _print_json(
            {
                "valid": False,
                "error": {"code": "storage_error", "message": str(error)},
            }
        )
        return 3
    if batch is None:
        _print_json(
            {
                "valid": False,
                "error": {
                    "code": "no_active_import",
                    "message": "No active import version",
                },
            }
        )
        return 5
    _print_json(_readiness_payload(batch))
    return 0


def _solver_problem(database_path: Path) -> int:
    repository = SqliteImportRepository(database_path)
    try:
        repository.initialize()
        batch = repository.get_active_batch()
    except StorageError as error:
        _print_json(
            {"valid": False, "error": {"code": "storage_error", "message": str(error)}}
        )
        return 3
    if batch is None:
        _print_json(
            {
                "valid": False,
                "error": {
                    "code": "no_active_import",
                    "message": "No active import version",
                },
            }
        )
        return 5
    _print_json(solver_problem_payload(build_solver_problem(batch)))
    return 0


def _solve(database_path: Path, seed: int, time_limit: int) -> int:
    repository = SqliteImportRepository(database_path)
    try:
        repository.initialize()
        batch = repository.get_active_batch()
    except StorageError as error:
        _print_json(
            {"valid": False, "error": {"code": "storage_error", "message": str(error)}}
        )
        return 3
    if batch is None:
        _print_json(
            {
                "valid": False,
                "error": {
                    "code": "no_active_import",
                    "message": "No active import version",
                },
            }
        )
        return 5
    problem = build_solver_problem(batch)
    result = CpSatScheduleSolver().solve(
        problem,
        SolverOptions(seed=seed, time_limit_seconds=time_limit),
    )
    _print_json(solver_result_payload(result, problem))
    return 0 if result.status is SolverStatus.FEASIBLE else 6


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "validate":
        return _validate(arguments.file)
    if arguments.command == "import":
        return _activate(arguments.file, arguments.database)
    if arguments.command == "status":
        return _status(arguments.database)
    if arguments.command == "readiness":
        return _readiness(arguments.database)
    if arguments.command == "solver-problem":
        return _solver_problem(arguments.database)
    if arguments.command == "solve":
        return _solve(arguments.database, arguments.seed, arguments.time_limit)
    if arguments.command == "activate-version":
        return _activate_version(arguments.version_id, arguments.database)
    raise AssertionError(f"Unhandled command: {arguments.command}")


if __name__ == "__main__":
    raise SystemExit(main())
