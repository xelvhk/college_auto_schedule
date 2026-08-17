from __future__ import annotations

from rasp.solver.contracts import (
    DiagnosticSeverity,
    SolverDiagnostic,
    SolverProblem,
    SolverResult,
)


def _diagnostic_payload(diagnostic: SolverDiagnostic) -> dict[str, object]:
    return {
        "severity": diagnostic.severity.value,
        "code": diagnostic.code,
        "message": diagnostic.message,
        "section": diagnostic.section,
        "objectCode": diagnostic.object_code,
        "lessonDate": diagnostic.lesson_date.isoformat()
        if diagnostic.lesson_date
        else None,
        "slotCode": diagnostic.slot_code,
        "demandCodes": diagnostic.demand_codes,
        "remediation": diagnostic.remediation,
    }


def solver_problem_payload(problem: SolverProblem) -> dict[str, object]:
    eligible_weeks = {
        week
        for demand in problem.demands
        for week in demand.eligible_week_starts
    }
    return {
        "isReady": problem.is_ready,
        "workloadCount": problem.source_workload_count,
        "lessonDemandCount": len(problem.demands),
        "eligibleWeekCount": len(eligible_weeks),
        "placementDomainCount": len(problem.placement_domains),
        "placementOptionCount": sum(
            len(domain.options) for domain in problem.placement_domains
        ),
        "errorCount": sum(
            item.severity is DiagnosticSeverity.ERROR
            for item in problem.diagnostics
        ),
        "warningCount": sum(
            item.severity is DiagnosticSeverity.WARNING
            for item in problem.diagnostics
        ),
        "diagnostics": [
            _diagnostic_payload(diagnostic)
            for diagnostic in problem.diagnostics
        ],
        "demandSamples": [
            {
                "demandCode": demand.demand_code,
                "workloadRowCode": demand.workload_row_code,
                "groupCode": demand.group_code,
                "teacherCode": demand.teacher_code,
                "disciplineCode": demand.discipline_code,
                "durationAcademicHours": demand.duration_academic_hours,
                "eligibleWeekStarts": [
                    week.isoformat() for week in demand.eligible_week_starts
                ],
            }
            for demand in problem.demands[:5]
        ],
        "placementDomainSamples": [
            {
                "workloadRowCode": domain.workload_row_code,
                "optionCount": len(domain.options),
                "options": [
                    {
                        "lessonDate": option.lesson_date.isoformat(),
                        "teachingWeekStart": option.teaching_week_start.isoformat(),
                        "slotCodes": option.slot_codes,
                        "roomCode": option.room_code,
                    }
                    for option in domain.options[:5]
                ],
            }
            for domain in problem.placement_domains[:5]
        ],
    }


def solver_result_payload(
    result: SolverResult,
    problem: SolverProblem,
) -> dict[str, object]:
    demands = {item.demand_code: item for item in problem.demands}
    assignments: list[dict[str, object]] = []
    for assignment in result.assignments:
        demand = demands[assignment.demand_code]
        assignments.append(
            {
                "demandCode": assignment.demand_code,
                "workloadRowCode": demand.workload_row_code,
                "disciplineCode": demand.discipline_code,
                "groupCode": assignment.group_code,
                "teacherCode": assignment.teacher_code,
                "lessonDate": assignment.lesson_date.isoformat(),
                "slotCode": assignment.slot_code,
                "occupiedSlotCodes": assignment.occupied_slot_codes
                or (assignment.slot_code,),
                "roomCode": assignment.room_code,
            }
        )
    return {
        "status": result.status.value,
        "seed": result.seed,
        "assignmentCount": len(assignments),
        "assignments": assignments,
        "diagnostics": [
            _diagnostic_payload(diagnostic) for diagnostic in result.diagnostics
        ],
    }
