from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import time
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from rasp.domain.models import (
    AcademicYear,
    BellSlot,
    CalendarBatch,
    CalendarException,
    CalendarPeriod,
    ResourceUnavailability,
)


@dataclass(frozen=True, slots=True)
class CalendarIssue:
    section: str
    row: int
    column: str | None
    code: str
    message: str


class CalendarValidationError(ValueError):
    def __init__(self, issues: Iterable[CalendarIssue]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"Calendar contains {len(self.issues)} validation issue(s)")


ModelT = TypeVar("ModelT", bound=BaseModel)


def _parse_rows(
    section: str,
    rows: Iterable[Mapping[str, Any]],
    model: type[ModelT],
) -> tuple[list[tuple[int, ModelT]], list[CalendarIssue]]:
    parsed: list[tuple[int, ModelT]] = []
    issues: list[CalendarIssue] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            parsed.append((row_number, model.model_validate(dict(row))))
        except ValidationError as error:
            for detail in error.errors(include_url=False, include_input=False):
                location = detail.get("loc", ())
                issues.append(
                    CalendarIssue(
                        section,
                        row_number,
                        str(location[0]) if location else None,
                        "invalid_value",
                        str(detail["msg"]),
                    )
                )
    return parsed, issues


def _duplicate_issues(
    section: str,
    records: Iterable[tuple[int, BaseModel]],
    field: str,
) -> list[CalendarIssue]:
    issues: list[CalendarIssue] = []
    seen: set[object] = set()
    for row_number, record in records:
        value = getattr(record, field)
        if value in seen:
            issues.append(
                CalendarIssue(
                    section,
                    row_number,
                    field,
                    "duplicate_code",
                    f"Duplicate stable value: {value}",
                )
            )
        seen.add(value)
    return issues


def _times_overlap(
    first_start: time,
    first_end: time,
    second_start: time,
    second_end: time,
) -> bool:
    return first_start < second_end and second_start < first_end


def build_calendar_batch(
    *,
    academic_year_rows: Iterable[Mapping[str, Any]],
    period_rows: Iterable[Mapping[str, Any]],
    bell_slot_rows: Iterable[Mapping[str, Any]],
    exception_rows: Iterable[Mapping[str, Any]] = (),
    unavailability_rows: Iterable[Mapping[str, Any]] = (),
) -> CalendarBatch:
    years, issues = _parse_rows("academic_years", academic_year_rows, AcademicYear)
    periods, parsed_issues = _parse_rows("calendar_periods", period_rows, CalendarPeriod)
    issues.extend(parsed_issues)
    slots, parsed_issues = _parse_rows("bell_slots", bell_slot_rows, BellSlot)
    issues.extend(parsed_issues)
    exceptions, parsed_issues = _parse_rows(
        "calendar_exceptions", exception_rows, CalendarException
    )
    issues.extend(parsed_issues)
    unavailability, parsed_issues = _parse_rows(
        "resource_unavailability", unavailability_rows, ResourceUnavailability
    )
    issues.extend(parsed_issues)

    issues.extend(_duplicate_issues("academic_years", years, "academic_year"))
    issues.extend(_duplicate_issues("calendar_periods", periods, "period_code"))
    issues.extend(_duplicate_issues("bell_slots", slots, "slot_code"))
    issues.extend(
        _duplicate_issues("calendar_exceptions", exceptions, "exception_code")
    )
    issues.extend(
        _duplicate_issues(
            "resource_unavailability", unavailability, "unavailability_code"
        )
    )

    years_by_code = {year.academic_year: year for _, year in years}
    for row_number, period in periods:
        year = years_by_code.get(period.academic_year)
        if year is None:
            issues.append(
                CalendarIssue(
                    "calendar_periods",
                    row_number,
                    "academic_year",
                    "unknown_academic_year",
                    "Period references an unknown academic year",
                )
            )
        elif period.starts_on < year.starts_on or period.ends_on > year.ends_on:
            issues.append(
                CalendarIssue(
                    "calendar_periods",
                    row_number,
                    "starts_on",
                    "period_outside_academic_year",
                    "Period must fit inside its academic year",
                )
            )

    slots_by_scope: dict[tuple[str, str], list[tuple[int, BellSlot]]] = {}
    for row_number, slot in slots:
        if slot.academic_year not in years_by_code:
            issues.append(
                CalendarIssue(
                    "bell_slots",
                    row_number,
                    "academic_year",
                    "unknown_academic_year",
                    "Bell slot references an unknown academic year",
                )
            )
        slots_by_scope.setdefault((slot.academic_year, slot.shift_code), []).append(
            (row_number, slot)
        )
    for scoped_slots in slots_by_scope.values():
        for index, (_, first) in enumerate(scoped_slots):
            for row_number, second in scoped_slots[index + 1 :]:
                if first.lesson_number == second.lesson_number:
                    issues.append(
                        CalendarIssue(
                            "bell_slots",
                            row_number,
                            "lesson_number",
                            "duplicate_lesson_number",
                            "Lesson number must be unique inside a shift",
                        )
                    )
                if _times_overlap(
                    first.starts_at,
                    first.ends_at,
                    second.starts_at,
                    second.ends_at,
                ):
                    issues.append(
                        CalendarIssue(
                            "bell_slots",
                            row_number,
                            "starts_at",
                            "overlapping_bell_slots",
                            "Bell slots inside a shift must not overlap",
                        )
                    )

    for row_number, exception in exceptions:
        year = years_by_code.get(exception.academic_year)
        dates = (exception.exception_date, exception.transferred_to)
        if year is None:
            issues.append(
                CalendarIssue(
                    "calendar_exceptions",
                    row_number,
                    "academic_year",
                    "unknown_academic_year",
                    "Calendar exception references an unknown academic year",
                )
            )
        elif any(
            value is not None and not year.starts_on <= value <= year.ends_on
            for value in dates
        ):
            issues.append(
                CalendarIssue(
                    "calendar_exceptions",
                    row_number,
                    "exception_date",
                    "exception_outside_academic_year",
                    "Calendar exception must fit inside its academic year",
                )
            )

    for row_number, unavailable in unavailability:
        year = years_by_code.get(unavailable.academic_year)
        if year is None:
            issues.append(
                CalendarIssue(
                    "resource_unavailability",
                    row_number,
                    "academic_year",
                    "unknown_academic_year",
                    "Unavailability references an unknown academic year",
                )
            )
        elif unavailable.starts_on < year.starts_on or unavailable.ends_on > year.ends_on:
            issues.append(
                CalendarIssue(
                    "resource_unavailability",
                    row_number,
                    "starts_on",
                    "unavailability_outside_academic_year",
                    "Unavailability must fit inside its academic year",
                )
            )
    if issues:
        raise CalendarValidationError(issues)
    return CalendarBatch(
        academic_years=tuple(record for _, record in years),
        periods=tuple(record for _, record in periods),
        bell_slots=tuple(record for _, record in slots),
        exceptions=tuple(record for _, record in exceptions),
        unavailability=tuple(record for _, record in unavailability),
    )
