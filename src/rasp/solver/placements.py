from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, time, timedelta

from rasp.application.cycles import workload_applies_on_date
from rasp.domain.models import (
    BellSlot,
    CalendarExceptionType,
    CalendarPeriodType,
    Group,
    ImportBatch,
    ResourceUnavailability,
    ResourceType,
    Room,
    WorkloadItem,
)
from rasp.solver.contracts import (
    DiagnosticSeverity,
    PlacementOption,
    SolverDiagnostic,
    WorkloadPlacementDomain,
)


MAX_PLACEMENT_OPTIONS = 1_000_000
ACADEMIC_HOUR_MINUTES = 45
STUDY_WEEK_DAYS = {"five_days": 5, "six_days": 6}
AvailabilityIndex = dict[
    tuple[ResourceType, str], tuple[ResourceUnavailability, ...]
]


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _dates_between(starts_on: date, ends_on: date) -> Iterable[date]:
    current = starts_on
    while current <= ends_on:
        yield current
        current += timedelta(days=1)


def _slot_academic_hours(slot: BellSlot) -> int | None:
    microseconds_per_second = 1_000_000
    start_microseconds = (
        (
            slot.starts_at.hour * 3600
            + slot.starts_at.minute * 60
            + slot.starts_at.second
        )
        * microseconds_per_second
        + slot.starts_at.microsecond
    )
    end_microseconds = (
        (
            slot.ends_at.hour * 3600
            + slot.ends_at.minute * 60
            + slot.ends_at.second
        )
        * microseconds_per_second
        + slot.ends_at.microsecond
    )
    academic_hour_microseconds = (
        ACADEMIC_HOUR_MINUTES * 60 * microseconds_per_second
    )
    duration_microseconds = end_microseconds - start_microseconds
    if duration_microseconds % academic_hour_microseconds:
        return None
    return duration_microseconds // academic_hour_microseconds


def _slot_sequences(
    slots: tuple[BellSlot, ...],
    duration_academic_hours: int,
) -> tuple[tuple[BellSlot, ...], ...]:
    ordered = sorted(
        slots,
        key=lambda item: (
            item.shift_code,
            item.lesson_number,
            item.starts_at,
            item.slot_code,
        ),
    )
    sequences: list[tuple[BellSlot, ...]] = []
    for start_index, first in enumerate(ordered):
        total_hours = 0
        current: list[BellSlot] = []
        expected_number = first.lesson_number
        for slot in ordered[start_index:]:
            if (
                slot.shift_code != first.shift_code
                or slot.lesson_number != expected_number
            ):
                break
            slot_hours = _slot_academic_hours(slot)
            if slot_hours is None:
                break
            total_hours += slot_hours
            current.append(slot)
            if total_hours == duration_academic_hours:
                sequences.append(tuple(current))
                break
            if total_hours > duration_academic_hours:
                break
            expected_number += 1
    return tuple(sequences)


def _matching_rooms(
    batch: ImportBatch,
    workload: WorkloadItem,
    group: Group,
) -> tuple[Room, ...]:
    required_capacity = workload.room_capacity or group.headcount
    required_equipment = set(workload.required_equipment_codes)
    return tuple(
        sorted(
            (
                room
                for room in batch.rooms
                if room.active
                and room.capacity >= required_capacity
                and (
                    workload.room_type is None
                    or room.room_type_code == workload.room_type
                )
                and required_equipment.issubset(room.equipment_codes)
            ),
            key=lambda item: item.room_code,
        )
    )


def _overlaps(
    starts_at: time,
    ends_at: time,
    unavailable_starts_at: time,
    unavailable_ends_at: time,
) -> bool:
    return starts_at < unavailable_ends_at and unavailable_starts_at < ends_at


def _resource_is_available(
    availability: AvailabilityIndex,
    resource_type: ResourceType,
    resource_code: str,
    actual_date: date,
    slots: tuple[BellSlot, ...],
) -> bool:
    for item in availability.get((resource_type, resource_code), ()):
        if not item.starts_on <= actual_date <= item.ends_on:
            continue
        if item.starts_at is None or item.ends_at is None:
            return False
        if any(
            _overlaps(slot.starts_at, slot.ends_at, item.starts_at, item.ends_at)
            for slot in slots
        ):
            return False
    return True


