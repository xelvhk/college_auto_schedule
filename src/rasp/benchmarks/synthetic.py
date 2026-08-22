from __future__ import annotations

from collections import Counter
from datetime import date, time, timedelta

from pydantic import BaseModel, ConfigDict, Field, computed_field

from rasp.domain.models import (
    AcademicYear,
    BellSlot,
    Building,
    CalendarPeriod,
    Curriculum,
    CurriculumDiscipline,
    Group,
    ImportBatch,
    Room,
    RoomType,
    Specialty,
    Student,
    Teacher,
    WorkloadItem,
)


class SyntheticCollegeScale(BaseModel):
    """Deterministic, privacy-safe benchmark dimensions."""

    model_config = ConfigDict(frozen=True)

    group_count: int = Field(default=40, ge=1)
    teacher_count: int = Field(default=100, ge=1)
    room_count: int = Field(default=60, ge=1)
    disciplines_per_group: int = Field(default=10, ge=1)
    events_per_workload: int = Field(default=50, ge=1)
    students_per_group: int = Field(default=25, ge=1)
    teaching_week_count: int = Field(default=18, ge=1, le=26)

    @computed_field
    @property
    def lesson_count(self) -> int:
        return (
            self.group_count
            * self.disciplines_per_group
            * self.events_per_workload
        )


def _bell_slots() -> tuple[BellSlot, ...]:
    slots: list[BellSlot] = []
    starts_at = time(8, 0)
    start_minutes = starts_at.hour * 60 + starts_at.minute
    for number in range(1, 9):
        slot_start = start_minutes + (number - 1) * 55
        slot_end = slot_start + 45
        slots.append(
            BellSlot(
                slot_code=f"S-{number:02d}",
                academic_year="2026/2027",
                shift_code="DAY",
                lesson_number=number,
                starts_at=time(slot_start // 60, slot_start % 60),
                ends_at=time(slot_end // 60, slot_end % 60),
            )
        )
    return tuple(slots)


def build_synthetic_college(
    scale: SyntheticCollegeScale | None = None,
) -> ImportBatch:
    """Build a stable medium-college dataset without personal or external data."""

    scale = scale or SyntheticCollegeScale()
    workload_hours = scale.events_per_workload * 2
    workload_count = scale.group_count * scale.disciplines_per_group
    teacher_workloads = Counter(
        workload_index % scale.teacher_count
        for workload_index in range(workload_count)
    )
    teachers = tuple(
        Teacher(
            teacher_code=f"T-{index + 1:03d}",
            full_name=f"Синтетический преподаватель {index + 1:03d}",
            yearly_assigned_hours=teacher_workloads[index] * workload_hours,
            yearly_limit_hours=max(900, teacher_workloads[index] * workload_hours),
            max_hours_per_day=8,
            max_days_per_week=5,
            home_building_code="MAIN",
        )
        for index in range(scale.teacher_count)
    )
    groups = tuple(
        Group(
            group_code=f"G-{index + 1:03d}",
            specialty_code="09.02.07",
            curriculum_code="CURR-2026",
            course=1,
            headcount=scale.students_per_group,
            program_base="9",
            study_week_type="five_days",
            primary_building_code="MAIN",
            subgroup_count=2,
        )
        for index in range(scale.group_count)
    )
    disciplines = tuple(
        CurriculumDiscipline(
            curriculum_code="CURR-2026",
            discipline_code=f"D-{index + 1:02d}",
            discipline_name=f"Синтетическая дисциплина {index + 1:02d}",
            semester=1,
            lesson_type="practice",
            planned_hours=workload_hours,
        )
        for index in range(scale.disciplines_per_group)
    )
    workloads: list[WorkloadItem] = []
    workload_index = 0
    for group in groups:
        for discipline in disciplines:
            teacher_index = workload_index % scale.teacher_count
            workloads.append(
                WorkloadItem(
                    workload_row_code=f"W-{workload_index + 1:04d}",
                    academic_year="2026/2027",
                    semester=1,
                    discipline_code=discipline.discipline_code,
                    discipline_name=discipline.discipline_name,
                    group_code=group.group_code,
                    teacher_code=f"T-{teacher_index + 1:03d}",
                    lesson_type="practice",
                    total_academic_hours=workload_hours,
                    event_duration_hours=2,
                    recurrence="weekly",
                    room_type="GENERAL",
                    room_capacity=scale.students_per_group,
                )
            )
            workload_index += 1
    students = tuple(
        Student(
            student_code=f"S-{group_index + 1:03d}-{student_index + 1:02d}",
            full_name=(
                f"Синтетический студент {group_index + 1:03d}-"
                f"{student_index + 1:02d}"
            ),
            group_code=f"G-{group_index + 1:03d}",
            status="active",
            enrollment_date=date(2026, 9, 1),
            subgroup_codes=((student_index % 2) + 1,),
        )
        for group_index in range(scale.group_count)
        for student_index in range(scale.students_per_group)
    )
    rooms = tuple(
        Room(
            room_code=f"R-{index + 1:03d}",
            room_name=f"Аудитория {index + 1:03d}",
            building_code="MAIN",
            room_type_code="GENERAL",
            capacity=max(30, scale.students_per_group),
        )
        for index in range(scale.room_count)
    )

    return ImportBatch(
        teachers=teachers,
        groups=groups,
        workloads=tuple(workloads),
        specialties=(
            Specialty(
                specialty_code="09.02.07",
                specialty_name=(
                    "Синтетическая информационная специальность"
                ),
                program_base="9",
                education_form="full_time",
            ),
        ),
        curricula=(
            Curriculum(
                curriculum_code="CURR-2026",
                specialty_code="09.02.07",
                admission_year=2026,
                version="benchmark-1",
                valid_from=date(2026, 9, 1),
                status="active",
            ),
        ),
        disciplines=disciplines,
        students=students,
        buildings=(Building(building_code="MAIN", building_name="Главный корпус"),),
        room_types=(
            RoomType(room_type_code="GENERAL", room_type_name="Учебная аудитория"),
        ),
        rooms=rooms,
        academic_years=(
            AcademicYear(
                academic_year="2026/2027",
                starts_on=date(2026, 8, 31),
                ends_on=date(2027, 8, 31),
            ),
        ),
        calendar_periods=(
            CalendarPeriod(
                period_code="SEM-1",
                academic_year="2026/2027",
                period_name="Первый семестр",
                period_type="teaching",
                starts_on=date(2026, 8, 31),
                ends_on=(
                    date(2026, 8, 31)
                    + timedelta(weeks=scale.teaching_week_count)
                    - timedelta(days=1)
                ),
                semester=1,
            ),
        ),
        bell_slots=_bell_slots(),
    )
