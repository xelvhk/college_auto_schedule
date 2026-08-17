from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from rasp.imports.excel import read_import_workbook
from rasp.solver import (
    CpSatScheduleSolver,
    DiagnosticSeverity,
    LessonDemand,
    PlacementOption,
    SolverMode,
    SolverOptions,
    SolverProblem,
    SolverStatus,
    WorkloadPlacementDomain,
    build_solver_problem,
    find_assignment_conflicts,
)


FIXTURES = Path(__file__).parent / "fixtures"


def demand(code: str, workload: str, teacher: str = "T-001") -> LessonDemand:
    return LessonDemand(
        demand_code=code,
        workload_row_code=workload,
        academic_year="2026/2027",
        semester=1,
        discipline_code="D-001",
        group_code="G-001",
        teacher_code=teacher,
        lesson_type="lecture",
        duration_academic_hours=2,
        eligible_week_starts=(date(2026, 8, 31),),
    )


def option(day: str, room: str = "R-001", *slots: str) -> PlacementOption:
    return PlacementOption(
        lesson_date=day,
        teaching_week_start="2026-08-31",
        slot_codes=slots or ("S-01",),
        room_code=room,
    )


class CpSatScheduleSolverTests(unittest.TestCase):
    def test_canonical_problem_produces_complete_conflict_free_schedule(self) -> None:
        batch = read_import_workbook(FIXTURES / "valid-import.xlsx")
        problem = build_solver_problem(batch)

        first = CpSatScheduleSolver().solve(problem, SolverOptions(seed=7))
        second = CpSatScheduleSolver().solve(problem, SolverOptions(seed=7))

        self.assertEqual(first.status, SolverStatus.FEASIBLE)
        self.assertEqual(len(first.assignments), 36)
        self.assertEqual(first.assignments, second.assignments)
        self.assertEqual(find_assignment_conflicts(first.assignments), ())
        self.assertTrue(all(item.occupied_slot_codes for item in first.assignments))

        domains = {
            domain.workload_row_code: set(domain.options)
            for domain in problem.placement_domains
        }
        demands = {item.demand_code: item for item in problem.demands}
        for assignment in first.assignments:
            item = demands[assignment.demand_code]
            self.assertIn(
                PlacementOption(
                    lesson_date=assignment.lesson_date,
                    teaching_week_start=next(
                        placement.teaching_week_start
                        for placement in domains[item.workload_row_code]
                        if placement.lesson_date == assignment.lesson_date
                        and placement.slot_codes == assignment.occupied_slot_codes
                        and placement.room_code == assignment.room_code
                    ),
                    slot_codes=assignment.occupied_slot_codes,
                    room_code=assignment.room_code,
                ),
                domains[item.workload_row_code],
            )

    def test_resource_collision_makes_problem_infeasible(self) -> None:
        problem = SolverProblem(
            source_workload_count=2,
            demands=(demand("W-001#001", "W-001"), demand("W-002#001", "W-002")),
            diagnostics=(),
            placement_domains=(
                WorkloadPlacementDomain(
                    workload_row_code="W-001",
                    options=(option("2026-09-01"),),
                ),
                WorkloadPlacementDomain(
                    workload_row_code="W-002",
                    options=(option("2026-09-01"),),
                ),
            ),
        )

        result = CpSatScheduleSolver().solve(problem, SolverOptions())

        self.assertEqual(result.status, SolverStatus.INFEASIBLE)
        self.assertEqual(result.assignments, ())
        self.assertEqual(result.diagnostics[0].code, "schedule_infeasible")

    def test_second_occupied_slot_is_part_of_resource_constraints(self) -> None:
        problem = SolverProblem(
            source_workload_count=2,
            demands=(demand("W-001#001", "W-001"), demand("W-002#001", "W-002")),
            diagnostics=(),
            placement_domains=(
                WorkloadPlacementDomain(
                    workload_row_code="W-001",
                    options=(option("2026-09-01", "R-001", "S-01", "S-02"),),
                ),
                WorkloadPlacementDomain(
                    workload_row_code="W-002",
                    options=(option("2026-09-01", "R-002", "S-02"),),
                ),
            ),
        )

        result = CpSatScheduleSolver().solve(problem, SolverOptions())

        self.assertEqual(result.status, SolverStatus.INFEASIBLE)

    def test_not_ready_and_unsupported_modes_are_not_started(self) -> None:
        not_ready = SolverProblem(
            source_workload_count=0,
            demands=(),
            diagnostics=(
                {
                    "severity": DiagnosticSeverity.ERROR,
                    "code": "source_invalid",
                    "message": "Некорректные данные",
                },
            ),
        )

        invalid_result = CpSatScheduleSolver().solve(not_ready, SolverOptions())
        mode_result = CpSatScheduleSolver().solve(
            SolverProblem(source_workload_count=0, demands=(), diagnostics=()),
            SolverOptions(mode=SolverMode.OPTIMIZE_ONLY),
        )

        self.assertEqual(invalid_result.status, SolverStatus.NOT_STARTED)
        self.assertEqual(invalid_result.diagnostics[-1].code, "solver_problem_not_ready")
        self.assertEqual(mode_result.status, SolverStatus.NOT_STARTED)
        self.assertEqual(mode_result.diagnostics[-1].code, "unsupported_solver_mode")

    def test_time_limit_is_validated_at_the_contract_boundary(self) -> None:
        for value in (0, 301):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SolverOptions(time_limit_seconds=value)

    def test_seed_is_limited_to_the_cp_sat_integer_range(self) -> None:
        for value in (-1, 2_147_483_648):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                SolverOptions(seed=value)


if __name__ == "__main__":
    unittest.main()
