from __future__ import annotations

import unittest

from rasp.application.readiness import (
    analyze_curriculum_alignment,
    analyze_room_supply,
    analyze_schedule_readiness,
)
from rasp.domain.models import (
    AcademicCycle,
    AcademicYear,
    BellSlot,
    Building,
    CalendarException,
    CalendarPeriod,
    Curriculum,
    CurriculumDiscipline,
    Group,
    ImportBatch,
    ReferenceDataBatch,
    Room,
    RoomType,
    ResourceUnavailability,
    Specialty,
    Teacher,
    TeacherReplacement,
    WorkloadItem,
)


def make_import_batch(*, hours: int = 72, discipline_code: str = "МДК.01.01") -> ImportBatch:
    return ImportBatch(
        teachers=(
            Teacher(
                teacher_code="T-001",
                full_name="Иванова Ирина Игоревна",
                yearly_assigned_hours=720,
            ),
        ),
        groups=(
            Group(
                group_code="ИС-101",
                specialty_code="09.02.07",
                curriculum_code="UP-09.02.07-2026",
                course=1,
                headcount=25,
            ),
        ),
        workloads=(
            WorkloadItem(
                workload_row_code="W-001",
                academic_year="2026/2027",
                semester=1,
                discipline_code=discipline_code,
                discipline_name="Разработка программных модулей",
                group_code="ИС-101",
                teacher_code="T-001",
                lesson_type="practice",
                total_academic_hours=hours,
                event_duration_hours=2,
            ),
        ),
    )


def make_reference_batch(*, planned_hours: int = 72) -> ReferenceDataBatch:
    return ReferenceDataBatch(
        specialties=(
            Specialty(
                specialty_code="09.02.07",
                specialty_name="Информационные системы и программирование",
                program_base="9",
                education_form="full_time",
            ),
        ),
        curricula=(
            Curriculum(
                curriculum_code="UP-09.02.07-2026",
                specialty_code="09.02.07",
                admission_year=2026,
                version="1",
                valid_from="2026-09-01",
                status="active",
            ),
        ),
        disciplines=(
            CurriculumDiscipline(
                curriculum_code="UP-09.02.07-2026",
                discipline_code="МДК.01.01",
                discipline_name="Разработка программных модулей",
                semester=1,
                lesson_type="practice",
                planned_hours=planned_hours,
            ),
        ),
    )


def make_ready_batch(*, hours: int = 72) -> ImportBatch:
    imports = make_import_batch(hours=hours)
    references = make_reference_batch()
    return imports.model_copy(
        update={
            "specialties": references.specialties,
            "curricula": references.curricula,
            "disciplines": references.disciplines,
            "buildings": (
                Building(building_code="MAIN", building_name="Главный корпус"),
            ),
            "room_types": (
                RoomType(
                    room_type_code="CLASSROOM",
                    room_type_name="Учебная аудитория",
                ),
            ),
            "rooms": (
                Room(
                    room_code="MAIN-101",
                    room_name="Аудитория 101",
                    building_code="MAIN",
                    room_type_code="CLASSROOM",
                    capacity=30,
                ),
            ),
            "academic_years": (
                AcademicYear(
                    academic_year="2026/2027",
                    starts_on="2026-09-01",
                    ends_on="2027-06-30",
                ),
            ),
            "calendar_periods": (
                CalendarPeriod(
                    period_code="SEM-1",
                    academic_year="2026/2027",
                    period_name="Первый семестр",
                    period_type="teaching",
                    starts_on="2026-09-01",
                    ends_on="2026-12-28",
                    semester=1,
                ),
            ),
            "bell_slots": (
                BellSlot(
                    slot_code="S1-01",
                    academic_year="2026/2027",
                    shift_code="S1",
                    lesson_number=1,
                    starts_at="08:30",
                    ends_at="10:00",
                ),
            ),
            "calendar_exceptions": (
                CalendarException(
                    exception_code="EX-001",
                    academic_year="2026/2027",
                    exception_type="holiday",
                    exception_date="2026-11-04",
                ),
            ),
            "resource_unavailability": (
                ResourceUnavailability(
                    unavailability_code="U-001",
                    academic_year="2026/2027",
                    resource_type="teacher",
                    resource_code="T-001",
                    starts_on="2026-10-01",
                    ends_on="2026-10-01",
                ),
            ),
        }
    )


