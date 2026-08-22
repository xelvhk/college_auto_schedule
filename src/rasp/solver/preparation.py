from __future__ import annotations

from datetime import date, timedelta

from rasp.application.cycles import workload_applies_on_date
from rasp.application.readiness import (
    ReadinessSeverity,
    analyze_schedule_readiness,
)
from rasp.domain.models import (
    CalendarPeriod,
    CalendarPeriodType,
    ImportBatch,
    WorkloadItem,
)
from rasp.solver.contracts import (
    DiagnosticSeverity,
    LessonDemand,
    SolverDiagnostic,
    SolverProblem,
)
from rasp.solver.placements import build_placement_domains


MAX_LESSON_DEMANDS = 100_000
TWO_STAGE_DEMAND_THRESHOLD = 5_000


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _period_week_starts(period: CalendarPeriod) -> tuple[date, ...]:
    starts: list[date] = []
    current = _monday(period.starts_on)
    last = _monday(period.ends_on)
    while current <= last:
        starts.append(current)
        current += timedelta(days=7)
    return tuple(starts)


def _eligible_weeks(batch: ImportBatch, workload: WorkloadItem) -> tuple[date, ...]:
    periods = sorted(
        (
            period
            for period in batch.calendar_periods
            if period.period_type is CalendarPeriodType.TEACHING
            and period.academic_year == workload.academic_year
            and period.semester == workload.semester
        ),
        key=lambda period: (period.starts_on, period.ends_on, period.period_code),
    )
    week_starts = sorted(
        {week for period in periods for week in _period_week_starts(period)}
    )
    if workload.cycle_code is None:
        return tuple(week_starts)
    cycles = {
        cycle.cycle_code: cycle
        for cycle in batch.academic_cycles
        if cycle.active
    }
    cycle = cycles.get(workload.cycle_code)
    if cycle is None:
        return ()
    return tuple(
        week
        for week in week_starts
        if workload_applies_on_date(workload, week, cycle)
    )


def _readiness_diagnostics(batch: ImportBatch) -> list[SolverDiagnostic]:
    report = analyze_schedule_readiness(batch)
    severity = {
        ReadinessSeverity.ERROR: DiagnosticSeverity.ERROR,
        ReadinessSeverity.WARNING: DiagnosticSeverity.WARNING,
    }
    return [
        SolverDiagnostic(
            severity=severity[issue.severity],
            code=issue.code,
            message=issue.message,
            section=issue.section,
            object_code=issue.object_code,
            remediation=issue.remediation,
        )
        for issue in report.issues
    ]


def _sort_diagnostics(diagnostics: list[SolverDiagnostic]) -> None:
    severity_order = {
        DiagnosticSeverity.ERROR: 0,
        DiagnosticSeverity.WARNING: 1,
    }
    diagnostics.sort(
        key=lambda item: (
            severity_order[item.severity],
            item.code,
            item.section or "",
            item.object_code or "",
        )
    )


def build_solver_problem(
    batch: ImportBatch,
    *,
    defer_placement_domains: bool = False,
) -> SolverProblem:
    """Deterministically expand validated workload rows into lesson demands."""

    demands: list[LessonDemand] = []
    diagnostics = _readiness_diagnostics(batch)
    ordered_workloads = sorted(
        batch.workloads,
        key=lambda item: item.workload_row_code,
    )
    demand_count = sum(
        workload.total_academic_hours // workload.event_duration_hours
        for workload in ordered_workloads
    )
    if demand_count > MAX_LESSON_DEMANDS:
        diagnostics.append(
            SolverDiagnostic(
                severity=DiagnosticSeverity.ERROR,
                code="lesson_demand_limit_exceeded",
                message=(
                    "Объём задачи превышает безопасный предел в 100 000 занятий"
                ),
                section="workloads",
                remediation=(
                    "Разделите расчёт по учебным годам или проверьте часы нагрузки."
                ),
            )
        )
        _sort_diagnostics(diagnostics)
        return SolverProblem(
            source_workload_count=len(ordered_workloads),
            demands=(),
            diagnostics=tuple(diagnostics),
        )
    groups = {group.group_code: group for group in batch.groups}

    for workload in ordered_workloads:
        eligible_weeks = _eligible_weeks(batch, workload)
        if not eligible_weeks:
            diagnostics.append(
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code="no_eligible_teaching_weeks",
                    message="Для строки нагрузки нет допустимых учебных недель",
                    section="workloads",
                    object_code=workload.workload_row_code,
                    remediation=(
                        "Сверьте учебный период, семестр и выбранный учебный цикл."
                    ),
                )
            )
        event_count = workload.total_academic_hours // workload.event_duration_hours
        group = groups.get(workload.group_code)
        required_capacity = workload.room_capacity or (
            group.headcount if group is not None else None
        )
        for number in range(1, event_count + 1):
            demands.append(
                LessonDemand(
                    demand_code=f"{workload.workload_row_code}#{number:03d}",
                    workload_row_code=workload.workload_row_code,
                    academic_year=workload.academic_year,
                    semester=workload.semester,
                    discipline_code=workload.discipline_code,
                    group_code=workload.group_code,
                    subgroup=workload.subgroup,
                    stream=workload.stream,
                    teacher_code=workload.teacher_code,
                    lesson_type=workload.lesson_type,
                    duration_academic_hours=workload.event_duration_hours,
                    lesson_bundle_code=workload.lesson_bundle_code,
                    required_room_type=workload.room_type,
                    required_room_capacity=required_capacity,
                    required_equipment_codes=workload.required_equipment_codes,
                    eligible_week_starts=eligible_weeks,
                )
            )

    if defer_placement_domains or demand_count > TWO_STAGE_DEMAND_THRESHOLD:
        diagnostics.append(
            SolverDiagnostic(
                severity=DiagnosticSeverity.WARNING,
                code="two_stage_solver_required",
                message=(
                    "Семестровые варианты будут размещаться "
                    "по неделям, "
                    "чтобы ограничить размер модели"
                ),
                section="solver",
            )
        )
        _sort_diagnostics(diagnostics)
        return SolverProblem(
            source_workload_count=len(ordered_workloads),
            demands=tuple(demands),
            diagnostics=tuple(diagnostics),
        )

    placement_domains, placement_diagnostics = build_placement_domains(batch)
    diagnostics.extend(placement_diagnostics)
    _sort_diagnostics(diagnostics)
    return SolverProblem(
        source_workload_count=len(ordered_workloads),
        demands=tuple(demands),
        diagnostics=tuple(diagnostics),
        placement_domains=placement_domains,
    )
