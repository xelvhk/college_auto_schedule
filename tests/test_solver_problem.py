from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from rasp.domain.models import ImportBatch
from rasp.imports.excel import read_import_workbook
from rasp.solver import (
    DiagnosticSeverity,
    ScheduleAssignment,
    build_solver_problem,
    find_assignment_conflicts,
)


FIXTURES = Path(__file__).parent / "fixtures"


class SolverProblemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = read_import_workbook(FIXTURES / "valid-import.xlsx")

    def test_canonical_workload_expands_into_deterministic_lesson_demands(self) -> None:
        first = build_solver_problem(self.batch)
        second = build_solver_problem(self.batch)

        self.assertEqual(first, second)
        self.assertTrue(first.is_ready)
        self.assertEqual(len(first.demands), 36)
        self.assertEqual(first.demands[0].demand_code, "W-001#001")
        self.assertEqual(first.demands[-1].demand_code, "W-001#036")
        self.assertEqual(
            first.demands[0].eligible_week_starts,
            (
                date(2026, 8, 31),
                date(2026, 9, 14),
                date(2026, 9, 28),
                date(2026, 10, 12),
                date(2026, 10, 26),
                date(2026, 11, 9),
                date(2026, 11, 23),
                date(2026, 12, 7),
                date(2026, 12, 21),
            ),
        )

    def test_workload_without_eligible_teaching_week_is_blocking(self) -> None:
        batch = ImportBatch(
            **(
                self.batch.model_dump()
                | {
                    "calendar_periods": tuple(
                        period.model_copy(update={"semester": 2})
                        for period in self.batch.calendar_periods
                    )
                }
            )
        )

        problem = build_solver_problem(batch)

        self.assertFalse(problem.is_ready)
        self.assertEqual(len(problem.demands), 36)
        self.assertEqual(problem.demands[0].eligible_week_starts, ())
        self.assertIn(
            "no_eligible_teaching_weeks",
            {diagnostic.code for diagnostic in problem.diagnostics},
        )

    def test_oversized_problem_is_rejected_before_demands_are_materialized(self) -> None:
        workload = self.batch.workloads[0].model_copy(
            update={"total_academic_hours": 6, "event_duration_hours": 2}
        )
        batch = self.batch.model_copy(update={"workloads": (workload,)})

        with patch("rasp.solver.preparation.MAX_LESSON_DEMANDS", 2):
            problem = build_solver_problem(batch)

        self.assertFalse(problem.is_ready)
        self.assertEqual(problem.source_workload_count, 1)
        self.assertEqual(problem.demands, ())
        self.assertIn(
            "lesson_demand_limit_exceeded",
            {diagnostic.code for diagnostic in problem.diagnostics},
        )


class AssignmentConflictTests(unittest.TestCase):
    def assignment(self, demand_code: str, **changes: object) -> ScheduleAssignment:
        values: dict[str, object] = {
            "demand_code": demand_code,
            "teacher_code": "T-001",
            "group_code": "ИС-101",
            "room_code": "R-101",
            "lesson_date": "2026-09-01",
            "slot_code": "S1-01",
        }
        values.update(changes)
        return ScheduleAssignment(**values)

    def test_same_slot_reports_each_double_booked_resource(self) -> None:
        conflicts = find_assignment_conflicts(
            (self.assignment("W-001#001"), self.assignment("W-002#001"))
        )

        self.assertEqual(
            [conflict.code for conflict in conflicts],
            ["group_double_booking", "room_double_booking", "teacher_double_booking"],
        )
        self.assertTrue(
            all(conflict.severity is DiagnosticSeverity.ERROR for conflict in conflicts)
        )
        self.assertTrue(
            all(
                conflict.demand_codes == ("W-001#001", "W-002#001")
                for conflict in conflicts
            )
        )

    def test_different_date_or_slot_does_not_conflict(self) -> None:
        conflicts = find_assignment_conflicts(
            (
                self.assignment("W-001#001"),
                self.assignment("W-002#001", slot_code="S1-02"),
                self.assignment("W-003#001", lesson_date="2026-09-02"),
            )
        )

        self.assertEqual(conflicts, ())


if __name__ == "__main__":
    unittest.main()
