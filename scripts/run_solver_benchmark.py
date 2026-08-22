from __future__ import annotations

import argparse
from pathlib import Path

from rasp.benchmarks import (
    SyntheticCollegeScale,
    build_synthetic_college,
    run_solver_benchmark,
)
from rasp.solver import CpSatScheduleSolver, SolverOptions, build_solver_problem


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic small solver benchmark."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/solver-small.json"),
    )
    parser.add_argument("--time-limit", type=int, default=30)
    args = parser.parse_args()

    scale = SyntheticCollegeScale(
        group_count=2,
        teacher_count=5,
        room_count=3,
        disciplines_per_group=2,
        events_per_workload=4,
        students_per_group=10,
        teaching_week_count=3,
    )
    batch = build_synthetic_college(scale)
    report = run_solver_benchmark(
        dataset_name="synthetic-small-pr",
        problem_factory=lambda: build_solver_problem(batch),
        solver=CpSatScheduleSolver(),
        options=SolverOptions(seed=0, time_limit_seconds=args.time_limit),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if report.completion_ratio == 1 and report.hard_conflict_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
