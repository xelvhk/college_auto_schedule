from __future__ import annotations

from rasp.domain.models import ImportBatch
from rasp.solver.contracts import SolverOptions, SolverProblem, SolverResult
from rasp.solver.cp_sat import CpSatScheduleSolver
from rasp.solver.preparation import build_solver_problem
from rasp.solver.two_stage import TwoStageScheduleSolver


def solve_schedule_batch(
    batch: ImportBatch,
    options: SolverOptions,
) -> tuple[SolverProblem, SolverResult]:
    """Select the bounded solver strategy for a normalized active batch."""

    problem = build_solver_problem(batch)
    requires_two_stage = any(
        diagnostic.code == "two_stage_solver_required"
        for diagnostic in problem.diagnostics
    )
    if requires_two_stage and problem.is_ready:
        result = TwoStageScheduleSolver().solve_batch(batch, options)
    else:
        result = CpSatScheduleSolver().solve(problem, options)
    return problem, result
