"""Pydantic-моделі відповіді API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Status(str, Enum):
    PASSED = "passed"
    NEEDS_REVISION = "needs_revision"
    MANUAL_REVIEW = "manual_review"


class WorkType(str, Enum):
    DZ = "dz"
    HYPOTHESIS = "hypothesis"
    STRATEGIC_QUESTION = "strategic_question"
    UNKNOWN = "unknown"


class Routing(str, Enum):
    SEND_TO_EXPERTS = "send_to_experts"
    RETURN_TO_STUDENT = "return_to_student"
    MANUAL_REVIEW = "manual_review"


class Area(str, Enum):
    FILE_NAME = "file_name"
    FORMATTING = "formatting"
    STRUCTURE = "structure"
    CALCULATION = "calculation"
    FUNNEL = "funnel"
    FILE_FORMAT = "file_format"
    CLASSIFICATION = "classification"


class Issue(BaseModel):
    code: str
    area: Area
    description: str


class ExtractedData(BaseModel):
    student_name: str = ""
    business: str = ""
    city: str = ""
    package: str = ""
    flow_number: str = ""


class CheckResult(BaseModel):
    status: Status
    work_type: WorkType
    routing: Routing
    issues: list[Issue] = Field(default_factory=list)
    extracted_data: ExtractedData = Field(default_factory=ExtractedData)
