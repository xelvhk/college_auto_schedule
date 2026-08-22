from __future__ import annotations

import argparse
from pathlib import Path

from rasp.benchmarks import run_medium_college_benchmark
from rasp.solver import SolverOptions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the 20,000-lesson medium-college benchmark."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/benchmarks/solver-medium.json"),
    )
    parser.add_argument("--time-limit", type=int, default=300)
    args = parser.parse_args()

    report = run_medium_college_benchmark(
        options=SolverOptions(seed=0, time_limit_seconds=args.time_limit),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    passed = (
        report.assignment_count == 20_000
        and report.completion_ratio == 1
        and report.hard_conflict_count == 0
        and report.preparation_seconds + report.solving_seconds <= 300
        and report.peak_memory_bytes <= 4 * 1024**3
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
