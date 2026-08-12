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


class ReadinessReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    issues: tuple[ReadinessIssue, ...]

    @property
    def is_ready(self) -> bool:
        return all(issue.severity is not ReadinessSeverity.ERROR for issue in self.issues)


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
