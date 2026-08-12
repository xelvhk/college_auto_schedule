from __future__ import annotations

import unittest

from pydantic import ValidationError

from rasp.domain.models import CurriculumDiscipline, Specialty
from rasp.imports.reference_data import (
    ReferenceDataValidationError,
    build_reference_data_batch,
)


class ReferenceDomainModelTests(unittest.TestCase):
    def test_specialty_normalizes_code_and_requires_supported_program_base(self) -> None:
        specialty = Specialty(
            specialty_code="  09.02.07 ",
            specialty_name="Информационные системы и программирование",
            program_base="9",
            education_form="full_time",
        )

        self.assertEqual(specialty.specialty_code, "09.02.07")
        with self.assertRaises(ValidationError):
            Specialty(
                specialty_code="09.02.07",
                specialty_name="Информационные системы и программирование",
                program_base="10",
                education_form="full_time",
            )

    def test_curriculum_discipline_rejects_negative_hours(self) -> None:
        with self.assertRaises(ValidationError):
            CurriculumDiscipline(
                curriculum_code="UP-09.02.07-2026",
                discipline_code="МДК.01.01",
                discipline_name="Разработка программных модулей",
                semester=1,
                lesson_type="practice",
                planned_hours=-1,
            )


class ReferenceDataBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.specialty_rows = [
            {
                "specialty_code": "09.02.07",
                "specialty_name": "Информационные системы и программирование",
                "program_base": "9",
                "education_form": "full_time",
            }
        ]
        self.curriculum_rows = [
            {
                "curriculum_code": "UP-09.02.07-2026",
                "specialty_code": "09.02.07",
                "admission_year": 2026,
                "version": "1",
                "valid_from": "2026-09-01",
                "status": "active",
            }
        ]
        self.discipline_rows = [
            {
                "curriculum_code": "UP-09.02.07-2026",
                "discipline_code": "МДК.01.01",
                "discipline_name": "Разработка программных модулей",
                "semester": 1,
                "lesson_type": "practice",
                "planned_hours": 72,
            }
        ]

    def test_builds_reference_batch_when_rows_are_consistent(self) -> None:
        batch = build_reference_data_batch(
            specialty_rows=self.specialty_rows,
            curriculum_rows=self.curriculum_rows,
            discipline_rows=self.discipline_rows,
        )

        self.assertEqual(batch.curricula[0].specialty_code, "09.02.07")
        self.assertEqual(batch.disciplines[0].planned_hours, 72)

    def test_rejects_curriculum_for_unknown_specialty(self) -> None:
        self.curriculum_rows[0]["specialty_code"] = "00.00.00"

        with self.assertRaises(ReferenceDataValidationError) as raised:
            build_reference_data_batch(
                specialty_rows=self.specialty_rows,
                curriculum_rows=self.curriculum_rows,
                discipline_rows=self.discipline_rows,
            )

        self.assertIn("unknown_specialty", {issue.code for issue in raised.exception.issues})

    def test_rejects_duplicate_discipline_key_after_code_normalization(self) -> None:
        duplicate = dict(self.discipline_rows[0])
        duplicate["discipline_code"] = " мдк.01.01 "

        with self.assertRaises(ReferenceDataValidationError) as raised:
            build_reference_data_batch(
                specialty_rows=self.specialty_rows,
                curriculum_rows=self.curriculum_rows,
                discipline_rows=[*self.discipline_rows, duplicate],
            )

        self.assertIn("duplicate_discipline", {issue.code for issue in raised.exception.issues})

    def test_rejects_overlapping_active_curriculum_revisions(self) -> None:
        overlapping = {
            **self.curriculum_rows[0],
            "curriculum_code": "UP-09.02.07-2026-V2",
            "version": "2",
            "valid_from": "2027-01-01",
        }

        with self.assertRaises(ReferenceDataValidationError) as raised:
            build_reference_data_batch(
                specialty_rows=self.specialty_rows,
                curriculum_rows=[*self.curriculum_rows, overlapping],
                discipline_rows=self.discipline_rows,
            )

        self.assertIn("overlapping_curricula", {issue.code for issue in raised.exception.issues})


if __name__ == "__main__":
    unittest.main()