def _availability_index(batch: ImportBatch) -> AvailabilityIndex:
    grouped: defaultdict[
        tuple[ResourceType, str], list[ResourceUnavailability]
    ] = defaultdict(list)
    for item in batch.resource_unavailability:
        grouped[(item.resource_type, item.resource_code)].append(item)
    return {
        key: tuple(
            sorted(
                values,
                key=lambda item: (
                    item.starts_on,
                    item.ends_on,
                    item.starts_at or time.min,
                    item.unavailability_code,
                ),
            )
        )
        for key, values in grouped.items()
    }


def _effective_teacher_code(
    batch: ImportBatch, workload: WorkloadItem, lesson_date: date
) -> str:
    matches = [
        item
        for item in batch.teacher_replacements
        if item.academic_year == workload.academic_year
        and item.original_teacher_code == workload.teacher_code
        and item.starts_on <= lesson_date <= item.ends_on
        and (item.workload_row_code is None or item.workload_row_code == workload.workload_row_code)
    ]
    if not matches:
        return workload.teacher_code
    # Ambiguous overlaps are rejected by readiness; a deterministic fallback keeps
    # placement-domain construction total for preview and diagnostics.
    return sorted(matches, key=lambda item: item.replacement_code)[0].substitute_teacher_code


def _teaching_dates(
    batch: ImportBatch,
    workload: WorkloadItem,
    group: Group,
) -> tuple[tuple[date, date], ...]:
    week_days = STUDY_WEEK_DAYS.get(group.study_week_type or "")
    if week_days is None:
        return ()
    periods = (
        period
        for period in batch.calendar_periods
        if period.period_type is CalendarPeriodType.TEACHING
        and period.academic_year == workload.academic_year
        and period.semester == workload.semester
    )
    schedule_dates = sorted(
        {
            day
            for period in periods
            for day in _dates_between(period.starts_on, period.ends_on)
        }
    )
    working_dates = {
        item.exception_date
        for item in batch.calendar_exceptions
        if item.academic_year == workload.academic_year
        and item.exception_type is CalendarExceptionType.WORKING_DAY
    }
    holidays = {
        item.exception_date
        for item in batch.calendar_exceptions
        if item.academic_year == workload.academic_year
        and item.exception_type is CalendarExceptionType.HOLIDAY
    }
    transfers = {
        item.exception_date: item.transferred_to
        for item in batch.calendar_exceptions
        if item.academic_year == workload.academic_year
        and item.exception_type is CalendarExceptionType.TRANSFERRED_DAY
        and item.transferred_to is not None
    }
    cycles = {
        item.cycle_code: item
        for item in batch.academic_cycles
        if item.active
    }
    cycle = cycles.get(workload.cycle_code) if workload.cycle_code else None
    if workload.cycle_code is not None and cycle is None:
        return ()
    result: set[tuple[date, date]] = set()
    for schedule_date in schedule_dates:
        if schedule_date.weekday() >= week_days and schedule_date not in working_dates:
            continue
        if not workload_applies_on_date(workload, schedule_date, cycle):
            continue
        actual_date = transfers.get(schedule_date, schedule_date)
        if actual_date in holidays:
            continue
        result.add((schedule_date, actual_date))
    return tuple(sorted(result, key=lambda item: (item[1], item[0])))


def _shortened_cutoffs(batch: ImportBatch, academic_year: str) -> dict[date, time]:
    cutoffs: dict[date, time] = {}
    for item in batch.calendar_exceptions:
        if (
            item.academic_year != academic_year
            or item.exception_type is not CalendarExceptionType.SHORTENED_DAY
            or item.shortened_ends_at is None
        ):
            continue
        existing = cutoffs.get(item.exception_date)
        if existing is None or item.shortened_ends_at < existing:
            cutoffs[item.exception_date] = item.shortened_ends_at
    return cutoffs


