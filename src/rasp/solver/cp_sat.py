from __future__ import annotations

from collections import defaultdict
from datetime import date

from ortools.sat.python import cp_model

from rasp.solver.contracts import (
    DiagnosticSeverity,
    ScheduleAssignment,
    SolverDiagnostic,
    SolverMode,
    SolverOptions,
    SolverProblem,
    SolverResult,
    SolverStatus,
)


class CpSatScheduleSolver:
    """Select one exact placement for every demand using hard resource rules."""

    def solve(self, problem: SolverProblem, options: SolverOptions) -> SolverResult:
        if not problem.is_ready:
            return self._not_started(
                problem,
                options,
                "solver_problem_not_ready",
                "Расчёт не запущен: устраните ошибки готовности исходных данных.",
            )
        if options.mode is not SolverMode.COMPLETE:
            return self._not_started(
                problem,
                options,
                "unsupported_solver_mode",
                "В этой версии поддерживается только полный расчёт расписания.",
            )

        domains = {
            domain.workload_row_code: domain.options
            for domain in problem.placement_domains
        }
        missing = sorted(
            {
                demand.workload_row_code
                for demand in problem.demands
                if not domains.get(demand.workload_row_code)
            }
        )
        if missing:
            return self._not_started(
                problem,
                options,
                "missing_placement_domain",
                "Для части нагрузки отсутствуют допустимые размещения.",
            )

        model = cp_model.CpModel()
        variables: dict[tuple[str, int], cp_model.IntVar] = {}
        resource_usage: defaultdict[
            tuple[str, str, date, str], list[cp_model.IntVar]
        ] = defaultdict(list)

        for demand in sorted(problem.demands, key=lambda item: item.demand_code):
            placements = domains[demand.workload_row_code]
            demand_variables: list[cp_model.IntVar] = []
            for index, placement in enumerate(placements):
                variable = model.new_bool_var(f"place_{demand.demand_code}_{index}")
                variables[(demand.demand_code, index)] = variable
                demand_variables.append(variable)
                for slot_code in placement.slot_codes:
                    for resource_type, resource_code in (
                        ("group", demand.group_code),
                        ("teacher", demand.teacher_code),
                        ("room", placement.room_code),
                    ):
                        resource_usage[
                            (
                                resource_type,
                                resource_code,
                                placement.lesson_date,
                                slot_code,
                            )
                        ].append(variable)
            model.add_exactly_one(demand_variables)

        demands_by_workload: defaultdict[str, list[str]] = defaultdict(list)
        for demand in sorted(problem.demands, key=lambda item: item.demand_code):
            demands_by_workload[demand.workload_row_code].append(demand.demand_code)
        for workload_code in sorted(demands_by_workload):
            demand_codes = demands_by_workload[workload_code]
            option_count = len(domains[workload_code])
            for previous, current in zip(demand_codes, demand_codes[1:]):
                previous_index = sum(
                    index * variables[(previous, index)]
                    for index in range(option_count)
                )
                current_index = sum(
                    index * variables[(current, index)]
                    for index in range(option_count)
                )
                model.add(previous_index < current_index)

        for key in sorted(resource_usage, key=lambda item: tuple(map(str, item))):
            used_variables = resource_usage[key]
            if len(used_variables) > 1:
                model.add_at_most_one(used_variables)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = options.time_limit_seconds
        solver.parameters.random_seed = options.seed
        solver.parameters.num_search_workers = 1
        status = solver.solve(model)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            assignments: list[ScheduleAssignment] = []
            for demand in sorted(problem.demands, key=lambda item: item.demand_code):
                placements = domains[demand.workload_row_code]
                selected = next(
                    placement
                    for index, placement in enumerate(placements)
                    if solver.value(variables[(demand.demand_code, index)])
                )
                assignments.append(
                    ScheduleAssignment(
                        demand_code=demand.demand_code,
                        teacher_code=demand.teacher_code,
                        group_code=demand.group_code,
                        room_code=selected.room_code,
                        lesson_date=selected.lesson_date,
                        slot_code=selected.slot_codes[0],
                        occupied_slot_codes=selected.slot_codes,
                    )
                )
            return SolverResult(
                status=SolverStatus.FEASIBLE,
                assignments=tuple(assignments),
                seed=options.seed,
            )

        if status == cp_model.INFEASIBLE:
            return SolverResult(
                status=SolverStatus.INFEASIBLE,
                diagnostics=(
                    SolverDiagnostic(
                        severity=DiagnosticSeverity.ERROR,
                        code="schedule_infeasible",
                        message="Расписание без пересечений для этих данных не найдено.",
                        section="solver",
                        remediation=(
                            "Добавьте доступные интервалы или аудитории и проверьте "
                            "ограничения преподавателей и групп."
                        ),
                    ),
                ),
                seed=options.seed,
            )

        model_invalid = status == cp_model.MODEL_INVALID
        return SolverResult(
            status=SolverStatus.UNKNOWN,
            diagnostics=(
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code=("solver_model_invalid" if model_invalid else "solver_timeout"),
                    message=(
                        "Внутренняя модель расчёта некорректна."
                        if model_invalid
                        else "За отведённое время допустимое расписание не найдено."
                    ),
                    section="solver",
                    remediation="Проверьте данные или увеличьте лимит времени.",
                ),
            ),
            seed=options.seed,
        )

    @staticmethod
    def _not_started(
        problem: SolverProblem,
        options: SolverOptions,
        code: str,
        message: str,
    ) -> SolverResult:
        return SolverResult(
            status=SolverStatus.NOT_STARTED,
            diagnostics=(
                *problem.diagnostics,
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code=code,
                    message=message,
                    section="solver",
                ),
            ),
            seed=options.seed,
        )
