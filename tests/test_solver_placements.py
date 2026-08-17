from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from rasp.domain.models import (
    BellSlot,
    CalendarException,
    ResourceUnavailability,
    Room,
)
from rasp.imports.excel import read_import_workbook
from rasp.solver import build_solver_problem


FIXTURES = Path(__file__).parent / "fixtures"


class SolverPlacementDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batch = read_import_workbook(FIXTURES / "valid-import.xlsx")

    def options(self, batch=None):
        problem = build_solver_problem(batch or self.batch)
        return problem, problem.placement_domains[0].options

    def test_canonical_domain_respects_cycle_week_and_full_day_unavailability(self) -> None:
        unsuitable_room = Room(
            room_code="MAIN-101",
            room_name="Малая аудитория",
            building_code="MAIN",
            room_type_code="COMPUTER_LAB",
            capacity=20,
            equipment_codes=(),
        )
        batch = self.batch.model_copy(
            update={"rooms": (*self.batch.rooms, unsuitable_room)}
        )

        problem, options = self.options(batch)

        self.assertTrue(problem.is_ready)
        self.assertEqual(len(problem.placement_domains), 1)
        self.assertEqual(len(options), 50)
        self.assertEqual(options[0].lesson_date, date(2026, 9, 1))
        self.assertEqual(options[0].slot_codes, ("S1-01",))
        self.assertEqual(options[0].room_code, "MAIN-201")
        self.assertEqual({item.room_code for item in options}, {"MAIN-201"})
        self.assertNotIn(date(2026, 10, 1), {item.lesson_date for item in options})

    def test_five_day_week_excludes_saturday_but_explicit_working_day_adds_sunday(self) -> None:
        group = self.batch.groups[0].model_copy(update={"study_week_type": "five_days"})
        working_sunday = CalendarException(
            exception_code="EX-WORKING-SUNDAY",
            academic_year="2026/2027",
            exception_type="working_day",
            exception_date="2026-09-06",
        )
        batch = self.batch.model_copy(
            update={
                "groups": (group,),
                "calendar_exceptions": (
                    *self.batch.calendar_exceptions,
                    working_sunday,
                ),
            }
        )

        _, options = self.options(batch)
        dates = {item.lesson_date for item in options}

        self.assertNotIn(date(2026, 9, 5), dates)
        self.assertIn(date(2026, 9, 6), dates)

    def test_transfer_uses_source_day_semantics_and_target_resource_date(self) -> None:
        transfer = CalendarException(
            exception_code="EX-TRANSFER",
            academic_year="2026/2027",
            exception_type="transferred_day",
            exception_date="2026-09-02",
            transferred_to="2026-09-06",
        )
        target_unavailability = ResourceUnavailability(
            unavailability_code="U-TARGET",
            academic_year="2026/2027",
            resource_type="teacher",
            resource_code="T-001",
            starts_on="2026-09-06",
            ends_on="2026-09-06",
            starts_at="10:00",
            ends_at="11:00",
        )
        batch = self.batch.model_copy(
            update={
                "calendar_exceptions": (
                    *self.batch.calendar_exceptions,
                    transfer,
                ),
                "resource_unavailability": (
                    *self.batch.resource_unavailability,
                    target_unavailability,
                ),
            }
        )

        _, options = self.options(batch)
        dates = {item.lesson_date for item in options}

        self.assertNotIn(date(2026, 9, 2), dates)
        self.assertIn(date(2026, 9, 6), dates)
        transferred = next(
            item for item in options if item.lesson_date == date(2026, 9, 6)
        )
        self.assertEqual(transferred.teaching_week_start, date(2026, 8, 31))

    def test_holiday_shortening_and_each_resource_unavailability_remove_options(self) -> None:
        exceptions = (
            *self.batch.calendar_exceptions,
            CalendarException(
                exception_code="EX-HOLIDAY-2",
                academic_year="2026/2027",
                exception_type="holiday",
                exception_date="2026-09-02",
            ),
            CalendarException(
                exception_code="EX-SHORT",
                academic_year="2026/2027",
                exception_type="shortened_day",
                exception_date="2026-09-14",
                shortened_ends_at="09:30",
            ),
        )
        unavailable = list(self.batch.resource_unavailability)
        for number, resource_type, resource_code, day in (
            (2, "teacher", "T-001", "2026-09-15"),
            (3, "group", "ИС-101", "2026-09-16"),
            (4, "room", "MAIN-201", "2026-09-17"),
        ):
            unavailable.append(
                ResourceUnavailability(
                    unavailability_code=f"U-{number}",
                    academic_year="2026/2027",
                    resource_type=resource_type,
                    resource_code=resource_code,
                    starts_on=day,
                    ends_on=day,
                    starts_at="08:45",
                    ends_at="09:00",
                )
            )
        batch = self.batch.model_copy(
            update={
                "calendar_exceptions": exceptions,
                "resource_unavailability": tuple(unavailable),
            }
        )

        _, options = self.options(batch)
        dates = {item.lesson_date for item in options}

        for blocked in (
            date(2026, 9, 2),
            date(2026, 9, 14),
            date(2026, 9, 15),
            date(2026, 9, 16),
            date(2026, 9, 17),
        ):
            self.assertNotIn(blocked, dates)

    def test_four_academic_hours_require_two_consecutive_slots(self) -> None:
        workload = self.batch.workloads[0].model_copy(
            update={"total_academic_hours": 4, "event_duration_hours": 4}
        )
        second_slot = BellSlot(
            slot_code="S1-02",
            academic_year="2026/2027",
            shift_code="S1",
            lesson_number=2,
            starts_at="10:10",
            ends_at="11:40",
        )
        batch = self.batch.model_copy(
            update={
                "workloads": (workload,),
                "bell_slots": (*self.batch.bell_slots, second_slot),
            }
        )

        problem, options = self.options(batch)

        self.assertEqual(len(problem.demands), 1)
        self.assertTrue(options)
        self.assertEqual({item.slot_codes for item in options}, {("S1-01", "S1-02")})

    def test_unknown_week_type_and_unsupported_slot_duration_are_blocking(self) -> None:
        group = self.batch.groups[0].model_copy(update={"study_week_type": "custom"})
        invalid_slot = BellSlot(
            **(self.batch.bell_slots[0].model_dump() | {"ends_at": "09:31"})
        )
        batch = self.batch.model_copy(
            update={"groups": (group,), "bell_slots": (invalid_slot,)}
        )

        problem = build_solver_problem(batch)
        codes = {item.code for item in problem.diagnostics}

        self.assertFalse(problem.is_ready)
        self.assertEqual(problem.placement_domains[0].options, ())
        self.assertIn("unsupported_study_week_type", codes)
        self.assertIn("unsupported_bell_slot_duration", codes)
        self.assertIn("no_eligible_placements", codes)

    def test_placement_limit_is_checked_without_returning_partial_domains(self) -> None:
        with patch("rasp.solver.placements.MAX_PLACEMENT_OPTIONS", 2):
            problem = build_solver_problem(self.batch)

        self.assertFalse(problem.is_ready)
        self.assertEqual(problem.placement_domains, ())
        self.assertIn(
            "placement_option_limit_exceeded",
            {item.code for item in problem.diagnostics},
        )


if __name__ == "__main__":
    unittest.main()
