from __future__ import annotations

import unittest

from rasp.application.readiness import analyze_curriculum_alignment, analyze_room_supply
from rasp.domain.models import (
    Building,
    Curriculum,
    CurriculumDiscipline,
    Group,
    ImportBatch,
    ReferenceDataBatch,
    Room,
    RoomType,
    Specialty,
    Teacher,
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


if __name__ == "__main__":
    unittest.main()
