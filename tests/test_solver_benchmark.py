from __future__ import annotations

import unittest

from rasp.application.imports import validate_activation_invariants
from rasp.application.readiness import analyze_schedule_readiness
from rasp.benchmarks import (
    SyntheticCollegeScale,
    build_synthetic_college,
    run_solver_benchmark,
)
from rasp.solver import CpSatScheduleSolver, SolverOptions, build_solver_problem


class SyntheticSolverBenchmarkTests(unittest.TestCase):
    def test_default_dataset_matches_medium_college_target(self) -> None:
        scale = SyntheticCollegeScale()
        batch = build_synthetic_college(scale)

        self.assertEqual(len(batch.groups), 40)
        self.assertEqual(len(batch.teachers), 100)
        self.assertEqual(len(batch.rooms), 60)
        self.assertEqual(len(batch.workloads), 400)
        self.assertEqual(len(batch.students), 1_000)
        self.assertEqual(scale.lesson_count, 20_000)
        self.assertEqual(
            sum(
                item.total_academic_hours // item.event_duration_hours
                for item in batch.workloads
            ),
            20_000,
        )

    def test_dataset_is_deterministic_and_ready(self) -> None:
        first = build_synthetic_college()
        second = build_synthetic_college()

        self.assertEqual(first, second)
        validate_activation_invariants(first)
        report = analyze_schedule_readiness(first)
        self.assertTrue(report.is_ready)
        self.assertEqual(report.error_count, 0)

    def test_small_benchmark_reports_complete_conflict_free_result(self) -> None:
        scale = SyntheticCollegeScale(
            group_count=1,
            teacher_count=2,
            room_count=1,
            disciplines_per_group=1,
            events_per_workload=2,
            students_per_group=5,
            teaching_week_count=2,
        )
        batch = build_synthetic_college(scale)

        report = run_solver_benchmark(
            dataset_name="unit-small",
            problem_factory=lambda: build_solver_problem(batch),
            solver=CpSatScheduleSolver(),
            options=SolverOptions(seed=0, time_limit_seconds=5),
        )

        self.assertEqual(report.lesson_demand_count, 2)
        self.assertGreater(report.placement_option_count, 0)
        self.assertGreater(report.estimated_boolean_variable_count, 0)
        self.assertEqual(report.assignment_count, 2)
        self.assertEqual(report.completion_ratio, 1)
        self.assertEqual(report.hard_conflict_count, 0)
        self.assertGreater(report.peak_memory_bytes, 0)
        self.assertGreater(report.python_peak_memory_bytes, 0)


if __name__ == "__main__":
    unittest.main()
