"""Stage 3: LLM-powered screening and analysis of candidate processes."""

from __future__ import annotations

import json

import anthropic

from .models import CandidateProcess, Recommendation, StudentProfile

SCREEN_SYSTEM = """You are a Korean university admission expert. You have deep knowledge of:
- 수능 최저학력기준 (CSAT minimum requirements) for major Korean universities
- 내신 (GPA) competitiveness by university tier
- 전형 (admission process) types and their requirements

Your task: screen candidate admission processes for a student and classify each as realistic, borderline, or unlikely.
Use your general knowledge about Korean university admission standards."""

ANALYZE_SYSTEM = """You are a Korean university admission counselor providing detailed recommendations.
For each candidate, analyze:
1. 수능 최저학력기준: Does the student likely meet the minimum CSAT requirements?
2. 내신 경쟁력: Is the student's GPA competitive for this program?
3. Overall verdict: 안정 (safe), 도전 (reach), or 추천 (recommended)

Be practical and specific. Use your knowledge of Korean university admission standards.
Output valid JSON only."""

SCREEN_TOOL = {
    "name": "screen_results",
    "description": "Return screening results for candidate processes.",
    "input_schema": {
        "type": "object",
        "properties": {
            "screened": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "0-based index of the candidate"},
                        "classification": {
                            "type": "string",
                            "enum": ["realistic", "borderline", "unlikely"],
                        },
                        "reason": {"type": "string", "description": "Brief reason in Korean"},
                    },
                    "required": ["index", "classification"],
                },
            },
        },
        "required": ["screened"],
    },
}

ANALYZE_TOOL = {
    "name": "recommendations",
    "description": "Return detailed recommendations for the student.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "description": "0-based index in the finalist list"},
                        "verdict": {
                            "type": "string",
                            "enum": ["안정", "도전", "추천"],
                            "description": "안정=safe, 도전=reach, 추천=good match",
                        },
                        "suneung_analysis": {"type": "string", "description": "수능 최저 analysis in Korean"},
                        "gpa_analysis": {"type": "string", "description": "내신 분석 in Korean"},
                        "overall_assessment": {"type": "string", "description": "종합 평가 in Korean"},
                        "confidence": {"type": "number", "description": "0.0-1.0 confidence"},
                    },
                    "required": ["index", "verdict", "overall_assessment"],
                },
            },
        },
        "required": ["recommendations"],
    },
}


def _build_profile_summary(profile: StudentProfile) -> str:
    """Build a concise Korean profile summary."""
    parts = []
    if profile.target_department_keywords:
        parts.append(f"목표학과: {', '.join(profile.target_department_keywords)}")
    if profile.suneung_grades:
        grades = " ".join(f"{k}{v}" for k, v in profile.suneung_grades.items() if v is not None)
        parts.append(f"수능: {grades}")
    if profile.gpa_grade is not None:
        parts.append(f"내신: {profile.gpa_grade}등급")
    if profile.preferred_admission_type:
        parts.append(f"전형: {profile.preferred_admission_type}")
    if profile.additional_context:
        parts.append(f"기타: {profile.additional_context}")
    return " | ".join(parts)


def _build_candidate_lines(candidates: list[CandidateProcess]) -> str:
    """Build compact one-line summaries for screening."""
    lines = []
    for i, c in enumerate(candidates):
        quota_str = f"정원{c.quota}" if c.quota else ""
        type_str = c.process_type or ""
        adm_str = c.admission_type or ""
        lines.append(
            f"[{i}] {c.university} | {c.department_name} | {c.process_name} | {type_str} | {adm_str} | {quota_str}"
        )
    return "\n".join(lines)


def screen_candidates(
    client: anthropic.Anthropic,
    model: str,
    profile: StudentProfile,
    candidates: list[CandidateProcess],
) -> list[tuple[CandidateProcess, str]]:
    """Pass A: Screen all candidates. Returns (candidate, classification) pairs for realistic+borderline."""
    if not candidates:
        return []

    profile_summary = _build_profile_summary(profile)
    candidate_lines = _build_candidate_lines(candidates)

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=SCREEN_SYSTEM,
        tools=[SCREEN_TOOL],
        tool_choice={"type": "tool", "name": "screen_results"},
        messages=[{
            "role": "user",
            "content": f"""학생 프로필: {profile_summary}

다음 후보 전형들을 평가해주세요. 각각 realistic(합격 가능성 높음), borderline(도전적), unlikely(가능성 낮음)로 분류해주세요.

{candidate_lines}""",
        }],
    )

    # Parse screening results
    screened_indices: dict[int, str] = {}
    for block in response.content:
        if block.type == "tool_use" and block.name == "screen_results":
            for item in block.input.get("screened", []):
                idx = item["index"]
                classification = item["classification"]
                if 0 <= idx < len(candidates):
                    screened_indices[idx] = classification

    # Return realistic + borderline candidates
    result = []
    for idx, classification in screened_indices.items():
        if classification in ("realistic", "borderline"):
            result.append((candidates[idx], classification))

    return result


def analyze_finalists(
    client: anthropic.Anthropic,
    model: str,
    profile: StudentProfile,
    finalists: list[tuple[CandidateProcess, str]],
    limit: int = 10,
) -> list[Recommendation]:
    """Pass B: Detailed analysis of screened finalists."""
    if not finalists:
        return []

    profile_summary = _build_profile_summary(profile)

    # Build detailed finalist info
    finalist_lines = []
    for i, (c, classification) in enumerate(finalists):
        content_part = f"\n  내용: {c.content_summary}" if c.content_summary else ""
        finalist_lines.append(
            f"[{i}] {c.university} | {c.department_name} | {c.process_name} | "
            f"{c.process_type or ''} | {c.admission_type or ''} | "
            f"정원 {c.quota or '?'} | 스크리닝: {classification}{content_part}"
        )

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=ANALYZE_SYSTEM,
        tools=[ANALYZE_TOOL],
        tool_choice={"type": "tool", "name": "recommendations"},
        messages=[{
            "role": "user",
            "content": f"""학생 프로필: {profile_summary}

다음 후보들에 대해 상세 분석해주세요. 최대 {limit}개까지 추천해주세요.
가장 적합한 순서대로 분석해주세요.

{chr(10).join(finalist_lines)}""",
        }],
    )

    # Parse recommendations
    recommendations: list[Recommendation] = []
    for block in response.content:
        if block.type == "tool_use" and block.name == "recommendations":
            for item in block.input.get("recommendations", []):
                idx = item["index"]
                if 0 <= idx < len(finalists):
                    c, _ = finalists[idx]
                    recommendations.append(Recommendation(
                        university=c.university,
                        department_name=c.department_name,
                        process_name=c.process_name,
                        process_type=c.process_type,
                        admission_type=c.admission_type,
                        quota=c.quota,
                        verdict=item.get("verdict", "추천"),
                        suneung_analysis=item.get("suneung_analysis", ""),
                        gpa_analysis=item.get("gpa_analysis", ""),
                        overall_assessment=item.get("overall_assessment", ""),
                        confidence=item.get("confidence", 0.5),
                    ))

                if len(recommendations) >= limit:
                    break

    return recommendations
