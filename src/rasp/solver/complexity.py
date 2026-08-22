from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from rasp.solver.contracts import SolverProblem


@dataclass(frozen=True, slots=True)
class SolverComplexityEstimate:
    """Exact size estimate for the current full-domain CP-SAT encoding."""

    lesson_demand_count: int
    placement_option_count: int
    boolean_variable_count: int
    exactly_one_constraint_count: int
    ordering_constraint_count: int
    resource_usage_reference_count: int


def estimate_solver_complexity(problem: SolverProblem) -> SolverComplexityEstimate:
    """Count objects the full-domain solver would create without building it."""

    demands_by_workload = Counter(
        demand.workload_row_code for demand in problem.demands
    )
    placement_option_count = 0
    boolean_variable_count = 0
    resource_usage_reference_count = 0
    for domain in problem.placement_domains:
        demand_count = demands_by_workload[domain.workload_row_code]
        placement_option_count += len(domain.options)
        boolean_variable_count += demand_count * len(domain.options)
        resource_references_per_demand = sum(
            len(option.slot_codes) * 3 for option in domain.options
        )
        resource_usage_reference_count += (
            demand_count * resource_references_per_demand
        )

    return SolverComplexityEstimate(
        lesson_demand_count=len(problem.demands),
        placement_option_count=placement_option_count,
        boolean_variable_count=boolean_variable_count,
        exactly_one_constraint_count=len(problem.demands),
        ordering_constraint_count=sum(
            max(demand_count - 1, 0)
            for demand_count in demands_by_workload.values()
        ),
        resource_usage_reference_count=resource_usage_reference_count,
    )
