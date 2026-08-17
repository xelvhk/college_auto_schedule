from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Protocol, runtime_checkable

from pydantic import Field, StringConstraints

from rasp.domain.models import Code, DomainModel, LessonType


DemandCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=128),
]
MAX_SOLVER_SEED = 2_147_483_647


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class SolverMode(StrEnum):
    DIFFICULT_FIRST = "difficult_first"
    COMPLETE = "complete"
    OPTIMIZE_ONLY = "optimize_only"


class SolverStatus(StrEnum):
    NOT_STARTED = "not_started"
    FEASIBLE = "feasible"
    INFEASIBLE = "infeasible"
    UNKNOWN = "unknown"


class SolverDiagnostic(DomainModel):
    severity: DiagnosticSeverity
    code: Code
    message: str = Field(min_length=1, max_length=500)
    section: str | None = Field(default=None, max_length=64)
    object_code: str | None = Field(default=None, max_length=64)
    lesson_date: date | None = None
    slot_code: Code | None = None
    demand_codes: tuple[DemandCode, ...] = ()
    remediation: str | None = Field(default=None, max_length=500)


class LessonDemand(DomainModel):
    demand_code: DemandCode
    workload_row_code: Code
    academic_year: str
    semester: int = Field(ge=1, le=2)
    discipline_code: Code
    group_code: Code
    subgroup: str | None = Field(default=None, max_length=64)
    stream: str | None = Field(default=None, max_length=64)
    teacher_code: Code
    lesson_type: LessonType
    duration_academic_hours: int = Field(ge=1, le=8)
    lesson_bundle_code: Code | None = None
    required_room_type: str | None = Field(default=None, max_length=64)
    required_room_capacity: int | None = Field(default=None, gt=0)
    required_equipment_codes: tuple[Code, ...] = ()
    eligible_week_starts: tuple[date, ...]


class PlacementOption(DomainModel):
    lesson_date: date
    teaching_week_start: date
    slot_codes: tuple[Code, ...] = Field(min_length=1)
    room_code: Code


class WorkloadPlacementDomain(DomainModel):
    workload_row_code: Code
    options: tuple[PlacementOption, ...]


class SolverProblem(DomainModel):
    source_workload_count: int = Field(ge=0)
    demands: tuple[LessonDemand, ...]
    diagnostics: tuple[SolverDiagnostic, ...]
    placement_domains: tuple[WorkloadPlacementDomain, ...] = ()

    @property
    def is_ready(self) -> bool:
        return all(
            diagnostic.severity is not DiagnosticSeverity.ERROR
            for diagnostic in self.diagnostics
        )


class SolverOptions(DomainModel):
    mode: SolverMode = SolverMode.COMPLETE
    seed: int = Field(default=0, ge=0, le=MAX_SOLVER_SEED)
    time_limit_seconds: int = Field(default=30, ge=1, le=300)


class ScheduleAssignment(DomainModel):
    demand_code: DemandCode
    teacher_code: Code
    group_code: Code
    room_code: Code
    lesson_date: date
    slot_code: Code
    occupied_slot_codes: tuple[Code, ...] = ()


class SolverResult(DomainModel):
    status: SolverStatus
    assignments: tuple[ScheduleAssignment, ...] = ()
    diagnostics: tuple[SolverDiagnostic, ...] = ()
    seed: int = Field(ge=0)


@runtime_checkable
class ScheduleSolver(Protocol):
    def solve(self, problem: SolverProblem, options: SolverOptions) -> SolverResult:
        """Build a schedule without depending on a concrete optimization engine."""
        ...
