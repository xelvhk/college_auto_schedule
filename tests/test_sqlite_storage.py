from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from rasp.domain.models import (
    AcademicYear,
    BellSlot,
    Building,
    CalendarPeriod,
    Curriculum,
    CurriculumDiscipline,
    Equipment,
    Group,
    ImportBatch,
    Room,
    RoomType,
    Specialty,
    Student,
    Teacher,
    WorkloadItem,
)
from rasp.storage.sqlite import (
    SqliteImportRepository,
    StorageError,
    VersionNotFoundError,
)


def make_batch(*, teacher_code: str = "T-001") -> ImportBatch:
    return ImportBatch(
        teachers=(
            Teacher(
                teacher_code="T-001",
                full_name="Иванова Ирина Игоревна",
                yearly_assigned_hours=720,
            ),
        ),
        groups=(Group(group_code="ИС-101", course=1, headcount=25),),
        workloads=(
            WorkloadItem(
                workload_row_code="W-001",
                academic_year="2026/2027",
                semester=1,
                discipline_code="MDK.01.01",
                discipline_name="Основы программирования",
                group_code="ИС-101",
                teacher_code=teacher_code,
                lesson_type="practice",
                total_academic_hours=72,
                event_duration_hours=2,
            ),
        ),
    )


def make_full_batch() -> ImportBatch:
    base = make_batch()
    return base.model_copy(
        update={
            "groups": (
                base.groups[0].model_copy(
                    update={
                        "specialty_code": "09.02.07",
                        "curriculum_code": "UP-09.02.07-2026",
                    }
                ),
            ),
            "specialties": (
                Specialty(
                    specialty_code="09.02.07",
                    specialty_name="Информационные системы",
                    program_base="9",
                    education_form="full_time",
                ),
            ),
            "curricula": (
                Curriculum(
                    curriculum_code="UP-09.02.07-2026",
                    specialty_code="09.02.07",
                    admission_year=2026,
                    version="1.0",
                    valid_from="2026-09-01",
                    status="active",
                ),
            ),
            "disciplines": (
                CurriculumDiscipline(
                    curriculum_code="UP-09.02.07-2026",
                    discipline_code="MDK.01.01",
                    discipline_name="Основы программирования",
                    semester=1,
                    lesson_type="practice",
                    planned_hours=72,
                ),
            ),
            "students": (
                Student(
                    student_code="S-001",
                    full_name="Петров Пётр Петрович",
                    group_code="ИС-101",
                    status="active",
                    enrollment_date="2026-09-01",
                    subgroup_codes=(1,),
                    elective_codes=("WEB",),
                ),
            ),
            "buildings": (
                Building(building_code="MAIN", building_name="Главный корпус"),
            ),
            "room_types": (
                RoomType(
                    room_type_code="COMPUTER_LAB",
                    room_type_name="Компьютерный класс",
                ),
            ),
            "equipment": (
                Equipment(
                    equipment_code="COMPUTERS",
                    equipment_name="Компьютеры",
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
        }
    )


class SqliteImportRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "rasp.sqlite3"
        self.repository = SqliteImportRepository(self.database_path)
        self.repository.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_activates_and_restores_complete_import_batch(self) -> None:
        receipt = self.repository.activate_import(
            make_batch(),
            source_name="valid-import.xlsx",
            source_sha256="a" * 64,
        )

        restored = self.repository.get_active_batch()

        self.assertEqual(receipt.version_id, 1)
        self.assertFalse(receipt.reused)
        self.assertEqual(restored, make_batch())

    def test_atomically_activates_and_restores_all_sections(self) -> None:
        receipt = self.repository.activate_import(
            make_full_batch(),
            source_name="full-import.xlsx",
            source_sha256="8" * 64,
        )

        restored = self.repository.get_active_batch()

        self.assertEqual(receipt.specialty_count, 1)
        self.assertEqual(receipt.curriculum_count, 1)
        self.assertEqual(receipt.discipline_count, 1)
        self.assertEqual(receipt.student_count, 1)
        self.assertEqual(receipt.building_count, 1)
        self.assertEqual(receipt.room_type_count, 1)
        self.assertEqual(receipt.equipment_count, 1)
        self.assertEqual(receipt.room_count, 1)
        self.assertEqual(len(restored.academic_years), 1)
        self.assertEqual(len(restored.calendar_periods), 1)
        self.assertEqual(len(restored.bell_slots), 1)
        self.assertEqual(restored, make_full_batch())

    def test_preserves_group_curriculum_code(self) -> None:
        batch = make_batch().model_copy(
            update={
                "groups": (
                    make_batch().groups[0].model_copy(
                        update={"curriculum_code": "UP-09.02.07-2026"}
                    ),
                )
            }
        )

        self.repository.activate_import(
            batch,
            source_name="curriculum.xlsx",
            source_sha256="9" * 64,
        )

        restored = self.repository.get_active_batch()

        self.assertEqual(restored.groups[0].curriculum_code, "UP-09.02.07-2026")

    def test_same_fingerprint_is_idempotent(self) -> None:
        first = self.repository.activate_import(
            make_batch(), source_name="first.xlsx", source_sha256="b" * 64
        )
        second = self.repository.activate_import(
            ImportBatch(teachers=(), groups=(), workloads=()),
            source_name="renamed.xlsx",
            source_sha256="b" * 64,
        )

        versions = self.repository.list_versions()

        self.assertEqual(second.version_id, first.version_id)
        self.assertTrue(second.reused)
        self.assertEqual(second.teacher_count, 1)
        self.assertEqual(len(versions), 1)

    def test_failed_insert_rolls_back_and_preserves_previous_active_version(self) -> None:
        first = self.repository.activate_import(
            make_batch(), source_name="valid.xlsx", source_sha256="c" * 64
        )

        with self.assertRaises(StorageError):
            self.repository.activate_import(
                make_batch(teacher_code="T-404"),
                source_name="invalid.xlsx",
                source_sha256="d" * 64,
            )

        versions = self.repository.list_versions()
        restored = self.repository.get_active_batch()

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0].version_id, first.version_id)
        self.assertEqual(restored.workloads[0].teacher_code, "T-001")

    def test_reopen_keeps_active_version_and_enables_foreign_keys(self) -> None:
        self.repository.activate_import(
            make_batch(), source_name="valid.xlsx", source_sha256="e" * 64
        )

        reopened = SqliteImportRepository(self.database_path)
        reopened.initialize()

        self.assertEqual(reopened.get_active_batch(), make_batch())
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(foreign_keys, [])

    def test_audit_metadata_stores_only_source_basename(self) -> None:
        self.repository.activate_import(
            make_batch(),
            source_name="/private/college/valid.xlsx",
            source_sha256="f" * 64,
        )

        version = self.repository.list_versions()[0]

        self.assertEqual(version.source_name, "valid.xlsx")
        self.assertTrue(version.is_active)

    def test_can_reactivate_previous_version_without_copying_rows(self) -> None:
        first = self.repository.activate_import(
            make_batch(), source_name="first.xlsx", source_sha256="1" * 64
        )
        changed = make_batch().model_copy(
            update={
                "teachers": (
                    Teacher(
                        teacher_code="T-001",
                        full_name="Иванова Ирина Игоревна",
                        yearly_assigned_hours=700,
                    ),
                )
            }
        )
        self.repository.activate_import(
            changed, source_name="second.xlsx", source_sha256="2" * 64
        )

        activated = self.repository.activate_version(first.version_id)

        self.assertEqual(activated.version_id, first.version_id)
        self.assertTrue(activated.is_active)
        self.assertEqual(
            self.repository.get_active_batch().teachers[0].yearly_assigned_hours,
            720,
        )

    def test_rejects_unknown_version_without_changing_active_data(self) -> None:
        first = self.repository.activate_import(
            make_batch(), source_name="first.xlsx", source_sha256="3" * 64
        )

        with self.assertRaises(VersionNotFoundError):
            self.repository.activate_version(404)

        active = next(
            version for version in self.repository.list_versions() if version.is_active
        )
        self.assertEqual(active.version_id, first.version_id)

    def test_rejects_database_from_newer_application_version(self) -> None:
        future_database = Path(self.temporary_directory.name) / "future.sqlite3"
        with closing(sqlite3.connect(future_database)) as connection, connection:
            connection.execute("PRAGMA user_version = 99")

        repository = SqliteImportRepository(future_database)

        with self.assertRaises(StorageError):
            repository.initialize()
        with closing(sqlite3.connect(future_database)) as connection, connection:
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(schema_version, 99)

    def test_migrates_version_one_database_without_losing_groups(self) -> None:
        legacy_database = Path(self.temporary_directory.name) / "legacy.sqlite3"
        with closing(sqlite3.connect(legacy_database)) as connection, connection:
            connection.executescript(
                """
                PRAGMA user_version = 1;
                CREATE TABLE student_groups (
                    import_version_id INTEGER NOT NULL,
                    group_code TEXT NOT NULL,
                    specialty_code TEXT,
                    course INTEGER NOT NULL,
                    education_form TEXT NOT NULL,
                    headcount INTEGER NOT NULL,
                    program_base TEXT,
                    study_week_type TEXT,
                    primary_building_code TEXT,
                    subgroup_count INTEGER NOT NULL,
                    PRIMARY KEY (import_version_id, group_code)
                );
                INSERT INTO student_groups VALUES (
                    1, 'ИС-101', '09.02.07', 1, 'full_time', 25,
                    '9', NULL, NULL, 1
                );
                """
            )

        repository = SqliteImportRepository(legacy_database)
        repository.initialize()

        with closing(sqlite3.connect(legacy_database)) as connection:
            row = connection.execute(
                "SELECT group_code, curriculum_code FROM student_groups"
            ).fetchone()
            version = connection.execute("PRAGMA user_version").fetchone()[0]

        self.assertEqual(row, ("ИС-101", None))
        self.assertEqual(version, 6)

    def test_corrupted_stored_record_returns_safe_storage_error(self) -> None:
        self.repository.activate_import(
            make_batch(), source_name="valid.xlsx", source_sha256="4" * 64
        )
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute("UPDATE teachers SET max_hours_per_day = 0")

        with self.assertRaisesRegex(StorageError, "invalid stored data"):
            self.repository.get_active_batch()


if __name__ == "__main__":
    unittest.main()
