"""Stage 1: Parse natural language query into StudentProfile using Claude tool_use."""

from __future__ import annotations

import anthropic

from .models import StudentProfile

PARSE_SYSTEM = """You are a Korean university admission counselor assistant.
Extract the student's profile from their natural language question.
Use the provided tool to return structured data."""

PARSE_TOOL = {
    "name": "student_profile",
    "description": "Extract student profile from their question about university admission.",
    "input_schema": {
        "type": "object",
        "properties": {
            "target_department_keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords for target departments. Generate variations: e.g. for '영문과' → ['영어영문', '영문학', '영어학']. For '컴퓨터' → ['컴퓨터공학', '소프트웨어', '컴퓨터과학'].",
            },
            "suneung_grades": {
                "type": "object",
                "description": "수능 등급 by subject. Keys: 국어, 수학, 영어, 탐구, 한국사. Values: integer grade (1-9) or null if unknown.",
                "additionalProperties": {"type": ["integer", "null"]},
            },
            "gpa_grade": {
                "type": ["number", "null"],
                "description": "내신 등급 (학생부 교과 평균 등급). 1.0-9.0 scale.",
            },
            "preferred_process_types": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Preferred 전형 types from: 학생부교과, 학생부종합, 논술, 실기, 수능. Empty if no preference.",
            },
            "preferred_admission_type": {
                "type": ["string", "null"],
                "description": "수시 or 정시. null if no preference.",
            },
            "preferred_regions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Preferred regions (e.g. 서울, 경기, 부산). Empty if no preference.",
            },
            "additional_context": {
                "type": ["string", "null"],
                "description": "Any other relevant context from the question (extracurriculars, special circumstances, etc.)",
            },
        },
        "required": ["target_department_keywords", "suneung_grades"],
    },
}


def parse_query(client: anthropic.Anthropic, model: str, question: str) -> StudentProfile:
    """Parse a natural language question into a StudentProfile."""
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=PARSE_SYSTEM,
        tools=[PARSE_TOOL],
        tool_choice={"type": "tool", "name": "student_profile"},
        messages=[{"role": "user", "content": question}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "student_profile":
            return StudentProfile(**block.input)

    raise ValueError("LLM did not return a student_profile tool call")
