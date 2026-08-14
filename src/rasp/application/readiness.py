from __future__ import annotations

from collections import defaultdict
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from rasp.domain.models import ImportBatch, LessonType, ReferenceDataBatch


class ReadinessSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ReadinessIssue(BaseModel):
    model_config = ConfigDict(frozen=True)

    severity: ReadinessSeverity
    code: str
    message: str
    group_code: str | None = None
    curriculum_code: str | None = None
    discipline_code: str | None = None
    semester: int | None = None
    lesson_type: LessonType | None = None
    difference_hours: int | None = None
    section: str | None = None
    object_code: str | None = None
    remediation: str | None = None


class RoomDeficit(BaseModel):
    model_config = ConfigDict(frozen=True)

    workload_row_code: str
    group_code: str
    required_room_type: str | None = None
    required_capacity: int
    required_equipment_codes: tuple[str, ...] = ()


class ReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    issues: tuple[ReadinessIssue, ...]

    @property
    def is_ready(self) -> bool:
        return all(issue.severity is not ReadinessSeverity.ERROR for issue in self.issues)

    @property
    def error_count(self) -> int:
        return sum(
            issue.severity is ReadinessSeverity.ERROR for issue in self.issues
        )

    @property
    def warning_count(self) -> int:
        return sum(
            issue.severity is ReadinessSeverity.WARNING for issue in self.issues
        )


def analyze_curriculum_alignment(
    imports: ImportBatch,
    references: ReferenceDataBatch,
) -> ReadinessReport:
    """Check that group workload is covered by its selected curriculum."""

    issues: list[ReadinessIssue] = []
    curricula = {
        curriculum.curriculum_code: curriculum
        for curriculum in references.curricula
    }
    planned_hours = {
        discipline.stable_key: discipline.planned_hours
        for discipline in references.disciplines
    }
    groups = {group.group_code: group for group in imports.groups}
    assigned_hours: defaultdict[
        tuple[str, str, str, int, LessonType], int
    ] = defaultdict(int)

    for group in imports.groups:
        curriculum_code = group.curriculum_code
        if curriculum_code is None:
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.ERROR,
                    code="group_without_curriculum",
                    message="Group has no selected curriculum",
                    group_code=group.group_code,
                )
            )
            continue
        curriculum = curricula.get(curriculum_code)
        if curriculum is None:
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.ERROR,
                    code="unknown_group_curriculum",
                    message="Group references an unknown curriculum",
                    group_code=group.group_code,
                    curriculum_code=curriculum_code,
                )
            )
            continue
        if (
            group.specialty_code is not None
            and group.specialty_code != curriculum.specialty_code
        ):
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.ERROR,
                    code="curriculum_specialty_mismatch",
                    message="Group and curriculum belong to different specialties",
                    group_code=group.group_code,
                    curriculum_code=curriculum_code,
                )
            )

    for workload in imports.workloads:
        group = groups[workload.group_code]
        if group.curriculum_code is None or group.curriculum_code not in curricula:
            continue
        key = (
            group.group_code,
            group.curriculum_code,
            workload.discipline_code,
            workload.semester,
            workload.lesson_type,
        )
        curriculum_key = key[1:]
        if curriculum_key not in planned_hours:
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.ERROR,
                    code="discipline_not_in_curriculum",
                    message="Workload discipline is not present in the group curriculum",
                    group_code=group.group_code,
                    curriculum_code=group.curriculum_code,
                    discipline_code=workload.discipline_code,
                    semester=workload.semester,
                    lesson_type=workload.lesson_type,
                )
            )
            continue
        assigned_hours[key] += workload.total_academic_hours

    comparable_keys = {
        (
            group.group_code,
            curriculum_code,
            discipline_code,
            semester,
            lesson_type,
        )
        for group in imports.groups
        if (curriculum_code := group.curriculum_code) in curricula
        for (
            planned_curriculum_code,
            discipline_code,
            semester,
            lesson_type,
        ) in planned_hours
        if planned_curriculum_code == curriculum_code
    }

    for key in sorted(comparable_keys):
        assigned = assigned_hours[key]
        group_code, curriculum_code, discipline_code, semester, lesson_type = key
        planned = planned_hours[key[1:]]
        difference = assigned - planned
        common = {
            "group_code": group_code,
            "curriculum_code": curriculum_code,
            "discipline_code": discipline_code,
            "semester": semester,
            "lesson_type": lesson_type,
        }
        if difference > 0:
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.ERROR,
                    code="curriculum_hours_exceeded",
                    message="Assigned workload exceeds curriculum hours",
                    difference_hours=difference,
                    **common,
                )
            )
        elif difference < 0:
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.WARNING,
                    code="curriculum_hours_remaining",
                    message="Curriculum hours are not fully assigned",
                    difference_hours=-difference,
                    **common,
                )
            )

    return ReadinessReport(issues=tuple(issues))


