from __future__ import annotations

import unittest

from pydantic import ValidationError

from rasp.domain.models import AcademicYear, BellSlot, CalendarPeriod
from rasp.imports.calendar import CalendarValidationError, build_calendar_batch


class CalendarDomainTests(unittest.TestCase):
    def test_academic_year_requires_ordered_dates_matching_its_code(self) -> None:
        year = AcademicYear(
            academic_year="2026/2027",
            starts_on="2026-09-01",
            ends_on="2027-06-30",
        )

        self.assertEqual(year.academic_year, "2026/2027")
        with self.assertRaises(ValidationError):
            AcademicYear(
                academic_year="2026/2027",
                starts_on="2025-09-01",
                ends_on="2027-06-30",
            )

    def test_calendar_period_and_bell_slot_require_ordered_ranges(self) -> None:
        with self.assertRaises(ValidationError):
            CalendarPeriod(
                period_code="SEM-1",
                academic_year="2026/2027",
                period_name="Первый семестр",
                period_type="teaching",
                starts_on="2027-01-01",
                ends_on="2026-09-01",
                semester=1,
            )

        with self.assertRaises(ValidationError):
            BellSlot(
                slot_code="S1-01",
                academic_year="2026/2027",
                shift_code="S1",
                lesson_number=1,
                starts_at="09:30",
                ends_at="09:00",
            )


class CalendarBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.years = [
            {
                "academic_year": "2026/2027",
                "starts_on": "2026-09-01",
                "ends_on": "2027-06-30",
                "active": True,
            }
        ]
        self.periods = [
            {
                "period_code": "SEM-1",
                "academic_year": "2026/2027",
                "period_name": "Первый семестр",
                "period_type": "teaching",
                "starts_on": "2026-09-01",
                "ends_on": "2026-12-28",
                "semester": 1,
            }
        ]
        self.slots = [
            {
                "slot_code": "S1-01",
                "academic_year": "2026/2027",
                "shift_code": "S1",
                "lesson_number": 1,
                "starts_at": "08:30",
                "ends_at": "10:00",
            },
            {
                "slot_code": "S1-02",
                "academic_year": "2026/2027",
                "shift_code": "S1",
                "lesson_number": 2,
                "starts_at": "10:10",
                "ends_at": "11:40",
            },
        ]

    def test_builds_consistent_calendar(self) -> None:
        batch = build_calendar_batch(
            academic_year_rows=self.years,
            period_rows=self.periods,
            bell_slot_rows=self.slots,
        )

        self.assertEqual(len(batch.academic_years), 1)
        self.assertEqual(len(batch.periods), 1)
        self.assertEqual(len(batch.bell_slots), 2)

    def test_rejects_period_outside_year_and_overlapping_slots(self) -> None:
        self.periods[0]["starts_on"] = "2026-08-31"
        self.slots[1]["starts_at"] = "09:50"

        with self.assertRaises(CalendarValidationError) as raised:
            build_calendar_batch(
                academic_year_rows=self.years,
                period_rows=self.periods,
                bell_slot_rows=self.slots,
            )

        codes = {issue.code for issue in raised.exception.issues}
        self.assertIn("period_outside_academic_year", codes)
        self.assertIn("overlapping_bell_slots", codes)

    def test_rejects_duplicate_active_academic_years(self) -> None:
        duplicate = dict(self.years[0])

        with self.assertRaises(CalendarValidationError) as raised:
            build_calendar_batch(
                academic_year_rows=[*self.years, duplicate],
                period_rows=self.periods,
                bell_slot_rows=self.slots,
            )

        self.assertIn("duplicate_code", {issue.code for issue in raised.exception.issues})


if __name__ == "__main__":
    unittest.main()