def build_placement_domains(
    batch: ImportBatch,
) -> tuple[tuple[WorkloadPlacementDomain, ...], tuple[SolverDiagnostic, ...]]:
    diagnostics: list[SolverDiagnostic] = []
    domains: list[WorkloadPlacementDomain] = []
    workload_years = {item.academic_year for item in batch.workloads}
    invalid_slot_codes = {
        slot.slot_code
        for slot in batch.bell_slots
        if slot.academic_year in workload_years
        and _slot_academic_hours(slot) is None
    }
    for slot_code in sorted(invalid_slot_codes):
        diagnostics.append(
            SolverDiagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="unsupported_bell_slot_duration",
                message="Длительность интервала не кратна 45 минутам",
                section="bell_slots",
                object_code=slot_code,
                remediation="Исправьте время начала или окончания интервала.",
            )
        )

    groups = {item.group_code: item for item in batch.groups}
    availability = _availability_index(batch)
    total_options = 0
    for workload in sorted(batch.workloads, key=lambda item: item.workload_row_code):
        group = groups.get(workload.group_code)
        options: list[PlacementOption] = []
        if group is not None and group.study_week_type not in STUDY_WEEK_DAYS:
            diagnostics.append(
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="unsupported_study_week_type",
                    message="Не задан поддерживаемый тип учебной недели группы",
                    section="groups",
                    object_code=group.group_code,
                    remediation="Укажите five_days или six_days.",
                )
            )
        relevant_slots = tuple(
            slot
            for slot in batch.bell_slots
            if slot.academic_year == workload.academic_year
            and slot.slot_code not in invalid_slot_codes
        )
        sequences = _slot_sequences(
            relevant_slots,
            workload.event_duration_hours,
        )
        rooms = _matching_rooms(batch, workload, group) if group is not None else ()
        teaching_dates = (
            _teaching_dates(batch, workload, group) if group is not None else ()
        )
        cutoffs = _shortened_cutoffs(batch, workload.academic_year)
        for schedule_date, actual_date in teaching_dates:
            effective_teacher_code = _effective_teacher_code(batch, workload, actual_date)
            for slots in sequences:
                cutoff = cutoffs.get(actual_date)
                if cutoff is not None and any(slot.ends_at > cutoff for slot in slots):
                    continue
                if not _resource_is_available(
                    availability,
                    ResourceType.TEACHER,
                    effective_teacher_code,
                    actual_date,
                    slots,
                ) or not _resource_is_available(
                    availability,
                    ResourceType.GROUP,
                    workload.group_code,
                    actual_date,
                    slots,
                ):
                    continue
                for room in rooms:
                    if not _resource_is_available(
                        availability,
                        ResourceType.ROOM,
                        room.room_code,
                        actual_date,
                        slots,
                    ):
                        continue
                    options.append(
                        PlacementOption(
                            lesson_date=actual_date,
                            teaching_week_start=_monday(schedule_date),
                            slot_codes=tuple(slot.slot_code for slot in slots),
                            room_code=room.room_code,
                            teacher_code=effective_teacher_code,
                        )
                    )
                    total_options += 1
                    if total_options > MAX_PLACEMENT_OPTIONS:
                        diagnostics.append(
                            SolverDiagnostic(
                                severity=DiagnosticSeverity.ERROR,
                                code="placement_option_limit_exceeded",
                                message=(
                                    "Объём задачи превышает безопасный предел "
                                    "в 1 000 000 вариантов размещения"
                                ),
                                section="workloads",
                                remediation=(
                                    "Разделите расчёт по семестрам или сократите "
                                    "допустимые ресурсы."
                                ),
                            )
                        )
                        return (), tuple(diagnostics)
        ordered_options = tuple(
            sorted(
                set(options),
                key=lambda item: (
                    item.lesson_date,
                    item.slot_codes,
                    item.room_code,
                    item.teaching_week_start,
                ),
            )
        )
        if not ordered_options:
            diagnostics.append(
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="no_eligible_placements",
                    message="Для строки нагрузки нет допустимых размещений",
                    section="workloads",
                    object_code=workload.workload_row_code,
                    remediation=(
                        "Сверьте календарь, сетку звонков, аудитории и "
                        "недоступность ресурсов."
                    ),
                )
            )
        domains.append(
            WorkloadPlacementDomain(
                workload_row_code=workload.workload_row_code,
                options=ordered_options,
            )
        )
    return tuple(domains), tuple(diagnostics)
