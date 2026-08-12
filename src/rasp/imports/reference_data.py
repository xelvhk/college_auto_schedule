from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from rasp.domain.models import (
    Curriculum,
    CurriculumDiscipline,
    CurriculumStatus,
    ReferenceDataBatch,
    Specialty,
)


@dataclass(frozen=True, slots=True)
class ReferenceDataIssue:
    section: str
    row: int
    column: str | None
    code: str
    message: str


class ReferenceDataValidationError(ValueError):
    def __init__(self, issues: Iterable[ReferenceDataIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(
            f"Reference data contains {len(self.issues)} validation issue(s)"
        )


ModelT = TypeVar("ModelT", bound=BaseModel)


def _parse_rows(
    section: str,
    rows: Iterable[Mapping[str, Any]],
    model: type[ModelT],
) -> tuple[list[tuple[int, ModelT]], list[ReferenceDataIssue]]:
    parsed: list[tuple[int, ModelT]] = []
    issues: list[ReferenceDataIssue] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            parsed.append((row_number, model.model_validate(dict(row))))
        except ValidationError as error:
            for detail in error.errors(include_url=False, include_input=False):
                location = detail.get("loc", ())
                issues.append(
                    ReferenceDataIssue(
                        section=section,
                        row=row_number,
                        column=str(location[0]) if location else None,
                        code="invalid_value",
                        message=str(detail["msg"]),
                    )
                )
    return parsed, issues


def _duplicate_code_issues(
    section: str,
    records: Iterable[tuple[int, BaseModel]],
    field: str,
) -> list[ReferenceDataIssue]:
    issues: list[ReferenceDataIssue] = []
    seen: set[str] = set()
    for row_number, record in records:
        code = str(getattr(record, field))
        if code in seen:
            issues.append(
                ReferenceDataIssue(
                    section=section,
                    row=row_number,
                    column=field,
                    code="duplicate_code",
                    message=f"Duplicate stable code: {code}",
                )
            )
        seen.add(code)
    return issues


def _periods_overlap(first: Curriculum, second: Curriculum) -> bool:
    first_end = first.valid_to or date.max
    second_end = second.valid_to or date.max
    return first.valid_from <= second_end and second.valid_from <= first_end


def build_reference_data_batch(
    *,
    specialty_rows: Iterable[Mapping[str, Any]],
    curriculum_rows: Iterable[Mapping[str, Any]],
    discipline_rows: Iterable[Mapping[str, Any]],
) -> ReferenceDataBatch:
    """Validate curriculum reference data atomically without changing storage."""

    specialties, issues = _parse_rows(
        "specialties", specialty_rows, Specialty
    )
    curricula, curriculum_issues = _parse_rows(
        "curricula", curriculum_rows, Curriculum
    )
    disciplines, discipline_issues = _parse_rows(
        "disciplines", discipline_rows, CurriculumDiscipline
    )
    issues.extend(curriculum_issues)
    issues.extend(discipline_issues)

    issues.extend(
        _duplicate_code_issues(
            "specialties", specialties, "specialty_code"
        )
    )
    issues.extend(
        _duplicate_code_issues("curricula", curricula, "curriculum_code")
    )

    specialty_codes = {
        specialty.specialty_code for _, specialty in specialties
    }
    for row_number, curriculum in curricula:
        if curriculum.specialty_code not in specialty_codes:
            issues.append(
                ReferenceDataIssue(
                    section="curricula",
                    row=row_number,
                    column="specialty_code",
                    code="unknown_specialty",
                    message=(
                        "Curriculum references an unknown specialty code: "
                        f"{curriculum.specialty_code}"
                    ),
                )
            )

    curriculum_codes = {
        curriculum.curriculum_code for _, curriculum in curricula
    }
    discipline_keys: set[tuple[object, ...]] = set()
    for row_number, discipline in disciplines:
        if discipline.curriculum_code not in curriculum_codes:
            issues.append(
                ReferenceDataIssue(
                    section="disciplines",
                    row=row_number,
                    column="curriculum_code",
                    code="unknown_curriculum",
                    message=(
                        "Discipline references an unknown curriculum code: "
                        f"{discipline.curriculum_code}"
                    ),
                )
            )
        if discipline.stable_key in discipline_keys:
            issues.append(
                ReferenceDataIssue(
                    section="disciplines",
                    row=row_number,
                    column="discipline_code",
                    code="duplicate_discipline",
                    message="Duplicate curriculum discipline key",
                )
            )
        discipline_keys.add(discipline.stable_key)

    active_curricula = [
        (row_number, curriculum)
        for row_number, curriculum in curricula
        if curriculum.status is CurriculumStatus.ACTIVE
    ]
    for index, (_, first) in enumerate(active_curricula):
        for row_number, second in active_curricula[index + 1 :]:
            same_scope = (
                first.specialty_code == second.specialty_code
                and first.admission_year == second.admission_year
            )
            if same_scope and _periods_overlap(first, second):
                issues.append(
                    ReferenceDataIssue(
                        section="curricula",
                        row=row_number,
                        column="valid_from",
                        code="overlapping_curricula",
                        message=(
                            "Active curriculum revisions overlap for the same "
                            "specialty and admission year"
                        ),
                    )
                )

    if issues:
        raise ReferenceDataValidationError(issues)

    return ReferenceDataBatch(
        specialties=tuple(record for _, record in specialties),
        curricula=tuple(record for _, record in curricula),
        disciplines=tuple(record for _, record in disciplines),
    )
