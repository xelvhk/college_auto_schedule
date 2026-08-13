from __future__ import annotations

from datetime import date, time
from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    StringConstraints,
    field_validator,
    model_validator,
)


Code = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]
ShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=255),
]


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def blank_strings_are_missing(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


class EmploymentType(StrEnum):
    STAFF = "staff"
    PART_TIME = "part_time"
    CONTRACTOR = "contractor"


class EducationForm(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    EXTRAMURAL = "extramural"


class LessonType(StrEnum):
    LECTURE = "lecture"
    PRACTICE = "practice"
    LAB = "lab"
    CONSULTATION = "consultation"
    EXAM = "exam"
    CREDIT = "credit"


class ProgramBase(StrEnum):
    NINE_CLASSES = "9"
    ELEVEN_CLASSES = "11"


class CurriculumStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class CalendarPeriodType(StrEnum):
    TEACHING = "teaching"
    PRACTICE = "practice"
    EXAM_SESSION = "exam_session"
    HOLIDAY = "holiday"
    VACATION = "vacation"


class StudentStatus(StrEnum):
    ACTIVE = "active"
    ACADEMIC_LEAVE = "academic_leave"
    GRADUATED = "graduated"
    DISMISSED = "dismissed"


class Teacher(DomainModel):
    teacher_code: Code
    full_name: ShortText
    department: str | None = Field(default=None, max_length=255)
    employment_type: EmploymentType | None = None
    yearly_assigned_hours: int = Field(ge=0)
    yearly_limit_hours: int | None = Field(default=None, gt=0)
    max_hours_per_day: int | None = Field(default=None, ge=1, le=16)
    max_days_per_week: int | None = Field(default=None, ge=1, le=7)
    home_building_code: Code | None = None
    active: bool = True

    @field_validator("teacher_code", "home_building_code")
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @model_validator(mode="after")
    def assigned_hours_fit_limit(self) -> Teacher:
        if (
            self.yearly_limit_hours is not None
            and self.yearly_assigned_hours > self.yearly_limit_hours
        ):
            raise ValueError("yearly_assigned_hours exceeds yearly_limit_hours")
        return self


class Group(DomainModel):
    group_code: Code
    specialty_code: Code | None = None
    curriculum_code: Code | None = None
    course: int = Field(ge=1, le=6)
    education_form: EducationForm = EducationForm.FULL_TIME
    headcount: PositiveInt
    program_base: str | None = Field(default=None, max_length=64)
    study_week_type: str | None = Field(default=None, max_length=64)
    primary_building_code: Code | None = None
    subgroup_count: int = Field(default=1, ge=1, le=20)

    @field_validator(
        "group_code", "specialty_code", "curriculum_code", "primary_building_code"
    )
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class Specialty(DomainModel):
    specialty_code: Code
    specialty_name: ShortText
    qualification: str | None = Field(default=None, max_length=255)
    program_base: ProgramBase
    education_form: EducationForm
    active: bool = True

    @field_validator("specialty_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class Curriculum(DomainModel):
    curriculum_code: Code
    specialty_code: Code
    admission_year: int = Field(ge=2000, le=2100)
    version: ShortText
    valid_from: date
    valid_to: date | None = None
    status: CurriculumStatus

    @field_validator("curriculum_code", "specialty_code")
    @classmethod
    def normalize_codes(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validity_period_is_ordered(self) -> Curriculum:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be earlier than valid_from")
        return self


class CurriculumDiscipline(DomainModel):
    curriculum_code: Code
    discipline_code: Code
    discipline_name: ShortText
    section_code: Code | None = None
    semester: int = Field(ge=1, le=12)
    lesson_type: LessonType
    planned_hours: int = Field(ge=0)
    control_form: str | None = Field(default=None, max_length=128)

    @field_validator("curriculum_code", "discipline_code", "section_code")
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @property
    def stable_key(self) -> tuple[str, str, int, LessonType]:
        return (
            self.curriculum_code,
            self.discipline_code,
            self.semester,
            self.lesson_type,
        )


class AcademicYear(DomainModel):
    academic_year: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}/\d{4}$")
    ]
    starts_on: date
    ends_on: date
    active: bool = True

    @model_validator(mode="after")
    def dates_match_year_code(self) -> AcademicYear:
        first_year, second_year = (int(part) for part in self.academic_year.split("/"))
        if second_year != first_year + 1:
            raise ValueError("academic_year must contain consecutive years")
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must not be earlier than starts_on")
        if self.starts_on.year != first_year or self.ends_on.year != second_year:
            raise ValueError("academic year dates must match academic_year")
        return self


class CalendarPeriod(DomainModel):
    period_code: Code
    academic_year: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}/\d{4}$")
    ]
    period_name: ShortText
    period_type: CalendarPeriodType
    starts_on: date
    ends_on: date
    semester: int | None = Field(default=None, ge=1, le=2)

    @field_validator("period_code")
    @classmethod
    def normalize_period_code(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def period_dates_are_ordered(self) -> CalendarPeriod:
        if self.ends_on < self.starts_on:
            raise ValueError("ends_on must not be earlier than starts_on")
        if self.period_type is CalendarPeriodType.TEACHING and self.semester is None:
            raise ValueError("teaching period requires semester")
        return self


class BellSlot(DomainModel):
    slot_code: Code
    academic_year: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}/\d{4}$")
    ]
    shift_code: Code
    lesson_number: int = Field(ge=1, le=20)
    starts_at: time
    ends_at: time

    @field_validator("slot_code", "shift_code")
    @classmethod
    def normalize_slot_codes(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def slot_times_are_ordered(self) -> BellSlot:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class Student(DomainModel):
    student_code: Code
    full_name: ShortText
    group_code: Code
    status: StudentStatus
    enrollment_date: date | None = None
    end_date: date | None = None
    subgroup_codes: tuple[int, ...] = ()
    elective_codes: tuple[Code, ...] = ()

    @field_validator("student_code", "group_code")
    @classmethod
    def normalize_codes(cls, value: str) -> str:
        return value.upper()

    @field_validator("subgroup_codes", mode="before")
    @classmethod
    def parse_subgroups(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            value = tuple(part.strip() for part in value.split(";") if part.strip())
        if isinstance(value, (tuple, list)):
            return tuple(sorted({int(part) for part in value}))
        return value

    @field_validator("elective_codes", mode="before")
    @classmethod
    def parse_electives(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            value = tuple(part.strip() for part in value.split(";") if part.strip())
        if isinstance(value, (tuple, list)):
            return tuple(sorted({str(part).strip().upper() for part in value}))
        return value

    @model_validator(mode="after")
    def enrollment_period_is_ordered(self) -> Student:
        if (
            self.enrollment_date is not None
            and self.end_date is not None
            and self.end_date < self.enrollment_date
        ):
            raise ValueError("end_date must not be earlier than enrollment_date")
        return self


class Building(DomainModel):
    building_code: Code
    building_name: ShortText
    active: bool = True

    @field_validator("building_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class RoomType(DomainModel):
    room_type_code: Code
    room_type_name: ShortText
    active: bool = True

    @field_validator("room_type_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class Equipment(DomainModel):
    equipment_code: Code
    equipment_name: ShortText
    active: bool = True

    @field_validator("equipment_code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class Room(DomainModel):
    room_code: Code
    room_name: ShortText
    building_code: Code
    room_type_code: Code
    capacity: PositiveInt
    equipment_codes: tuple[Code, ...] = ()
    active: bool = True

    @field_validator("room_code", "building_code", "room_type_code")
    @classmethod
    def normalize_codes(cls, value: str) -> str:
        return value.upper()

    @field_validator("equipment_codes", mode="before")
    @classmethod
    def parse_equipment(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            value = tuple(part.strip() for part in value.split(";") if part.strip())
        if isinstance(value, (tuple, list)):
            return tuple(sorted({str(part).strip().upper() for part in value}))
        return value


class WorkloadItem(DomainModel):
    workload_row_code: Code
    academic_year: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}/\d{4}$")
    ]
    semester: int = Field(ge=1, le=2)
    discipline_code: Code
    discipline_name: ShortText
    group_code: Code
    subgroup: str | None = Field(default=None, max_length=64)
    stream: str | None = Field(default=None, max_length=64)
    teacher_code: Code
    lesson_type: LessonType
    total_academic_hours: PositiveInt
    event_duration_hours: PositiveInt = Field(le=8)
    recurrence: str | None = Field(default=None, max_length=64)
    lesson_bundle_code: Code | None = None
    room_type: str | None = Field(default=None, max_length=64)
    room_capacity: int | None = Field(default=None, gt=0)
    required_equipment_codes: tuple[Code, ...] = ()

    @field_validator(
        "workload_row_code",
        "discipline_code",
        "group_code",
        "teacher_code",
        "lesson_bundle_code",
        "room_type",
    )
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("required_equipment_codes", mode="before")
    @classmethod
    def parse_required_equipment(cls, value: object) -> object:
        if value is None:
            return ()
        if isinstance(value, str):
            value = tuple(part.strip() for part in value.split(";") if part.strip())
        if isinstance(value, (tuple, list)):
            return tuple(sorted({str(part).strip().upper() for part in value}))
        return value

    @model_validator(mode="after")
    def hours_form_whole_events(self) -> WorkloadItem:
        if self.total_academic_hours % self.event_duration_hours:
            raise ValueError(
                "total_academic_hours must be divisible by event_duration_hours"
            )
        return self


class ImportBatch(DomainModel):
    teachers: tuple[Teacher, ...]
    groups: tuple[Group, ...]
    workloads: tuple[WorkloadItem, ...]
    specialties: tuple[Specialty, ...] = ()
    curricula: tuple[Curriculum, ...] = ()
    disciplines: tuple[CurriculumDiscipline, ...] = ()
    students: tuple[Student, ...] = ()
    buildings: tuple[Building, ...] = ()
    room_types: tuple[RoomType, ...] = ()
    equipment: tuple[Equipment, ...] = ()
    rooms: tuple[Room, ...] = ()
    academic_years: tuple[AcademicYear, ...] = ()
    calendar_periods: tuple[CalendarPeriod, ...] = ()
    bell_slots: tuple[BellSlot, ...] = ()


class ReferenceDataBatch(DomainModel):
    specialties: tuple[Specialty, ...]
    curricula: tuple[Curriculum, ...]
    disciplines: tuple[CurriculumDiscipline, ...]


class CalendarBatch(DomainModel):
    academic_years: tuple[AcademicYear, ...]
    periods: tuple[CalendarPeriod, ...]
    bell_slots: tuple[BellSlot, ...]
