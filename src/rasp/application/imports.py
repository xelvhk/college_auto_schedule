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


def validate_activation_invariants(batch: ImportBatch) -> None:
    """Reject structurally contradictory data through every activation route."""

    issues: list[ImportIssue] = []

    def add_issue(
        section: str,
        row: int,
        column: str | None,
        code: str,
        message: str,
    ) -> None:
        issues.append(ImportIssue(section, row, column, code, message))

    def reject_duplicate_codes(
        section: str, records: tuple[object, ...], attribute: str
    ) -> None:
        seen: set[object] = set()
        for row_number, record in enumerate(records, start=2):
            value = getattr(record, attribute)
            if value in seen:
                add_issue(
                    section,
                    row_number,
                    attribute,
                    "duplicate_code",
                    "Stable code is duplicated",
                )
            seen.add(value)

    unique_sections = (
        ("teachers", batch.teachers, "teacher_code"),
        ("groups", batch.groups, "group_code"),
        ("workloads", batch.workloads, "workload_row_code"),
        ("specialties", batch.specialties, "specialty_code"),
        ("curricula", batch.curricula, "curriculum_code"),
        ("students", batch.students, "student_code"),
        ("buildings", batch.buildings, "building_code"),
        ("room_types", batch.room_types, "room_type_code"),
        ("equipment", batch.equipment, "equipment_code"),
        ("rooms", batch.rooms, "room_code"),
        ("academic_years", batch.academic_years, "academic_year"),
        ("academic_cycles", batch.academic_cycles, "cycle_code"),
        ("calendar_periods", batch.calendar_periods, "period_code"),
        ("bell_slots", batch.bell_slots, "slot_code"),
        ("calendar_exceptions", batch.calendar_exceptions, "exception_code"),
        (
            "resource_unavailability",
            batch.resource_unavailability,
            "unavailability_code",
        ),
        ("cycle_commissions", batch.cycle_commissions, "commission_code"),
        (
            "teacher_replacements",
            batch.teacher_replacements,
            "replacement_code",
        ),
    )
    for section, records, attribute in unique_sections:
        reject_duplicate_codes(section, records, attribute)

    discipline_keys: set[tuple[object, ...]] = set()
    for row_number, discipline in enumerate(batch.disciplines, start=2):
        key = (
            discipline.curriculum_code,
            discipline.discipline_code,
            discipline.semester,
            discipline.lesson_type,
        )
        if key in discipline_keys:
            add_issue(
                "disciplines",
                row_number,
                "discipline_code",
                "duplicate_code",
                "Curriculum discipline key is duplicated",
            )
        discipline_keys.add(key)

    teachers = {item.teacher_code: item for item in batch.teachers}
    groups = {item.group_code: item for item in batch.groups}
    workloads = {item.workload_row_code: item for item in batch.workloads}
    specialties = {item.specialty_code for item in batch.specialties}
    curricula = {item.curriculum_code for item in batch.curricula}
    buildings = {item.building_code for item in batch.buildings}
    room_types = {item.room_type_code for item in batch.room_types}
    equipment = {item.equipment_code for item in batch.equipment}
    rooms = {item.room_code for item in batch.rooms}
    academic_years = {item.academic_year: item for item in batch.academic_years}
    cycles = {item.cycle_code: item for item in batch.academic_cycles}
    commissions = {
        item.commission_code: item for item in batch.cycle_commissions
    }

    for row_number, curriculum in enumerate(batch.curricula, start=2):
        if curriculum.specialty_code not in specialties:
            add_issue(
                "curricula", row_number, "specialty_code", "unknown_specialty",
                "Curriculum references an unknown specialty",
            )
    for row_number, discipline in enumerate(batch.disciplines, start=2):
        if discipline.curriculum_code not in curricula:
            add_issue(
                "disciplines", row_number, "curriculum_code", "unknown_curriculum",
                "Discipline references an unknown curriculum",
            )
    for row_number, teacher in enumerate(batch.teachers, start=2):
        if teacher.home_building_code and teacher.home_building_code not in buildings:
            add_issue(
                "teachers", row_number, "home_building_code", "unknown_building",
                "Teacher references an unknown building",
            )
        if teacher.cycle_commission_code:
            commission = commissions.get(teacher.cycle_commission_code)
            if commission is None or not commission.active:
                add_issue(
                    "teachers", row_number, "cycle_commission_code",
                    "inactive_teacher_cycle_commission",
                    "Teacher references an unknown or inactive cycle commission",
                )
    for row_number, group in enumerate(batch.groups, start=2):
        if group.specialty_code and group.specialty_code not in specialties:
            add_issue(
                "groups", row_number, "specialty_code", "unknown_specialty",
                "Group references an unknown specialty",
            )
        if group.curriculum_code and group.curriculum_code not in curricula:
            add_issue(
                "groups", row_number, "curriculum_code", "unknown_curriculum",
                "Group references an unknown curriculum",
            )
        if group.primary_building_code and group.primary_building_code not in buildings:
            add_issue(
                "groups", row_number, "primary_building_code", "unknown_building",
                "Group references an unknown building",
            )
    for row_number, workload in enumerate(batch.workloads, start=2):
        if workload.teacher_code not in teachers:
            add_issue(
                "workloads", row_number, "teacher_code", "unknown_teacher",
                "Workload references an unknown teacher",
            )
        if workload.group_code not in groups:
            add_issue(
                "workloads", row_number, "group_code", "unknown_group",
                "Workload references an unknown group",
            )
        if workload.academic_year not in academic_years:
            add_issue(
                "workloads", row_number, "academic_year", "unknown_academic_year",
                "Workload references an unknown academic year",
            )
        if workload.cycle_code:
            cycle = cycles.get(workload.cycle_code)
            if cycle is None or cycle.academic_year != workload.academic_year:
                add_issue(
                    "workloads", row_number, "cycle_code", "unknown_cycle",
                    "Workload references an unknown or incompatible academic cycle",
                )
        if not set(workload.required_equipment_codes).issubset(equipment):
            add_issue(
                "workloads",
                row_number,
                "required_equipment_codes",
                "unknown_required_equipment",
                "Workload references unknown required equipment",
            )
    for row_number, student in enumerate(batch.students, start=2):
        if student.group_code not in groups:
            add_issue(
                "students", row_number, "group_code", "unknown_student_group",
                "Student references an unknown group",
            )
    for row_number, room in enumerate(batch.rooms, start=2):
        if room.building_code not in buildings:
            add_issue(
                "rooms", row_number, "building_code", "unknown_building",
                "Room references an unknown building",
            )
        if room.room_type_code not in room_types:
            add_issue(
                "rooms", row_number, "room_type_code", "unknown_room_type",
                "Room references an unknown room type",
            )
        if not set(room.equipment_codes).issubset(equipment):
            add_issue(
                "rooms", row_number, "equipment_codes", "unknown_equipment",
                "Room references unknown equipment",
            )
    for row_number, cycle in enumerate(batch.academic_cycles, start=2):
        if cycle.academic_year not in academic_years:
            add_issue(
                "academic_cycles", row_number, "academic_year",
                "unknown_cycle_academic_year",
                "Academic cycle references an unknown academic year",
            )

    academic_year_references = (
        ("calendar_periods", batch.calendar_periods),
        ("bell_slots", batch.bell_slots),
        ("calendar_exceptions", batch.calendar_exceptions),
        ("resource_unavailability", batch.resource_unavailability),
    )
    for section, records in academic_year_references:
        for row_number, record in enumerate(records, start=2):
            if record.academic_year not in academic_years:
                add_issue(
                    section,
                    row_number,
                    "academic_year",
                    "unknown_academic_year",
                    "Record references an unknown academic year",
                )

    resource_codes = {
        "teacher": set(teachers), "group": set(groups), "room": rooms,
    }
    for row_number, unavailable in enumerate(batch.resource_unavailability, start=2):
        if unavailable.resource_code not in resource_codes[unavailable.resource_type.value]:
            add_issue(
                "resource_unavailability", row_number, "resource_code",
                "unknown_unavailability_resource",
                "Unavailability references an unknown resource",
            )

    for row_number, replacement in enumerate(batch.teacher_replacements, start=2):
        original = teachers.get(replacement.original_teacher_code)
        substitute = teachers.get(replacement.substitute_teacher_code)
        academic_year = academic_years.get(replacement.academic_year)
        workload = workloads.get(replacement.workload_row_code or "")
        if original is None or not original.active:
            add_issue(
                "teacher_replacements", row_number, "original_teacher_code",
                "inactive_replacement_original_teacher",
                "Original replacement teacher is unknown or inactive",
            )
        if substitute is None or not substitute.active:
            add_issue(
                "teacher_replacements", row_number, "substitute_teacher_code",
                "inactive_replacement_substitute_teacher",
                "Substitute teacher is unknown or inactive",
            )
        if academic_year is None or not academic_year.active:
            add_issue(
                "teacher_replacements", row_number, "academic_year",
                "inactive_replacement_academic_year",
                "Replacement academic year is unknown or inactive",
            )
        elif not (
            academic_year.starts_on <= replacement.starts_on
            and replacement.ends_on <= academic_year.ends_on
        ):
            add_issue(
                "teacher_replacements", row_number, "starts_on",
                "replacement_dates_outside_academic_year",
                "Replacement dates are outside the academic year",
            )
        if replacement.workload_row_code:
            if workload is None:
                add_issue(
                    "teacher_replacements", row_number, "workload_row_code",
                    "unknown_replacement_workload",
                    "Replacement references an unknown workload",
                )
            elif workload.teacher_code != replacement.original_teacher_code:
                add_issue(
                    "teacher_replacements", row_number, "workload_row_code",
                    "replacement_workload_teacher_mismatch",
                    "Replacement workload belongs to another teacher",
                )
            elif workload.academic_year != replacement.academic_year:
                add_issue(
                    "teacher_replacements", row_number, "workload_row_code",
                    "replacement_workload_academic_year_mismatch",
                    "Replacement workload belongs to another academic year",
                )

    ordered_replacements = sorted(
        batch.teacher_replacements,
        key=lambda item: (
            item.academic_year, item.original_teacher_code,
            item.starts_on, item.replacement_code,
        ),
    )
    for index, previous in enumerate(ordered_replacements):
        for current in ordered_replacements[index + 1 :]:
            if (current.academic_year, current.original_teacher_code) != (
                previous.academic_year, previous.original_teacher_code
            ):
                break
            same_scope = (
                previous.workload_row_code is None
                or current.workload_row_code is None
                or previous.workload_row_code == current.workload_row_code
            )
            if same_scope and current.starts_on <= previous.ends_on:
                add_issue(
                    "teacher_replacements", 0, "starts_on",
                    "overlapping_teacher_replacements",
                    "Teacher replacements overlap in the same scope",
                )

    if issues:
        issues.sort(key=lambda item: (item.section, item.row, item.code))
        raise ImportValidationError(issues)


def validate_and_activate_workbook(
    workbook_path: str | Path,
    repository: SqliteImportRepository,
) -> ImportReceipt:
    """Validate a stable file snapshot, then atomically make it active."""

    source = Path(workbook_path)
    preflight_import_workbook(source)
    fingerprint_before = _sha256_file(source)
    batch = read_import_workbook(source)
    validate_activation_invariants(batch)
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