class CurriculumReadinessTests(unittest.TestCase):
    def test_matching_workload_is_ready(self) -> None:
        report = analyze_curriculum_alignment(
            make_import_batch(), make_reference_batch()
        )

        self.assertTrue(report.is_ready)
        self.assertEqual(report.issues, ())

    def test_unknown_discipline_blocks_calculation(self) -> None:
        report = analyze_curriculum_alignment(
            make_import_batch(discipline_code="ОП.404"), make_reference_batch()
        )

        self.assertFalse(report.is_ready)
        self.assertIn("discipline_not_in_curriculum", {issue.code for issue in report.issues})

    def test_overallocated_hours_block_calculation(self) -> None:
        report = analyze_curriculum_alignment(
            make_import_batch(hours=74), make_reference_batch(planned_hours=72)
        )

        self.assertFalse(report.is_ready)
        self.assertIn("curriculum_hours_exceeded", {issue.code for issue in report.issues})

    def test_underallocated_hours_are_reported_as_warning(self) -> None:
        report = analyze_curriculum_alignment(
            make_import_batch(hours=70), make_reference_batch(planned_hours=72)
        )

        self.assertTrue(report.is_ready)
        warning = next(issue for issue in report.issues if issue.code == "curriculum_hours_remaining")
        self.assertEqual(warning.severity, "warning")
        self.assertEqual(warning.difference_hours, 2)

    def test_completely_unassigned_curriculum_hours_are_reported(self) -> None:
        batch = make_import_batch().model_copy(update={"workloads": ()})

        report = analyze_curriculum_alignment(batch, make_reference_batch())

        warning = next(
            issue for issue in report.issues if issue.code == "curriculum_hours_remaining"
        )
        self.assertEqual(warning.group_code, "ИС-101")
        self.assertEqual(warning.difference_hours, 72)

    def test_groups_using_same_curriculum_are_compared_independently(self) -> None:
        batch = make_import_batch()
        second_group = batch.groups[0].model_copy(update={"group_code": "ИС-102"})
        second_workload = batch.workloads[0].model_copy(
            update={"workload_row_code": "W-002", "group_code": "ИС-102"}
        )
        batch = batch.model_copy(
            update={
                "groups": (*batch.groups, second_group),
                "workloads": (*batch.workloads, second_workload),
            }
        )

        report = analyze_curriculum_alignment(batch, make_reference_batch())

        self.assertTrue(report.is_ready)
        self.assertEqual(report.issues, ())

    def test_group_without_curriculum_blocks_calculation(self) -> None:
        batch = make_import_batch()
        batch = batch.model_copy(
            update={
                "groups": (
                    batch.groups[0].model_copy(update={"curriculum_code": None}),
                )
            }
        )

        report = analyze_curriculum_alignment(batch, make_reference_batch())

        self.assertFalse(report.is_ready)
        self.assertIn("group_without_curriculum", {issue.code for issue in report.issues})

    def test_room_supply_matches_type_capacity_and_equipment(self) -> None:
        batch = make_import_batch()
        workload = batch.workloads[0].model_copy(
            update={
                "room_type": "COMPUTER_LAB",
                "room_capacity": 25,
                "required_equipment_codes": ("COMPUTERS",),
            }
        )
        batch = batch.model_copy(
            update={
                "workloads": (workload,),
                "buildings": (
                    Building(building_code="MAIN", building_name="Главный корпус"),
                ),
                "room_types": (
                    RoomType(
                        room_type_code="COMPUTER_LAB",
                        room_type_name="Компьютерный класс",
                    ),
                ),
                "rooms": (
                    Room(
                        room_code="MAIN-201",
                        room_name="Лаборатория 201",
                        building_code="MAIN",
                        room_type_code="COMPUTER_LAB",
                        capacity=25,
                        equipment_codes=("COMPUTERS",),
                    ),
                ),
            }
        )

        self.assertEqual(analyze_room_supply(batch), ())

        undersized = batch.rooms[0].model_copy(update={"capacity": 24})
        deficits = analyze_room_supply(
            batch.model_copy(update={"rooms": (undersized,)})
        )

        self.assertEqual(deficits[0].workload_row_code, "W-001")
        self.assertEqual(deficits[0].required_capacity, 25)


