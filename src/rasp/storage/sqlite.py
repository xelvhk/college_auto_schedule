from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from rasp.domain.models import Group, ImportBatch, Teacher, WorkloadItem


SCHEMA_VERSION = 1
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
                if current_version < SCHEMA_VERSION:
                    connection.execute("PRAGMA user_version = 1")
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
                    teacher_count, group_count, workload_count = self._version_counts(
                        connection, version_id
                    )
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
        )

    @staticmethod
    def _version_counts(
        connection: sqlite3.Connection, version_id: int
    ) -> tuple[int, int, int]:
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
        return int(teachers), int(groups), int(workloads)

    def _insert_batch(
        self,
        connection: sqlite3.Connection,
        version_id: int,
        batch: ImportBatch,
    ) -> None:
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
            INSERT INTO student_groups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item.group_code,
                    item.specialty_code,
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
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
        except sqlite3.Error as error:
            raise StorageError("Unable to read active import") from error

        try:
            return ImportBatch(
                teachers=tuple(self._teacher_from_row(row) for row in teacher_rows),
                groups=tuple(self._group_from_row(row) for row in group_rows),
                workloads=tuple(
                    self._workload_from_row(row) for row in workload_rows
                ),
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
        )
