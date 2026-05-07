"""Data models for the recommendation pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StudentProfile(BaseModel):
    """Parsed student profile from natural language query."""

    target_department_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords for target departments (e.g. ['영어영문', '영문학'])",
    )
    suneung_grades: dict[str, int | None] = Field(
        default_factory=dict,
        description="수능 등급 by subject: 국어, 수학, 영어, 탐구, 한국사, etc.",
    )
    gpa_grade: float | None = Field(
        default=None,
        description="내신 등급 (e.g. 3.0)",
    )
    preferred_process_types: list[str] = Field(
        default_factory=list,
        description="Preferred 전형 types: 학생부교과, 학생부종합, 논술, 실기, 수능",
    )
    preferred_admission_type: str | None = Field(
        default=None,
        description="수시 or 정시",
    )
    preferred_regions: list[str] = Field(
        default_factory=list,
        description="Preferred regions (e.g. ['서울', '경기'])",
    )
    additional_context: str | None = Field(
        default=None,
        description="Any other context from the user's question",
    )


class CandidateProcess(BaseModel):
    """A candidate admission process from DB filtering."""

    process_id: int
    university: str
    department_name: str
    process_name: str
    process_type: str | None = None
    admission_type: str | None = None
    quota: int | None = None
    content_summary: str = ""


class Recommendation(BaseModel):
    """A single recommendation with analysis."""

    university: str
    department_name: str
    process_name: str
    process_type: str | None = None
    admission_type: str | None = None
    quota: int | None = None
    verdict: str = Field(description="추천/도전/안정")
    suneung_analysis: str = ""
    gpa_analysis: str = ""
    overall_assessment: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class RecommendationResult(BaseModel):
    """Complete result of the recommendation pipeline."""

    profile: StudentProfile
    candidate_count: int = 0
    screened_count: int = 0
    recommendations: list[Recommendation] = Field(default_factory=list)
