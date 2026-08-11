from __future__ import annotations

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
    course: int = Field(ge=1, le=6)
    education_form: EducationForm = EducationForm.FULL_TIME
    headcount: PositiveInt
    program_base: str | None = Field(default=None, max_length=64)
    study_week_type: str | None = Field(default=None, max_length=64)
    primary_building_code: Code | None = None
    subgroup_count: int = Field(default=1, ge=1, le=20)

    @field_validator("group_code", "specialty_code", "primary_building_code")
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


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

    @field_validator(
        "workload_row_code",
        "discipline_code",
        "group_code",
        "teacher_code",
        "lesson_bundle_code",
    )
    @classmethod
    def normalize_codes(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

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

