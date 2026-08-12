from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook, load_workbook
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
    def test_rejects_partial_reference_sheet_set(self) -> None:
        workbook = load_workbook(FIXTURES / "valid-import.xlsx")
        del workbook["Дисциплины"]

        with NamedTemporaryFile(suffix=".xlsx") as target:
            workbook.save(target.name)
            workbook.close()
            with self.assertRaises(ImportValidationError) as raised:
                read_import_workbook(target.name)

        self.assertEqual(raised.exception.issues[0].code, "missing_sheet")
        self.assertIn("Дисциплины", raised.exception.issues[0].message)

    def test_reads_reference_sheets_into_the_same_batch(self) -> None:
        workbook = Workbook()
        workbook.remove(workbook.active)
        sheets = {
            "Преподаватели": (
                ["teacher_code", "full_name", "yearly_assigned_hours"],
                [["T-001", "Иванова Ирина Игоревна", 72]],
            ),
            "Группы": (
                [
                    "group_code",
                    "specialty_code",
                    "curriculum_code",
                    "course",
                    "headcount",
                ],
                [["ИС-101", "09.02.07", "UP-09.02.07-2026", 1, 25]],
            ),
            "Нагрузка": (
                [
                    "workload_row_code",
                    "academic_year",
                    "semester",
                    "discipline_code",
                    "discipline_name",
                    "group_code",
                    "teacher_code",
                    "lesson_type",
                    "total_academic_hours",
                    "event_duration_hours",
                ],
                [[
                    "W-001", "2026/2027", 1, "MDK.01.01",
                    "Основы программирования", "ИС-101", "T-001",
                    "practice", 72, 2,
                ]],
            ),
            "Специальности": (
                [
                    "specialty_code",
                    "specialty_name",
                    "program_base",
                    "education_form",
                ],
                [["09.02.07", "Информационные системы", "9", "full_time"]],
            ),
            "Учебные планы": (
                [
                    "curriculum_code",
                    "specialty_code",
                    "admission_year",
                    "version",
                    "valid_from",
                    "status",
                ],
                [[
                    "UP-09.02.07-2026", "09.02.07", 2026, "1.0",
                    date(2026, 9, 1), "active",
                ]],
            ),
            "Дисциплины": (
                [
                    "curriculum_code",
                    "discipline_code",
                    "discipline_name",
                    "semester",
                    "lesson_type",
                    "planned_hours",
                ],
                [[
                    "UP-09.02.07-2026", "MDK.01.01",
                    "Основы программирования", 1, "practice", 72,
                ]],
            ),
        }
        for title, (headers, rows) in sheets.items():
            worksheet = workbook.create_sheet(title)
            worksheet.append(headers)
            for row in rows:
                worksheet.append(row)

        with NamedTemporaryFile(suffix=".xlsx") as target:
            workbook.save(target.name)
            batch = read_import_workbook(target.name)

        self.assertEqual(batch.specialties[0].specialty_code, "09.02.07")
        self.assertEqual(batch.curricula[0].curriculum_code, "UP-09.02.07-2026")
        self.assertEqual(batch.disciplines[0].planned_hours, 72)

    def test_reads_valid_canonical_workbook(self) -> None:
        batch = read_import_workbook(FIXTURES / "valid-import.xlsx")

        self.assertEqual(batch.teachers[0].teacher_code, "T-001")
        self.assertEqual(batch.groups[0].group_code, "ИС-101")
        self.assertEqual(batch.workloads[0].workload_row_code, "W-001")
        self.assertEqual(batch.specialties[0].specialty_code, "09.02.07")

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