class ScheduleReadinessTests(unittest.TestCase):
    def test_inactive_cycle_blocks_cyclic_workload(self) -> None:
        batch = make_ready_batch()
        workload = batch.workloads[0].model_copy(
            update={
                "cycle_code": "NUMERATOR-DENOMINATOR",
                "cycle_week_numbers": (1,),
            }
        )
        cycle = AcademicCycle(
            cycle_code="NUMERATOR-DENOMINATOR",
            academic_year="2026/2027",
            cycle_name="Числитель / знаменатель",
            cycle_length_weeks=2,
            anchor_date="2026-09-01",
            active=False,
        )

        report = analyze_schedule_readiness(
            batch.model_copy(
                update={"workloads": (workload,), "academic_cycles": (cycle,)}
            )
        )

        self.assertIn("inactive_workload_cycle", {issue.code for issue in report.issues})

    def test_complete_batch_is_ready(self) -> None:
        report = analyze_schedule_readiness(make_ready_batch())

        self.assertTrue(report.is_ready)
        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)
        self.assertEqual(report.issues, ())

    def test_missing_required_section_blocks_calculation(self) -> None:
        batch = make_ready_batch().model_copy(update={"bell_slots": ()})

        report = analyze_schedule_readiness(batch)

        issue = next(issue for issue in report.issues if issue.code == "missing_bell_slots")
        self.assertFalse(report.is_ready)
        self.assertEqual(issue.section, "bell_slots")
        self.assertIsNotNone(issue.remediation)

    def test_room_deficit_blocks_calculation(self) -> None:
        small_room = make_ready_batch().rooms[0].model_copy(update={"capacity": 20})
        batch = make_ready_batch().model_copy(update={"rooms": (small_room,)})

        report = analyze_schedule_readiness(batch)

        issue = next(issue for issue in report.issues if issue.code == "no_suitable_room")
        self.assertFalse(report.is_ready)
        self.assertEqual(issue.object_code, "W-001")
        self.assertEqual(issue.group_code, "ИС-101")

    def test_workload_requires_active_teacher_and_matching_calendar(self) -> None:
        batch = make_ready_batch()
        batch = batch.model_copy(
            update={
                "teachers": (
                    batch.teachers[0].model_copy(update={"active": False}),
                ),
                "academic_years": (
                    batch.academic_years[0].model_copy(update={"active": False}),
                ),
                "calendar_periods": (
                    batch.calendar_periods[0].model_copy(update={"semester": 2}),
                ),
                "bell_slots": (
                    batch.bell_slots[0].model_copy(
                        update={"academic_year": "2025/2026"}
                    ),
                ),
            }
        )

        report = analyze_schedule_readiness(batch)
        codes = {issue.code for issue in report.issues}

        self.assertIn("inactive_workload_teacher", codes)
        self.assertIn("inactive_workload_academic_year", codes)
        self.assertIn("missing_workload_period", codes)
        self.assertIn("missing_workload_bell_slots", codes)

    def test_overlapping_global_and_workload_replacement_blocks_activation(self) -> None:
        batch = make_ready_batch()
        substitute = Teacher(
            teacher_code="T-002",
            full_name="Петров Пётр Петрович",
            yearly_assigned_hours=0,
        )
        global_replacement = TeacherReplacement(
            replacement_code="REP-ALL",
            academic_year="2026/2027",
            original_teacher_code="T-001",
            substitute_teacher_code="T-002",
            starts_on="2026-09-01",
            ends_on="2026-09-10",
        )
        scoped_replacement = TeacherReplacement(
            replacement_code="REP-W-001",
            academic_year="2026/2027",
            original_teacher_code="T-001",
            substitute_teacher_code="T-002",
            starts_on="2026-09-05",
            ends_on="2026-09-15",
            workload_row_code="W-001",
        )

        report = analyze_schedule_readiness(batch.model_copy(update={
            "teachers": (*batch.teachers, substitute),
            "teacher_replacements": (global_replacement, scoped_replacement),
        }))

        self.assertIn("overlapping_teacher_replacements", {issue.code for issue in report.issues})

    def test_warnings_do_not_block_and_order_is_deterministic(self) -> None:
        batch = make_ready_batch(hours=70).model_copy(
            update={"calendar_exceptions": (), "resource_unavailability": ()}
        )

        first = analyze_schedule_readiness(batch)
        second = analyze_schedule_readiness(batch)

        self.assertTrue(first.is_ready)
        self.assertEqual(first, second)
        self.assertEqual(first.error_count, 0)
        self.assertEqual(first.warning_count, 3)
        self.assertEqual(
            [issue.code for issue in first.issues],
            [
                "calendar_exceptions_not_configured",
                "curriculum_hours_remaining",
                "resource_unavailability_not_configured",
            ],
        )


if __name__ == "__main__":
    unittest.main()
