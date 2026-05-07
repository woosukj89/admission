"""Orchestrator: ties together query parsing, DB filtering, and LLM analysis."""

from __future__ import annotations

import anthropic

from ..storage.admission_store import AdmissionStore
from .analyzer import analyze_finalists, screen_candidates
from .db_filter import filter_candidates
from .models import RecommendationResult, StudentProfile
from .query_parser import parse_query


class RecommendationPipeline:
    """3-stage recommendation pipeline: parse → filter → analyze."""

    def __init__(
        self,
        store: AdmissionStore,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
    ):
        self.store = store
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def run(
        self,
        question: str,
        admission_type: str | None = None,
        limit: int = 10,
        on_stage: callable = None,
    ) -> RecommendationResult:
        """Run the full recommendation pipeline.

        Args:
            question: Natural language question in Korean
            admission_type: Optional filter (수시/정시)
            limit: Max recommendations to return
            on_stage: Callback(stage_num, stage_name, detail) for progress
        """
        def _notify(stage: int, name: str, detail: str = ""):
            if on_stage:
                on_stage(stage, name, detail)

        # Stage 1: Parse query
        _notify(1, "학생 프로필 분석 중", "")
        profile = parse_query(self.client, self.model, question)
        _notify(1, "학생 프로필 분석 완료", self._profile_summary(profile))

        # Stage 2: DB filtering
        _notify(2, "후보 전형 검색 중", "")
        candidates = filter_candidates(self.store, profile, admission_type)
        _notify(2, "후보 전형 검색 완료", f"{len(candidates)}개 후보")

        if not candidates:
            return RecommendationResult(
                profile=profile,
                candidate_count=0,
                screened_count=0,
                recommendations=[],
            )

        # Stage 3a: Screen
        _notify(3, "AI 분석 중", f"{len(candidates)}개 후보 스크리닝")
        finalists = screen_candidates(self.client, self.model, profile, candidates)
        _notify(3, "스크리닝 완료", f"{len(finalists)}개 유효 후보")

        # Stage 3b: Analyze
        if finalists:
            recommendations = analyze_finalists(
                self.client, self.model, profile, finalists, limit=limit
            )
        else:
            recommendations = []

        _notify(3, "분석 완료", f"{len(recommendations)}개 추천")

        return RecommendationResult(
            profile=profile,
            candidate_count=len(candidates),
            screened_count=len(finalists),
            recommendations=recommendations,
        )

    @staticmethod
    def _profile_summary(profile: StudentProfile) -> str:
        parts = []
        if profile.target_department_keywords:
            parts.append(f"목표: {', '.join(profile.target_department_keywords[:3])}")
        if profile.suneung_grades:
            grades = " ".join(
                f"{k}{v}" for k, v in profile.suneung_grades.items() if v is not None
            )
            if grades:
                parts.append(f"수능: {grades}")
        if profile.gpa_grade is not None:
            parts.append(f"내신: {profile.gpa_grade}")
        return " | ".join(parts)
