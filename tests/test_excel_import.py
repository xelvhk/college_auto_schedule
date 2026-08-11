from __future__ import annotations

import unittest
from pathlib import Path

from pydantic import ValidationError

from rasp.domain.models import Group, Teacher, WorkloadItem
from rasp.imports.excel import (
    ImportValidationError,
    build_import_batch,
    read_import_workbook,
)


FIXTURES = Path(__file__).parent / "fixtures"


class DomainModelTests(unittest.TestCase):
    def test_teacher_rejects_impossible_daily_limit(self) -> None:
        with self.assertRaises(ValidationError):
            Teacher(
                teacher_code="T-001",
                full_name="Иванова Ирина Игоревна",
                yearly_assigned_hours=720,
                max_hours_per_day=0,
            )

    def test_group_normalizes_code_and_requires_positive_headcount(self) -> None:
        group = Group(group_code="  ис-101  ", course=1, headcount=25)
        self.assertEqual(group.group_code, "ИС-101")

        with self.assertRaises(ValidationError):
            Group(group_code="ИС-102", course=1, headcount=0)


class ImportBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.teacher_rows = [
            {
                "teacher_code": "T-001",
                "full_name": "Иванова Ирина Игоревна",
                "yearly_assigned_hours": 720,
                "max_hours_per_day": 8,
                "active": True,
            }
        ]
        self.group_rows = [
            {
                "group_code": "ИС-101",
                "course": 1,
                "headcount": 25,
                "subgroup_count": 1,
            }
        ]
        self.workload_rows = [
            {
                "workload_row_code": "W-001",
                "academic_year": "2026/2027",
                "semester": 1,
                "discipline_code": "MDK.01.01",
                "discipline_name": "Основы программирования",
                "group_code": "ИС-101",
                "teacher_code": "T-001",
                "lesson_type": "practice",
                "total_academic_hours": 72,
                "event_duration_hours": 2,
            }
        ]

    def test_builds_atomic_batch_when_rows_are_valid(self) -> None:
        batch = build_import_batch(
            teacher_rows=self.teacher_rows,
            group_rows=self.group_rows,
            workload_rows=self.workload_rows,
        )

        self.assertEqual(len(batch.teachers), 1)
        self.assertEqual(len(batch.groups), 1)
        self.assertEqual(len(batch.workloads), 1)
        self.assertEqual(batch.workloads[0].teacher_code, "T-001")

    def test_rejects_unknown_teacher_without_returning_partial_batch(self) -> None:
        self.workload_rows[0]["teacher_code"] = "T-404"

        with self.assertRaises(ImportValidationError) as raised:
            build_import_batch(
                teacher_rows=self.teacher_rows,
                group_rows=self.group_rows,
                workload_rows=self.workload_rows,
            )

        self.assertTrue(
            any(issue.code == "unknown_teacher" for issue in raised.exception.issues)
        )

    def test_rejects_duplicate_stable_codes(self) -> None:
        duplicate = dict(self.teacher_rows[0])
        duplicate["full_name"] = "Другой преподаватель"

        with self.assertRaises(ImportValidationError) as raised:
            build_import_batch(
                teacher_rows=[*self.teacher_rows, duplicate],
                group_rows=self.group_rows,
                workload_rows=self.workload_rows,
            )

        self.assertTrue(
            any(issue.code == "duplicate_code" for issue in raised.exception.issues)
        )

    def test_workload_requires_whole_number_of_events(self) -> None:
        with self.assertRaises(ValidationError):
            WorkloadItem(**{**self.workload_rows[0], "total_academic_hours": 71})


class WorkbookImportTests(unittest.TestCase):
    def test_reads_valid_three_sheet_workbook(self) -> None:
        batch = read_import_workbook(FIXTURES / "valid-import.xlsx")

        self.assertEqual(batch.teachers[0].teacher_code, "T-001")
        self.assertEqual(batch.groups[0].group_code, "ИС-101")
        self.assertEqual(batch.workloads[0].workload_row_code, "W-001")

    def test_rejects_non_xlsx_file_before_parsing(self) -> None:
        with self.assertRaises(ImportValidationError) as raised:
            read_import_workbook(__file__)

        self.assertEqual(raised.exception.issues[0].code, "invalid_extension")

    def test_rejects_formula_cells(self) -> None:
        with self.assertRaises(ImportValidationError) as raised:
            read_import_workbook(FIXTURES / "formula-import.xlsx")

        self.assertTrue(
            any(issue.code == "formula_forbidden" for issue in raised.exception.issues)
        )


if __name__ == "__main__":
    unittest.main()
