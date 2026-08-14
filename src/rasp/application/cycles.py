from __future__ import annotations

from datetime import date, timedelta

from rasp.domain.models import AcademicCycle, WorkloadItem


def cycle_week_number(cycle: AcademicCycle, target_date: date) -> int:
    """Return the one-based cycle position of the week containing target_date."""

    anchor_monday = cycle.anchor_date - timedelta(days=cycle.anchor_date.weekday())
    target_monday = target_date - timedelta(days=target_date.weekday())
    weeks_from_anchor = (target_monday - anchor_monday).days // 7
    return weeks_from_anchor % cycle.cycle_length_weeks + 1


def workload_applies_on_date(
    workload: WorkloadItem,
    target_date: date,
    cycle: AcademicCycle | None = None,
) -> bool:
    """Return whether a workload row is active in target_date's academic week."""

    if workload.cycle_code is None:
        return True
    if cycle is None or cycle.cycle_code != workload.cycle_code:
        raise ValueError("workload requires its selected academic cycle")
    if cycle.academic_year != workload.academic_year:
        raise ValueError("workload and academic cycle years must match")
    return cycle_week_number(cycle, target_date) in workload.cycle_week_numbers
