from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from time import perf_counter

from rasp.domain.models import ImportBatch
from rasp.solver.conflicts import find_assignment_conflicts
from rasp.solver.contracts import (
    DiagnosticSeverity,
    LessonDemand,
    PlacementOption,
    ScheduleAssignment,
    SolverDiagnostic,
    SolverMode,
    SolverOptions,
    SolverProblem,
    SolverResult,
    SolverStatus,
    WorkloadPlacementDomain,
)
from rasp.solver.cp_sat import CpSatScheduleSolver
from rasp.solver.placements import build_placement_domains
from rasp.solver.preparation import build_solver_problem


@dataclass(frozen=True, slots=True)
class TwoStageSolverMetrics:
    week_count: int = 0
    generated_placement_option_count: int = 0
    max_week_demand_count: int = 0
    max_week_placement_option_count: int = 0


class TwoStageScheduleSolver:
    """Allocate workload volume to weeks, then solve bounded weekly models."""

    def __init__(
        self,
        *,
        room_candidate_limit: int = 10,
        max_weekly_attempts: int = 3,
    ) -> None:
        if room_candidate_limit < 1:
            raise ValueError("room_candidate_limit must be positive")
        if max_weekly_attempts < 1:
            raise ValueError("max_weekly_attempts must be positive")
        self.room_candidate_limit = room_candidate_limit
        self.max_weekly_attempts = max_weekly_attempts
        self.last_metrics = TwoStageSolverMetrics()

    def solve_batch(
        self,
        batch: ImportBatch,
        options: SolverOptions,
    ) -> SolverResult:
        base_problem = build_solver_problem(
            batch,
            defer_placement_domains=True,
        )
        direct_solver = CpSatScheduleSolver()
        generated_option_count = 0
        max_week_demand_count = 0
        max_week_option_count = 0
        if not base_problem.is_ready or options.mode is not SolverMode.COMPLETE:
            return direct_solver.solve(base_problem, options)

        started_at = perf_counter()
        demands_by_workload: defaultdict[str, list[LessonDemand]] = defaultdict(list)
        for demand in sorted(base_problem.demands, key=lambda item: item.demand_code):
            demands_by_workload[demand.workload_row_code].append(demand)

        demands_by_week: defaultdict[date, list[LessonDemand]] = defaultdict(list)
        for workload_code in sorted(demands_by_workload):
            demands = demands_by_workload[workload_code]
            capacity_domains, capacity_diagnostics = build_placement_domains(
                batch,
                workload_codes={workload_code},
                room_candidate_limit=1,
            )
            generated_option_count += sum(
                len(domain.options) for domain in capacity_domains
            )
            if capacity_diagnostics or not capacity_domains:
                return self._failed(
                    options,
                    "two_stage_missing_week_capacity",
                    (
                        "Для части нагрузки нет доступной ёмкости "
                        "по неделям."
                    ),
                )
            placement_keys: defaultdict[date, set[tuple[object, ...]]] = defaultdict(
                set
            )
            for placement in capacity_domains[0].options:
                placement_keys[placement.teaching_week_start].add(
                    (
                        placement.lesson_date,
                        placement.slot_codes,
                        placement.teacher_code,
                    )
                )
            capacities = {
                week: len(keys)
                for week, keys in placement_keys.items()
                if week in demands[0].eligible_week_starts
            }
            if sum(capacities.values()) < len(demands):
                return self._failed(
                    options,
                    "two_stage_insufficient_week_capacity",
                    (
                        "Для строки нагрузки недостаточно недельной "
                        "ёмкости."
                    ),
                )
            allocated: defaultdict[date, int] = defaultdict(int)
            for demand in demands:
                available_weeks = [
                    week
                    for week, capacity in capacities.items()
                    if allocated[week] < capacity
                ]
                selected_week = min(
                    available_weeks,
                    key=lambda week: (
                        allocated[week] / capacities[week],
                        allocated[week],
                        week,
                    ),
                )
                demands_by_week[selected_week].append(demand)
                allocated[selected_week] += 1

        assignments = []
        ordered_weeks = sorted(demands_by_week)
        for week_start in ordered_weeks:
            week_demands = tuple(
                sorted(demands_by_week[week_start], key=lambda item: item.demand_code)
            )
            max_week_demand_count = max(
                max_week_demand_count,
                len(week_demands),
            )
            workload_codes = {
                demand.workload_row_code for demand in week_demands
            }
            weekly_assignments: tuple[ScheduleAssignment, ...] | None = None
            for attempt in range(self.max_weekly_attempts):
                elapsed = perf_counter() - started_at
                remaining = options.time_limit_seconds - elapsed
                if remaining <= 0:
                    return self._failed(
                        options,
                        "two_stage_timeout",
                        (
                            "Двухэтапный расчёт исчерпал общий лимит "
                            "времени."
                        ),
                    )
                placement_domains, placement_diagnostics = build_placement_domains(
                    batch,
                    workload_codes=workload_codes,
                    teaching_week_starts={week_start},
                    room_candidate_limit=(
                        self.room_candidate_limit * (attempt + 1)
                    ),
                    room_candidate_offset=attempt,
                )
                weekly_option_count = sum(
                    len(domain.options) for domain in placement_domains
                )
                generated_option_count += weekly_option_count
                max_week_option_count = max(
                    max_week_option_count,
                    weekly_option_count,
                )
                if placement_diagnostics:
                    continue
                weekly_assignments = self._solve_week_greedily(
                    week_demands,
                    placement_domains,
                    seed=options.seed,
                    attempt=attempt,
                )
                if weekly_assignments is not None:
                    assignments.extend(weekly_assignments)
                    break
            if weekly_assignments is None:
                return self._failed(
                    options,
                    "two_stage_week_infeasible",
                    (
                        "Не удалось разместить нагрузку учебной недели "
                        f"{week_start.isoformat()}."
                    ),
                )

        ordered_assignments = tuple(
            sorted(assignments, key=lambda item: item.demand_code)
        )
        self.last_metrics = TwoStageSolverMetrics(
            week_count=len(ordered_weeks),
            generated_placement_option_count=generated_option_count,
            max_week_demand_count=max_week_demand_count,
            max_week_placement_option_count=max_week_option_count,
        )
        if conflicts := find_assignment_conflicts(ordered_assignments):
            return SolverResult(
                status=SolverStatus.UNKNOWN,
                diagnostics=tuple(conflicts),
                seed=options.seed,
            )
        return SolverResult(
            status=SolverStatus.FEASIBLE,
            assignments=ordered_assignments,
            seed=options.seed,
        )

    @staticmethod
    def _solve_week_greedily(
        demands: tuple[LessonDemand, ...],
        placement_domains: tuple[WorkloadPlacementDomain, ...],
        *,
        seed: int,
        attempt: int,
    ) -> tuple[ScheduleAssignment, ...] | None:
        domains = {
            domain.workload_row_code: domain.options
            for domain in placement_domains
        }

        def demand_order(demand: LessonDemand) -> tuple[object, ...]:
            digest = sha256(
                f"{seed}:{attempt}:{demand.demand_code}".encode("utf-8")
            ).digest()
            return (
                len(domains.get(demand.workload_row_code, ())),
                digest,
                demand.demand_code,
            )

        occupied: set[tuple[str, str, date, str]] = set()
        selected_by_workload: defaultdict[str, list[PlacementOption]] = defaultdict(
            list
        )
        demands_by_workload: defaultdict[str, list[LessonDemand]] = defaultdict(list)
        for demand in demands:
            demands_by_workload[demand.workload_row_code].append(demand)

        for demand in sorted(demands, key=demand_order):
            options = domains.get(demand.workload_row_code, ())
            if not options:
                return None
            option_offset = int.from_bytes(
                sha256(
                    f"{seed}:{attempt}:{demand.demand_code}:option".encode("utf-8")
                ).digest()[:8],
                "big",
            ) % len(options)
            selected = None
            for index in range(len(options)):
                placement = options[(option_offset + index) % len(options)]
                usage = {
                    (resource_type, resource_code, placement.lesson_date, slot_code)
                    for slot_code in placement.slot_codes
                    for resource_type, resource_code in (
                        ("group", demand.group_code),
                        ("teacher", placement.teacher_code),
                        ("room", placement.room_code),
                    )
                }
                if occupied.isdisjoint(usage):
                    selected = placement
                    occupied.update(usage)
                    break
            if selected is None:
                return None
            selected_by_workload[demand.workload_row_code].append(selected)

        assignments: list[ScheduleAssignment] = []
        for workload_code in sorted(demands_by_workload):
            ordered_demands = sorted(
                demands_by_workload[workload_code],
                key=lambda item: item.demand_code,
            )
            ordered_placements = sorted(
                selected_by_workload[workload_code],
                key=lambda item: (
                    item.lesson_date,
                    item.slot_codes,
                    item.room_code,
                ),
            )
            for demand, placement in zip(ordered_demands, ordered_placements):
                assignments.append(
                    ScheduleAssignment(
                        demand_code=demand.demand_code,
                        teacher_code=placement.teacher_code,
                        group_code=demand.group_code,
                        room_code=placement.room_code,
                        lesson_date=placement.lesson_date,
                        slot_code=placement.slot_codes[0],
                        occupied_slot_codes=placement.slot_codes,
                    )
                )
        return tuple(assignments)

    @staticmethod
    def _failed(
        options: SolverOptions,
        code: str,
        message: str,
    ) -> SolverResult:
        return SolverResult(
            status=SolverStatus.UNKNOWN,
            diagnostics=(
                SolverDiagnostic(
                    severity=DiagnosticSeverity.ERROR,
                    code=code,
                    message=message,
                    section="solver",
                    remediation=(
                        "Проверьте доступность ресурсов или увеличьте "
                        "лимит времени."
                    ),
                ),
            ),
            seed=options.seed,
        )
