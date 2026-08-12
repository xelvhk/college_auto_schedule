from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from rasp.domain.models import (
    Building,
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


SCHEMA_VERSION = 5
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS import_versions (
    version_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL CHECK(length(source_name) BETWEEN 1 AND 255),
    source_sha256 TEXT NOT NULL UNIQUE CHECK(length(source_sha256) = 64),
    created_at TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK(is_active IN (0, 1))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_import
ON import_versions(is_active) WHERE is_active = 1;

CREATE TABLE IF NOT EXISTS teachers (
    import_version_id INTEGER NOT NULL,
    teacher_code TEXT NOT NULL,
    full_name TEXT NOT NULL,
    department TEXT,
    employment_type TEXT,
    yearly_assigned_hours INTEGER NOT NULL CHECK(yearly_assigned_hours >= 0),
    yearly_limit_hours INTEGER,
    max_hours_per_day INTEGER,
    max_days_per_week INTEGER,
    home_building_code TEXT,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY (import_version_id, teacher_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS student_groups (
    import_version_id INTEGER NOT NULL,
    group_code TEXT NOT NULL,
    specialty_code TEXT,
    curriculum_code TEXT,
    course INTEGER NOT NULL,
    education_form TEXT NOT NULL,
    headcount INTEGER NOT NULL,
    program_base TEXT,
    study_week_type TEXT,
    primary_building_code TEXT,
    subgroup_count INTEGER NOT NULL,
    PRIMARY KEY (import_version_id, group_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS students (
    import_version_id INTEGER NOT NULL,
    student_code TEXT NOT NULL,
    full_name TEXT NOT NULL,
    group_code TEXT NOT NULL,
    status TEXT NOT NULL,
    enrollment_date TEXT,
    end_date TEXT,
    subgroup_codes TEXT NOT NULL DEFAULT '',
    elective_codes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (import_version_id, student_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE,
    FOREIGN KEY (import_version_id, group_code)
        REFERENCES student_groups(import_version_id, group_code)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS buildings (
    import_version_id INTEGER NOT NULL,
    building_code TEXT NOT NULL,
    building_name TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY (import_version_id, building_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS room_types (
    import_version_id INTEGER NOT NULL,
    room_type_code TEXT NOT NULL,
    room_type_name TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY (import_version_id, room_type_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS equipment (
    import_version_id INTEGER NOT NULL,
    equipment_code TEXT NOT NULL,
    equipment_name TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY (import_version_id, equipment_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rooms (
    import_version_id INTEGER NOT NULL,
    room_code TEXT NOT NULL,
    room_name TEXT NOT NULL,
    building_code TEXT NOT NULL,
    room_type_code TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK(capacity > 0),
    equipment_codes TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY (import_version_id, room_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE,
    FOREIGN KEY (import_version_id, building_code)
        REFERENCES buildings(import_version_id, building_code)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (import_version_id, room_type_code)
        REFERENCES room_types(import_version_id, room_type_code)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS room_equipment (
    import_version_id INTEGER NOT NULL,
    room_code TEXT NOT NULL,
    equipment_code TEXT NOT NULL,
    PRIMARY KEY (import_version_id, room_code, equipment_code),
    FOREIGN KEY (import_version_id, room_code)
        REFERENCES rooms(import_version_id, room_code)
        ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (import_version_id, equipment_code)
        REFERENCES equipment(import_version_id, equipment_code)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS specialties (
    import_version_id INTEGER NOT NULL,
    specialty_code TEXT NOT NULL,
    specialty_name TEXT NOT NULL,
    qualification TEXT,
    program_base TEXT NOT NULL,
    education_form TEXT NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0, 1)),
    PRIMARY KEY (import_version_id, specialty_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS curricula (
    import_version_id INTEGER NOT NULL,
    curriculum_code TEXT NOT NULL,
    specialty_code TEXT NOT NULL,
    admission_year INTEGER NOT NULL,
    version TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (import_version_id, curriculum_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE,
    FOREIGN KEY (import_version_id, specialty_code)
        REFERENCES specialties(import_version_id, specialty_code)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS curriculum_disciplines (
    import_version_id INTEGER NOT NULL,
    curriculum_code TEXT NOT NULL,
    discipline_code TEXT NOT NULL,
    discipline_name TEXT NOT NULL,
    section_code TEXT,
    semester INTEGER NOT NULL,
    lesson_type TEXT NOT NULL,
    planned_hours INTEGER NOT NULL,
    control_form TEXT,
    PRIMARY KEY (
        import_version_id,
        curriculum_code,
        discipline_code,
        semester,
        lesson_type
    ),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE,
    FOREIGN KEY (import_version_id, curriculum_code)
        REFERENCES curricula(import_version_id, curriculum_code)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS workload_items (
    import_version_id INTEGER NOT NULL,
    workload_row_code TEXT NOT NULL,
    academic_year TEXT NOT NULL,
    semester INTEGER NOT NULL,
    discipline_code TEXT NOT NULL,
    discipline_name TEXT NOT NULL,
    group_code TEXT NOT NULL,
    subgroup TEXT,
    stream TEXT,
    teacher_code TEXT NOT NULL,
    lesson_type TEXT NOT NULL,
    total_academic_hours INTEGER NOT NULL,
    event_duration_hours INTEGER NOT NULL,
    recurrence TEXT,
    lesson_bundle_code TEXT,
    room_type TEXT,
    room_capacity INTEGER,
    required_equipment_codes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (import_version_id, workload_row_code),
    FOREIGN KEY (import_version_id) REFERENCES import_versions(version_id)
        ON DELETE CASCADE,
    FOREIGN KEY (import_version_id, teacher_code)
        REFERENCES teachers(import_version_id, teacher_code)
        DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (import_version_id, group_code)
        REFERENCES student_groups(import_version_id, group_code)
        DEFERRABLE INITIALLY DEFERRED
);
"""


class StorageError(RuntimeError):
    """A safe, public storage error that does not expose SQL or personal data."""


class VersionNotFoundError(StorageError):
    """The requested import version does not exist."""


@dataclass(frozen=True, slots=True)
class ImportReceipt:
    version_id: int
    created_at: str
    reused: bool
    teacher_count: int
    group_count: int
    workload_count: int
    specialty_count: int
    curriculum_count: int
    discipline_count: int
    student_count: int
    building_count: int
    room_type_count: int
    equipment_count: int
    room_count: int


@dataclass(frozen=True, slots=True)
class ImportVersion:
    version_id: int
    source_name: str
    source_sha256: str
    created_at: str
    is_active: bool


class SqliteImportRepository:
    """Versioned, transactional storage for fully validated import batches."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connection() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                current_version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if current_version > SCHEMA_VERSION:
                    raise StorageError(
                        "Database schema is newer than this application version"
                    )
                connection.executescript(SCHEMA)
                if current_version == 1:
                    connection.execute(
                        "ALTER TABLE student_groups ADD COLUMN curriculum_code TEXT"
                    )
                workload_columns = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(workload_items)"
                    ).fetchall()
                }
                if "required_equipment_codes" not in workload_columns:
                    connection.execute(
                        "ALTER TABLE workload_items "
                        "ADD COLUMN required_equipment_codes TEXT NOT NULL DEFAULT ''"
                    )
                if current_version < SCHEMA_VERSION:
                    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except sqlite3.Error as error:
            raise StorageError("Unable to initialize timetable storage") from error

    def activate_import(
        self,
        batch: ImportBatch,
        *,
        source_name: str,
        source_sha256: str,
    ) -> ImportReceipt:
        safe_source_name = Path(source_name.replace("\\", "/")).name[:255]
        if not safe_source_name:
            raise ValueError("source_name must contain a filename")
        if SHA256_PATTERN.fullmatch(source_sha256) is None:
            raise ValueError("source_sha256 must be a lowercase SHA-256 digest")

        created_at = datetime.now(timezone.utc).isoformat()
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    """
                    SELECT version_id, created_at
                    FROM import_versions
                    WHERE source_sha256 = ?
                    """,
                    (source_sha256,),
                ).fetchone()

                if existing is not None:
                    version_id = int(existing["version_id"])
                    created_at = str(existing["created_at"])
                    reused = True
                    counts = self._version_counts(connection, version_id)
                else:
                    cursor = connection.execute(
                        """
                        INSERT INTO import_versions
                            (source_name, source_sha256, created_at, is_active)
                        VALUES (?, ?, ?, 0)
                        """,
                        (safe_source_name, source_sha256, created_at),
                    )
                    version_id = int(cursor.lastrowid)
                    reused = False
                    self._insert_batch(connection, version_id, batch)
                    teacher_count = len(batch.teachers)
                    group_count = len(batch.groups)
                    workload_count = len(batch.workloads)
                    specialty_count = len(batch.specialties)
                    curriculum_count = len(batch.curricula)
                    discipline_count = len(batch.disciplines)
                    student_count = len(batch.students)
                    building_count = len(batch.buildings)
                    room_type_count = len(batch.room_types)
                    equipment_count = len(batch.equipment)
                    room_count = len(batch.rooms)

                if existing is not None:
                    (
                        teacher_count,
                        group_count,
                        workload_count,
                        specialty_count,
                        curriculum_count,
                        discipline_count,
                        student_count,
                        building_count,
                        room_type_count,
                        equipment_count,
                        room_count,
                    ) = counts

                connection.execute(
                    "UPDATE import_versions SET is_active = 0 WHERE is_active = 1"
                )
                connection.execute(
                    "UPDATE import_versions SET is_active = 1 WHERE version_id = ?",
                    (version_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise StorageError("Import was not saved; active data is unchanged") from error

        return ImportReceipt(
            version_id=version_id,
            created_at=created_at,
            reused=reused,
            teacher_count=teacher_count,
            group_count=group_count,
            workload_count=workload_count,
            specialty_count=specialty_count,
            curriculum_count=curriculum_count,
            discipline_count=discipline_count,
            student_count=student_count,
            building_count=building_count,
            room_type_count=room_type_count,
            equipment_count=equipment_count,
            room_count=room_count,
        )

    @staticmethod
    def _version_counts(
        connection: sqlite3.Connection, version_id: int
    ) -> tuple[int, int, int, int, int, int, int, int, int, int, int]:
        teachers = connection.execute(
            "SELECT COUNT(*) FROM teachers WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        groups = connection.execute(
            "SELECT COUNT(*) FROM student_groups WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        workloads = connection.execute(
            "SELECT COUNT(*) FROM workload_items WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        specialties = connection.execute(
            "SELECT COUNT(*) FROM specialties WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        curricula = connection.execute(
            "SELECT COUNT(*) FROM curricula WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        disciplines = connection.execute(
            "SELECT COUNT(*) FROM curriculum_disciplines WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        students = connection.execute(
            "SELECT COUNT(*) FROM students WHERE import_version_id = ?",
            (version_id,),
        ).fetchone()[0]
        buildings = connection.execute(
            "SELECT COUNT(*) FROM buildings WHERE import_version_id = ?", (version_id,)
        ).fetchone()[0]
        room_types = connection.execute(
            "SELECT COUNT(*) FROM room_types WHERE import_version_id = ?", (version_id,)
        ).fetchone()[0]
        equipment = connection.execute(
            "SELECT COUNT(*) FROM equipment WHERE import_version_id = ?", (version_id,)
        ).fetchone()[0]
        rooms = connection.execute(
            "SELECT COUNT(*) FROM rooms WHERE import_version_id = ?", (version_id,)
        ).fetchone()[0]
        return (
            int(teachers),
            int(groups),
            int(workloads),
            int(specialties),
            int(curricula),
            int(disciplines),
            int(students),
            int(buildings),
            int(room_types),
            int(equipment),
            int(rooms),
        )

    def _insert_batch(
        self,
        connection: sqlite3.Connection,
        version_id: int,
        batch: ImportBatch,
    ) -> None:
        connection.executemany(
            "INSERT INTO buildings VALUES (?, ?, ?, ?)",
            [
                (
                    version_id,
                    item.building_code,
                    item.building_name,
                    int(item.active),
                )
                for item in batch.buildings
            ],
        )
        connection.executemany(
            "INSERT INTO room_types VALUES (?, ?, ?, ?)",
            [
                (
                    version_id,
                    item.room_type_code,
                    item.room_type_name,
                    int(item.active),
                )
                for item in batch.room_types
            ],
        )
        connection.executemany(
            "INSERT INTO equipment VALUES (?, ?, ?, ?)",
            [
                (
                    version_id,
                    item.equipment_code,
                    item.equipment_name,
                    int(item.active),
                )
                for item in batch.equipment
            ],
        )
        connection.executemany(
            "INSERT INTO rooms VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    version_id,
                    item.room_code,
                    item.room_name,
                    item.building_code,
                    item.room_type_code,
                    item.capacity,
                    ";".join(item.equipment_codes),
                    int(item.active),
                )
                for item in batch.rooms
            ],
        )
        connection.executemany(
            "INSERT INTO room_equipment VALUES (?, ?, ?)",
            [
                (version_id, room.room_code, equipment_code)
                for room in batch.rooms
                for equipment_code in room.equipment_codes
            ],
        )
        connection.executemany(
            """
            INSERT INTO specialties VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item.specialty_code,
                    item.specialty_name,
                    item.qualification,
                    item.program_base.value,
                    item.education_form.value,
                    int(item.active),
                )
                for item in batch.specialties
            ],
        )
        connection.executemany(
            """
            INSERT INTO curricula VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item.curriculum_code,
                    item.specialty_code,
                    item.admission_year,
                    item.version,
                    item.valid_from.isoformat(),
                    item.valid_to.isoformat() if item.valid_to else None,
                    item.status.value,
                )
                for item in batch.curricula
            ],
        )
        connection.executemany(
            """
            INSERT INTO curriculum_disciplines VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    version_id,
                    item.curriculum_code,
                    item.discipline_code,
                    item.discipline_name,
                    item.section_code,
                    item.semester,
                    item.lesson_type.value,
                    item.planned_hours,
                    item.control_form,
                )
                for item in batch.disciplines
            ],
        )
        connection.executemany(
            """
            INSERT INTO teachers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item.teacher_code,
                    item.full_name,
                    item.department,
                    item.employment_type.value if item.employment_type else None,
                    item.yearly_assigned_hours,
                    item.yearly_limit_hours,
                    item.max_hours_per_day,
                    item.max_days_per_week,
                    item.home_building_code,
                    int(item.active),
                )
                for item in batch.teachers
            ],
        )
        connection.executemany(
            """
            INSERT INTO students VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item.student_code,
                    item.full_name,
                    item.group_code,
                    item.status.value,
                    item.enrollment_date.isoformat() if item.enrollment_date else None,
                    item.end_date.isoformat() if item.end_date else None,
                    ";".join(str(code) for code in item.subgroup_codes),
                    ";".join(item.elective_codes),
                )
                for item in batch.students
            ],
        )
        connection.executemany(
            """
            INSERT INTO student_groups (
                import_version_id,
                group_code,
                specialty_code,
                curriculum_code,
                course,
                education_form,
                headcount,
                program_base,
                study_week_type,
                primary_building_code,
                subgroup_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item.group_code,
                    item.specialty_code,
                    item.curriculum_code,
                    item.course,
                    item.education_form.value,
                    item.headcount,
                    item.program_base,
                    item.study_week_type,
                    item.primary_building_code,
                    item.subgroup_count,
                )
                for item in batch.groups
            ],
        )
        connection.executemany(
            """
            INSERT INTO workload_items VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                (
                    version_id,
                    item.workload_row_code,
                    item.academic_year,
                    item.semester,
                    item.discipline_code,
                    item.discipline_name,
                    item.group_code,
                    item.subgroup,
                    item.stream,
                    item.teacher_code,
                    item.lesson_type.value,
                    item.total_academic_hours,
                    item.event_duration_hours,
                    item.recurrence,
                    item.lesson_bundle_code,
                    item.room_type,
                    item.room_capacity,
                    ";".join(item.required_equipment_codes),
                )
                for item in batch.workloads
            ],
        )

    def list_versions(self) -> tuple[ImportVersion, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    """
                    SELECT version_id, source_name, source_sha256, created_at, is_active
                    FROM import_versions
                    ORDER BY version_id DESC
                    """
                ).fetchall()
        except sqlite3.Error as error:
            raise StorageError("Unable to list import versions") from error
        return tuple(
            ImportVersion(
                version_id=int(row["version_id"]),
                source_name=str(row["source_name"]),
                source_sha256=str(row["source_sha256"]),
                created_at=str(row["created_at"]),
                is_active=bool(row["is_active"]),
            )
            for row in rows
        )

    def activate_version(self, version_id: int) -> ImportVersion:
        if isinstance(version_id, bool) or version_id < 1:
            raise ValueError("version_id must be a positive integer")
        try:
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT version_id, source_name, source_sha256, created_at
                    FROM import_versions WHERE version_id = ?
                    """,
                    (version_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise VersionNotFoundError(
                        f"Import version {version_id} does not exist"
                    )
                connection.execute(
                    "UPDATE import_versions SET is_active = 0 WHERE is_active = 1"
                )
                connection.execute(
                    "UPDATE import_versions SET is_active = 1 WHERE version_id = ?",
                    (version_id,),
                )
                connection.commit()
        except sqlite3.Error as error:
            raise StorageError("Unable to activate import version") from error

        return ImportVersion(
            version_id=int(row["version_id"]),
            source_name=str(row["source_name"]),
            source_sha256=str(row["source_sha256"]),
            created_at=str(row["created_at"]),
            is_active=True,
        )

    def get_active_batch(self) -> ImportBatch | None:
        try:
            with self._connection() as connection:
                active = connection.execute(
                    "SELECT version_id FROM import_versions WHERE is_active = 1"
                ).fetchone()
                if active is None:
                    return None
                version_id = int(active["version_id"])
                teacher_rows = connection.execute(
                    "SELECT * FROM teachers WHERE import_version_id = ? ORDER BY teacher_code",
                    (version_id,),
                ).fetchall()
                group_rows = connection.execute(
                    "SELECT * FROM student_groups WHERE import_version_id = ? ORDER BY group_code",
                    (version_id,),
                ).fetchall()
                workload_rows = connection.execute(
                    """
                    SELECT * FROM workload_items
                    WHERE import_version_id = ? ORDER BY workload_row_code
                    """,
                    (version_id,),
                ).fetchall()
                specialty_rows = connection.execute(
                    """
                    SELECT * FROM specialties
                    WHERE import_version_id = ? ORDER BY specialty_code
                    """,
                    (version_id,),
                ).fetchall()
                curriculum_rows = connection.execute(
                    """
                    SELECT * FROM curricula
                    WHERE import_version_id = ? ORDER BY curriculum_code
                    """,
                    (version_id,),
                ).fetchall()
                discipline_rows = connection.execute(
                    """
                    SELECT * FROM curriculum_disciplines
                    WHERE import_version_id = ?
                    ORDER BY curriculum_code, discipline_code, semester, lesson_type
                    """,
                    (version_id,),
                ).fetchall()
                student_rows = connection.execute(
                    """
                    SELECT * FROM students
                    WHERE import_version_id = ? ORDER BY student_code
                    """,
                    (version_id,),
                ).fetchall()
                building_rows = connection.execute(
                    """
                    SELECT * FROM buildings
                    WHERE import_version_id = ? ORDER BY building_code
                    """,
                    (version_id,),
                ).fetchall()
                room_type_rows = connection.execute(
                    """
                    SELECT * FROM room_types
                    WHERE import_version_id = ? ORDER BY room_type_code
                    """,
                    (version_id,),
                ).fetchall()
                equipment_rows = connection.execute(
                    """
                    SELECT * FROM equipment
                    WHERE import_version_id = ? ORDER BY equipment_code
                    """,
                    (version_id,),
                ).fetchall()
                room_rows = connection.execute(
                    """
                    SELECT * FROM rooms
                    WHERE import_version_id = ? ORDER BY room_code
                    """,
                    (version_id,),
                ).fetchall()
        except sqlite3.Error as error:
            raise StorageError("Unable to read active import") from error

        try:
            return ImportBatch(
                teachers=tuple(self._teacher_from_row(row) for row in teacher_rows),
                groups=tuple(self._group_from_row(row) for row in group_rows),
                workloads=tuple(
                    self._workload_from_row(row) for row in workload_rows
                ),
                specialties=tuple(
                    self._specialty_from_row(row) for row in specialty_rows
                ),
                curricula=tuple(
                    self._curriculum_from_row(row) for row in curriculum_rows
                ),
                disciplines=tuple(
                    self._discipline_from_row(row) for row in discipline_rows
                ),
                students=tuple(self._student_from_row(row) for row in student_rows),
                buildings=tuple(self._building_from_row(row) for row in building_rows),
                room_types=tuple(self._room_type_from_row(row) for row in room_type_rows),
                equipment=tuple(self._equipment_from_row(row) for row in equipment_rows),
                rooms=tuple(self._room_from_row(row) for row in room_rows),
            )
        except ValidationError as error:
            raise StorageError("Database contains invalid stored data") from error

    @staticmethod
    def _teacher_from_row(row: sqlite3.Row) -> Teacher:
        return Teacher(
            teacher_code=row["teacher_code"],
            full_name=row["full_name"],
            department=row["department"],
            employment_type=row["employment_type"],
            yearly_assigned_hours=row["yearly_assigned_hours"],
            yearly_limit_hours=row["yearly_limit_hours"],
            max_hours_per_day=row["max_hours_per_day"],
            max_days_per_week=row["max_days_per_week"],
            home_building_code=row["home_building_code"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _group_from_row(row: sqlite3.Row) -> Group:
        return Group(
            group_code=row["group_code"],
            specialty_code=row["specialty_code"],
            curriculum_code=row["curriculum_code"],
            course=row["course"],
            education_form=row["education_form"],
            headcount=row["headcount"],
            program_base=row["program_base"],
            study_week_type=row["study_week_type"],
            primary_building_code=row["primary_building_code"],
            subgroup_count=row["subgroup_count"],
        )

    @staticmethod
    def _workload_from_row(row: sqlite3.Row) -> WorkloadItem:
        return WorkloadItem(
            workload_row_code=row["workload_row_code"],
            academic_year=row["academic_year"],
            semester=row["semester"],
            discipline_code=row["discipline_code"],
            discipline_name=row["discipline_name"],
            group_code=row["group_code"],
            subgroup=row["subgroup"],
            stream=row["stream"],
            teacher_code=row["teacher_code"],
            lesson_type=row["lesson_type"],
            total_academic_hours=row["total_academic_hours"],
            event_duration_hours=row["event_duration_hours"],
            recurrence=row["recurrence"],
            lesson_bundle_code=row["lesson_bundle_code"],
            room_type=row["room_type"],
            room_capacity=row["room_capacity"],
            required_equipment_codes=row["required_equipment_codes"],
        )

    @staticmethod
    def _specialty_from_row(row: sqlite3.Row) -> Specialty:
        return Specialty(
            specialty_code=row["specialty_code"],
            specialty_name=row["specialty_name"],
            qualification=row["qualification"],
            program_base=row["program_base"],
            education_form=row["education_form"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _curriculum_from_row(row: sqlite3.Row) -> Curriculum:
        return Curriculum(
            curriculum_code=row["curriculum_code"],
            specialty_code=row["specialty_code"],
            admission_year=row["admission_year"],
            version=row["version"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            status=row["status"],
        )

    @staticmethod
    def _discipline_from_row(row: sqlite3.Row) -> CurriculumDiscipline:
        return CurriculumDiscipline(
            curriculum_code=row["curriculum_code"],
            discipline_code=row["discipline_code"],
            discipline_name=row["discipline_name"],
            section_code=row["section_code"],
            semester=row["semester"],
            lesson_type=row["lesson_type"],
            planned_hours=row["planned_hours"],
            control_form=row["control_form"],
        )

    @staticmethod
    def _student_from_row(row: sqlite3.Row) -> Student:
        return Student(
            student_code=row["student_code"],
            full_name=row["full_name"],
            group_code=row["group_code"],
            status=row["status"],
            enrollment_date=row["enrollment_date"],
            end_date=row["end_date"],
            subgroup_codes=row["subgroup_codes"],
            elective_codes=row["elective_codes"],
        )

    @staticmethod
    def _building_from_row(row: sqlite3.Row) -> Building:
        return Building(
            building_code=row["building_code"],
            building_name=row["building_name"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _room_type_from_row(row: sqlite3.Row) -> RoomType:
        return RoomType(
            room_type_code=row["room_type_code"],
            room_type_name=row["room_type_name"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _equipment_from_row(row: sqlite3.Row) -> Equipment:
        return Equipment(
            equipment_code=row["equipment_code"],
            equipment_name=row["equipment_name"],
            active=bool(row["active"]),
        )

    @staticmethod
    def _room_from_row(row: sqlite3.Row) -> Room:
        return Room(
            room_code=row["room_code"],
            room_name=row["room_name"],
            building_code=row["building_code"],
            room_type_code=row["room_type_code"],
            capacity=row["capacity"],
            equipment_codes=row["equipment_codes"],
            active=bool(row["active"]),
        )