def analyze_room_supply(imports: ImportBatch) -> tuple[RoomDeficit, ...]:
    deficits: list[RoomDeficit] = []
    groups = {group.group_code: group for group in imports.groups}
    for workload in imports.workloads:
        group = groups.get(workload.group_code)
        required_capacity = workload.room_capacity or (group.headcount if group else 1)
        required_equipment = set(workload.required_equipment_codes)
        matching = [
            room
            for room in imports.rooms
            if room.active
            and room.capacity >= required_capacity
            and (workload.room_type is None or room.room_type_code == workload.room_type)
            and required_equipment.issubset(room.equipment_codes)
        ]
        if not matching:
            deficits.append(
                RoomDeficit(
                    workload_row_code=workload.workload_row_code,
                    group_code=workload.group_code,
                    required_room_type=workload.room_type,
                    required_capacity=required_capacity,
                    required_equipment_codes=workload.required_equipment_codes,
                )
            )
    return tuple(deficits)


def analyze_schedule_readiness(imports: ImportBatch) -> ReadinessReport:
    """Return a stable, privacy-safe preflight report for the future solver."""

    issues: list[ReadinessIssue] = []
    required_sections = (
        ("teachers", imports.teachers, "missing_teachers", "Добавьте преподавателей."),
        ("groups", imports.groups, "missing_groups", "Добавьте учебные группы."),
        ("workloads", imports.workloads, "missing_workloads", "Добавьте нагрузку."),
        (
            "curricula",
            imports.curricula,
            "missing_curricula",
            "Добавьте учебные планы.",
        ),
        (
            "disciplines",
            imports.disciplines,
            "missing_disciplines",
            "Добавьте дисциплины учебных планов.",
        ),
        (
            "academic_years",
            imports.academic_years,
            "missing_academic_years",
            "Добавьте учебный год.",
        ),
        (
            "calendar_periods",
            imports.calendar_periods,
            "missing_calendar_periods",
            "Добавьте хотя бы один период обучения.",
        ),
        (
            "bell_slots",
            imports.bell_slots,
            "missing_bell_slots",
            "Добавьте сетку звонков.",
        ),
    )
    for section, records, code, remediation in required_sections:
        if not records:
            issues.append(
                ReadinessIssue(
                    severity=ReadinessSeverity.ERROR,
                    code=code,
                    message="Отсутствует обязательный раздел исходных данных",
                    section=section,
                    remediation=remediation,
                )
            )

    if imports.groups and imports.curricula and imports.disciplines:
        curriculum_report = analyze_curriculum_alignment(
            imports,
            ReferenceDataBatch(
                specialties=imports.specialties,
                curricula=imports.curricula,
                disciplines=imports.disciplines,
            ),
        )
        for issue in curriculum_report.issues:
            issues.append(
                issue.model_copy(
                    update={
                        "section": "workloads",
                        "object_code": issue.discipline_code or issue.group_code,
                        "remediation": (
                            "Сверьте учебный план группы и распределённую нагрузку."
                        ),
                    }
                )
            )

    for deficit in analyze_room_supply(imports):
        issues.append(
            ReadinessIssue(
                severity=ReadinessSeverity.ERROR,
                code="no_suitable_room",
                message="Для строки нагрузки нет подходящей активной аудитории",
                section="rooms",
                object_code=deficit.workload_row_code,
                group_code=deficit.group_code,
                remediation=(
                    "Добавьте аудиторию нужного типа, вместимости и оснащения "
                    "или скорректируйте требования нагрузки."
                ),
            )
        )

    teachers = {teacher.teacher_code: teacher for teacher in imports.teachers}
    academic_years = {
        academic_year.academic_year: academic_year
        for academic_year in imports.academic_years
    }
    period_scopes = {
        (period.academic_year, period.semester)
        for period in imports.calendar_periods
        if period.semester is not None
    }
    bell_slot_years = {slot.academic_year for slot in imports.bell_slots}
    cycles = {cycle.cycle_code: cycle for cycle in imports.academic_cycles}
    for workload in imports.workloads:
        teacher = teachers.get(workload.teacher_code)
        academic_year = academic_years.get(workload.academic_year)
        checks = (
            (
                teacher is not None and teacher.active,
                "inactive_workload_teacher",
                "Назначенный преподаватель неактивен",
                "Активируйте преподавателя или измените назначение нагрузки.",
            ),
            (
                academic_year is not None and academic_year.active,
                "inactive_workload_academic_year",
                "Учебный год нагрузки отсутствует или неактивен",
                "Добавьте или активируйте учебный год, указанный в нагрузке.",
            ),
            (
                (workload.academic_year, workload.semester) in period_scopes,
                "missing_workload_period",
                "Для нагрузки не найден учебный период семестра",
                "Добавьте период нужного учебного года и семестра.",
            ),
            (
                workload.academic_year in bell_slot_years,
                "missing_workload_bell_slots",
                "Для учебного года нагрузки не задана сетка звонков",
                "Добавьте интервалы звонков для этого учебного года.",
            ),
        )
        for passed, code, message, remediation in checks:
            if not passed:
                issues.append(
                    ReadinessIssue(
                        severity=ReadinessSeverity.ERROR,
                        code=code,
                        message=message,
                        section="workloads",
                        object_code=workload.workload_row_code,
                        group_code=workload.group_code,
                        remediation=remediation,
                    )
                )
        if workload.cycle_code is not None:
            cycle = cycles.get(workload.cycle_code)
            if cycle is None or not cycle.active:
                issues.append(
                    ReadinessIssue(
                        severity=ReadinessSeverity.ERROR,
                        code="inactive_workload_cycle",
                        message="Учебный цикл нагрузки отсутствует или неактивен",
                        section="academic_cycles",
                        object_code=workload.workload_row_code,
                        group_code=workload.group_code,
                        remediation="Добавьте или активируйте выбранный учебный цикл.",
                    )
                )

    if imports.academic_years and not imports.calendar_exceptions:
        issues.append(
            ReadinessIssue(
                severity=ReadinessSeverity.WARNING,
                code="calendar_exceptions_not_configured",
                message="Исключения календаря не заданы",
                section="calendar_exceptions",
                remediation="Подтвердите, что праздники и переносы не требуются.",
            )
        )
    if imports.academic_years and not imports.resource_unavailability:
        issues.append(
            ReadinessIssue(
                severity=ReadinessSeverity.WARNING,
                code="resource_unavailability_not_configured",
                message="Недоступность ресурсов не задана",
                section="resource_unavailability",
                remediation=(
                    "Подтвердите доступность преподавателей, групп и аудиторий."
                ),
            )
        )

    severity_order = {
        ReadinessSeverity.ERROR: 0,
        ReadinessSeverity.WARNING: 1,
    }
    issues.sort(
        key=lambda issue: (
            severity_order[issue.severity],
            issue.code,
            issue.section or "",
            issue.object_code or "",
            issue.group_code or "",
        )
    )
    return ReadinessReport(issues=tuple(issues))
