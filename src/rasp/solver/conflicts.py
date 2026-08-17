from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Sequence

from rasp.solver.contracts import (
    DiagnosticSeverity,
    ScheduleAssignment,
    SolverDiagnostic,
)


def find_assignment_conflicts(
    assignments: Sequence[ScheduleAssignment],
) -> tuple[SolverDiagnostic, ...]:
    """Return deterministic hard conflicts for already placed lessons."""

    resources = (
        (
            "group",
            "group_code",
            "group_double_booking",
            "Учебная группа назначена на два занятия одновременно",
        ),
        (
            "room",
            "room_code",
            "room_double_booking",
            "Аудитория назначена на два занятия одновременно",
        ),
        (
            "teacher",
            "teacher_code",
            "teacher_double_booking",
            "Преподаватель назначен на два занятия одновременно",
        ),
    )
    conflicts: list[SolverDiagnostic] = []
    for section, attribute, code, message in resources:
        placements: defaultdict[tuple[date, str, str], list[str]] = defaultdict(list)
        for assignment in assignments:
            key = (
                assignment.lesson_date,
                assignment.slot_code,
                getattr(assignment, attribute),
            )
            placements[key].append(assignment.demand_code)
        for (lesson_date, slot_code, object_code), demand_codes in placements.items():
            if len(demand_codes) < 2:
                continue
            conflicts.append(
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code=code,
                    message=message,
                    section=section,
                    object_code=object_code,
                    lesson_date=lesson_date,
                    slot_code=slot_code,
                    demand_codes=tuple(sorted(demand_codes)),
                    remediation="Перенесите одно из занятий в другой интервал.",
                )
            )
    conflicts.sort(
        key=lambda item: (
            item.code,
            item.lesson_date or date.min,
            item.slot_code or "",
            item.object_code or "",
        )
    )
    return tuple(conflicts)
