from __future__ import annotations

import tracemalloc
from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from rasp.solver import (
    ScheduleSolver,
    SolverOptions,
    SolverProblem,
    SolverStatus,
    estimate_solver_complexity,
    find_assignment_conflicts,
)


class SolverBenchmarkReport(BaseModel):
    """Serializable evidence produced by every solver benchmark run."""

    model_config = ConfigDict(frozen=True)

    dataset_name: str = Field(min_length=1, max_length=128)
    preparation_seconds: float = Field(ge=0)
    solving_seconds: float = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    workload_count: int = Field(ge=0)
    lesson_demand_count: int = Field(ge=0)
    placement_option_count: int = Field(ge=0)
    estimated_boolean_variable_count: int = Field(ge=0)
    estimated_constraint_count: int = Field(ge=0)
    solver_status: SolverStatus
    assignment_count: int = Field(ge=0)
    completion_ratio: float = Field(ge=0, le=1)
    hard_conflict_count: int = Field(ge=0)
    diagnostic_codes: tuple[str, ...] = ()


def run_solver_benchmark(
    *,
    dataset_name: str,
    problem_factory: Callable[[], SolverProblem],
    solver: ScheduleSolver,
    options: SolverOptions,
) -> SolverBenchmarkReport:
    """Measure preparation and solving without persisting source data."""

    tracemalloc.start()
    try:
        preparation_started = perf_counter()
        problem = problem_factory()
        preparation_seconds = perf_counter() - preparation_started
        complexity = estimate_solver_complexity(problem)

        solving_started = perf_counter()
        result = solver.solve(problem, options)
        solving_seconds = perf_counter() - solving_started
        _, peak_memory_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    conflicts = find_assignment_conflicts(result.assignments)
    demand_count = len(problem.demands)
    completion_ratio = (
        len(result.assignments) / demand_count if demand_count else 0.0
    )
    return SolverBenchmarkReport(
        dataset_name=dataset_name,
        preparation_seconds=preparation_seconds,
        solving_seconds=solving_seconds,
        peak_memory_bytes=peak_memory_bytes,
        workload_count=problem.source_workload_count,
        lesson_demand_count=demand_count,
        placement_option_count=complexity.placement_option_count,
        estimated_boolean_variable_count=complexity.boolean_variable_count,
        estimated_constraint_count=(
            complexity.exactly_one_constraint_count
            + complexity.ordering_constraint_count
        ),
        solver_status=result.status,
        assignment_count=len(result.assignments),
        completion_ratio=completion_ratio,
        hard_conflict_count=len(conflicts),
        diagnostic_codes=tuple(item.code for item in result.diagnostics),
    )
