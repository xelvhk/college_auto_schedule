from __future__ import annotations

import tracemalloc
import sys
from ctypes import Structure, byref, c_size_t, sizeof
from ctypes.wintypes import DWORD
from collections.abc import Callable
from time import perf_counter

from pydantic import BaseModel, ConfigDict, Field

from rasp.benchmarks.synthetic import SyntheticCollegeScale, build_synthetic_college
from rasp.solver import (
    ScheduleSolver,
    SolverOptions,
    SolverProblem,
    SolverStatus,
    TwoStageScheduleSolver,
    build_solver_problem,
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
    python_peak_memory_bytes: int = Field(ge=0)
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
    strategy: str = Field(default="full_domain_cp_sat", min_length=1, max_length=64)
    stage_count: int = Field(default=1, ge=0)


def _process_peak_memory_bytes() -> int:
    if sys.platform == "win32":
        from ctypes import POINTER, WinDLL, get_last_error
        from ctypes.wintypes import BOOL, HANDLE

        class ProcessMemoryCounters(Structure):
            _fields_ = (
                ("cb", DWORD),
                ("page_fault_count", DWORD),
                ("peak_working_set_size", c_size_t),
                ("working_set_size", c_size_t),
                ("quota_peak_paged_pool_usage", c_size_t),
                ("quota_paged_pool_usage", c_size_t),
                ("quota_peak_non_paged_pool_usage", c_size_t),
                ("quota_non_paged_pool_usage", c_size_t),
                ("pagefile_usage", c_size_t),
                ("peak_pagefile_usage", c_size_t),
            )

        counters = ProcessMemoryCounters()
        counters.cb = sizeof(counters)
        kernel32 = WinDLL("kernel32", use_last_error=True)
        psapi = WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            HANDLE,
            POINTER(ProcessMemoryCounters),
            DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = BOOL
        process = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            process,
            byref(counters),
            counters.cb,
        ):
            raise OSError(
                get_last_error(),
                "Unable to read process peak working set",
            )
        return int(counters.peak_working_set_size)

    import resource

    peak = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return peak if sys.platform == "darwin" else peak * 1024


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
        _, python_peak_memory_bytes = tracemalloc.get_traced_memory()
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
        peak_memory_bytes=_process_peak_memory_bytes(),
        python_peak_memory_bytes=python_peak_memory_bytes,
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


def run_medium_college_benchmark(
    *,
    options: SolverOptions,
    scale: SyntheticCollegeScale | None = None,
) -> SolverBenchmarkReport:
    """Run the complete deterministic pilot dataset through the staged solver."""

    tracemalloc.start()
    try:
        preparation_started = perf_counter()
        batch = build_synthetic_college(scale)
        problem = build_solver_problem(batch)
        preparation_seconds = perf_counter() - preparation_started

        solver = TwoStageScheduleSolver()
        solving_started = perf_counter()
        result = solver.solve_batch(batch, options)
        solving_seconds = perf_counter() - solving_started
        _, python_peak_memory_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    metrics = solver.last_metrics
    conflicts = find_assignment_conflicts(result.assignments)
    demand_count = len(problem.demands)
    return SolverBenchmarkReport(
        dataset_name="synthetic-medium-college",
        preparation_seconds=preparation_seconds,
        solving_seconds=solving_seconds,
        peak_memory_bytes=_process_peak_memory_bytes(),
        python_peak_memory_bytes=python_peak_memory_bytes,
        workload_count=problem.source_workload_count,
        lesson_demand_count=demand_count,
        placement_option_count=metrics.generated_placement_option_count,
        estimated_boolean_variable_count=0,
        estimated_constraint_count=0,
        solver_status=result.status,
        assignment_count=len(result.assignments),
        completion_ratio=(
            len(result.assignments) / demand_count if demand_count else 0.0
        ),
        hard_conflict_count=len(conflicts),
        diagnostic_codes=tuple(item.code for item in result.diagnostics),
        strategy="two_stage_weekly_greedy",
        stage_count=metrics.week_count,
    )
