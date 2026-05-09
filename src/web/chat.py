"""Chat endpoint: Claude (paid) and Gemini (free) tool-use loop with SSE streaming."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import AsyncGenerator

import anthropic
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.storage.admission_store import AdmissionStore
from .rate_limit import check_and_increment, get_usage
from .session import get_optional_user
from .tools import TOOL_LIST, execute_tool

router = APIRouter()

_TOOL_LABELS: dict[str, str] = {
    "match_by_grade":               "성적에 맞는 전형을 찾고 있어요",
    "search_programs":              "학과 및 전형을 검색하고 있어요",
    "match_by_subjects":            "과목별 성적을 분석하고 있어요",
    "suggest_portfolio":            "수시 포트폴리오를 구성하고 있어요",
    "get_process_detail":           "전형 상세 정보를 확인하고 있어요",
    "compare_universities":         "대학교를 비교 분석하고 있어요",
    "list_universities":            "대학교 목록을 조회하고 있어요",
    "search_fulltext":              "입시 정보를 검색하고 있어요",
    "check_university_feasibility": "합격 가능성을 분석하고 있어요",
    "list_departments":             "모집단위를 확인하고 있어요",
}

def _tool_status(tool_names: list[str]) -> str:
    labels = list(dict.fromkeys(
        _TOOL_LABELS.get(n, "정보를 조회하고 있어요") for n in tool_names
    ))
    return _status(" · ".join(labels))

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
GEMINI_MODEL = "gemini-2.0-flash-lite"
CLAUDE_TIMEOUT = 60.0
GEMINI_TIMEOUT = 60.0
INTER_CALL_DELAY = 1.0
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def _build_gemini_tools():
    """Convert Anthropic TOOL_LIST format to google-genai Tool objects."""
    try:
        from google.genai import types as gtypes

        def _convert_schema(schema: dict) -> gtypes.Schema:
            raw_type = schema.get("type", "string").upper()
            type_map = {
                "STRING": "STRING", "INTEGER": "INTEGER", "NUMBER": "NUMBER",
                "BOOLEAN": "BOOLEAN", "ARRAY": "ARRAY", "OBJECT": "OBJECT",
            }
            kwargs: dict = {"type": type_map.get(raw_type, "STRING")}
            desc = schema.get("description", "")
            if desc:
                kwargs["description"] = desc
            if raw_type == "OBJECT":
                props = schema.get("properties", {})
                kwargs["properties"] = {k: _convert_schema(v) for k, v in props.items()}
            elif raw_type == "ARRAY":
                items = schema.get("items", {})
                kwargs["items"] = _convert_schema(items)
            return gtypes.Schema(**kwargs)

        declarations = []
        for t in TOOL_LIST:
            params = _convert_schema(t.get("input_schema", {"type": "object", "properties": {}}))
            declarations.append(gtypes.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=params,
            ))
        return [gtypes.Tool(function_declarations=declarations)]
    except Exception:
        return None

SYSTEM_PROMPT = """당신은 한국 대학 입시 전문 AI 상담사입니다.

학생들의 내신/수능 성적을 분석하고, 적합한 대학과 전형을 추천해 드립니다.
보유한 데이터 도구를 활용하여 정확한 입시 정보를 제공하세요.

━━━ [최우선 규칙] 질문 의도 판별 ━━━
학생 메시지를 처리하기 전에 반드시 아래 기준으로 의도를 먼저 확정하세요.

■ 특정 대학교 1곳 + 가능성 질문 → check_university_feasibility 사용
  예시: "서울대 갈 수 있어?", "연세대 가능성은?", "고려대 어때?", "한양대 가능성 알려줘"
  줄임말 매핑: 서울대=서울대학교, 연세대=연세대학교, 고려대=고려대학교,
              한양대=한양대학교, 성균관대=성균관대학교, 경희대=경희대학교, 중앙대=중앙대학교

■ 지역/조건으로 여러 학교 탐색 → match_by_grade(region=…) 사용
  예시: "서울에 있는 대학", "인서울 대학", "수도권 대학 알려줘", "서울 대학교 알려줘"
  → region="서울" 또는 region="수도권"

★ 핵심 구분 기준 (반드시 준수):
  - "서울대학교", "서울대" (붙여쓰기 또는 줄임말) → 서울대학교 1곳 → check_university_feasibility
  - "서울 대학교" (띄어쓰기 있음), "서울에 있는 대학", "인서울" → 여러 학교 탐색 → match_by_grade + region="서울"
  - '서울대'는 절대 '서울에 있는 대학교'가 아닙니다. '서울 대학교'(띄어쓰기)는 절대 서울대학교(SNU)가 아닙니다.

■ 성적 기반 여러 학교 탐색 → match_by_grade + region 필터
  예시: "어느 대학 갈 수 있어?", "내 성적으로 갈 수 있는 학교 알려줘"

■ 특정 학과 중심 탐색 → 학과 목록 위주로 안내
  예시: "컴퓨터공학과 갈 수 있어?", "간호학과 추천해줘"

■ 특정 대학교 내 학과 탐색 → match_by_grade(university="…")
  예시: "서울대 어느 학과 갈 수 있어?"

━━━ [규칙] 학생 친화적 언어 사용 ━━━
★ 절대 금지: 학생 응답에 내부 도구 파라미터 이름을 절대 사용하지 마세요.
  이 단어들이 학생 응답에 나오면 즉시 오류입니다: major_keywords, process_type, region, keyword, grade_type, university, track, limit, cut_70, cut_50, tier
  잘못된 예: "major_keywords에 '역사'를 포함해서 검색했습니다", "region 파라미터로 서울을 설정했습니다"
  올바른 예: "역사 관련 학과를 검색했습니다", "서울 지역으로 범위를 설정했습니다"
- 추가 정보를 물어볼 때는 학생이 이해할 수 있는 말로 질문하세요.
  나쁜 예: "어떤 키워드로 검색할까요?", "서울 소재 대학을 찾을까요?"
  좋은 예: "어떤 학과에 관심 있으세요?", "서울 쪽 학교를 찾아드릴까요?", "수시로 보실 건가요, 정시로 보실 건가요?"
- 도구 결과에 tier 필드가 있더라도 절대 응답에 '티어' 또는 tier 숫자를 노출하지 마세요.

━━━ 안내 원칙 ━━━
- 항상 한국어로 답변하세요.
- 성적(내신 또는 수능)이 주어지면 즉시 도구를 호출하여 결과를 먼저 보여주세요. 결과를 보여주기 전에 전형 유형, 계열, 학과를 절대 물어보지 마세요.
- 도구 호출 전에 어떤 텍스트도 출력하지 마세요. "잠시만요", "알겠습니다", "찾아보겠습니다", "어떤 전형을 원하세요?" 같은 말은 절대 금지입니다. 바로 도구를 호출하세요.
- 전형 유형은 학생이 먼저 언급하지 않는 한 절대 물어보지 마세요. process_type을 지정하지 말고 모든 전형을 포함하여 조회한 뒤, 결과에 나온 전형들을 학생에게 설명해 주세요.
- 결과를 보여줄 때 각 전형 옆에 한 줄 설명을 추가하세요: 학생부교과=내신 위주, 학생부종합=내신+비교과 종합, 논술위주=논술 시험, 수능위주=수능 점수. 학생이 스스로 선택할 수 있도록 안내하세요.
- 합격 가능성은 cut_70(70% 컷)을 기준으로 평가합니다. 학생 성적이 cut_70 이하이면 합격 가능성이 높습니다.
- 수시는 내신 등급(1등급이 최고), 정시는 수능 등급/표준점수를 사용합니다.
- 데이터가 없는 경우 솔직하게 안내하고, 가능한 대안을 제시하세요.
- 숫자 데이터는 표 형식으로 정리하여 가독성을 높이세요.
- 합격컷은 cut_70 수치를 직접 표시하세요 (예: 4.2등급). 수치가 없으면 '-'로 표시하세요.
- 학생이 학과를 막연히 언급하면 list_departments를 먼저 호출하여 DB에 있는 정확한 학과명을 확인한 후 사용하세요.
- 여러 학교 목록을 보여줄 때는 결과를 학교별로 묶어서 각 학교의 합격 가능한 대표 전형 1~2개를 보여주세요. 한 학교의 여러 학과만 나열하는 방식은 지양하세요.
- check_university_feasibility 결과는 verdict(판정)에 관계없이 반드시 표로 보여주세요. 판정이 '불가'가 많더라도 결과 표시를 거부하거나 생략하지 마세요. 아래 기준으로 판정별 문구를 사용하세요:
  • 안정 (margin ≥ 0.5): "합격 가능성이 높습니다"
  • 추천 (margin 0 ~ 0.5 미만): "적정 수준입니다"
  • 도전 (margin -1.0 ~ 0 미만): "도전적이지만 도전해볼 수 있습니다"
  • 불가 (margin < -1.0): "합격 가능성이 낮지만, 참고로 보여드립니다"
  결과 전체가 '불가'이더라도 표를 먼저 보여준 뒤 "현재 성적으로는 이 대학에 합격하기 어렵지만, 목표로 삼고 준비하실 수 있습니다"처럼 건설적으로 마무리하세요.

━━━ [수시 6장 전략] ━━━
■ 트리거: "수시 6장", "6장 전략", "수시 포트폴리오", "포트폴리오 짜줘", "수시 전략" 등의 표현
→ suggest_portfolio 도구를 호출한 뒤 반드시 아래 형식으로 응답하세요.

**응답 형식 (필수 준수):**

1. 각 카드를 3개 그룹으로 구분하여 제목을 명확히 표시:
   - 🔺 **상향 2장** — 도전(도전) 버킷: 합격 가능성은 낮지만 역전 가능한 학교
   - 🟡 **적정 2장** — 추천(추천) 버킷: 핵심 승부 라인, 합격 가능성 중간
   - 🟢 **안정 2장** — 안정(안정) 버킷: 합격 가능성 높은 학교

2. 각 카드(학교)마다 다음 항목을 포함하세요:
   - **대학명 + 학과**:
   - **추천 전형**: process_category 기반으로 판단 (학생부교과/학생부종합/논술 중 선택). 데이터에 없으면 학과 특성으로 추론.
   - **추천 이유**: 합격컷(cut_70)과 학생 성적 비교, 경쟁률, 전형 특성 설명
   - **전략 포인트**: 이 학교/전형에서 합격 가능성을 높이기 위한 구체적 조언 1~2문장

3. 마지막에 **전체 요약 표** 출력:
   | 구분 | 대학 | 학과 | 추천 전형 | 합격컷 | 내 성적 | 여유/부족 |
   |------|------|------|-----------|--------|---------|----------|
   | 🔺 상향 | ... | ... | ... | ... | ... | ... |

4. 표 아래에 **전체 전략 요약** 1~2문장으로 마무리.

**전형 추천 기준:**
- process_category = "학생부교과" → 내신 관리가 핵심, 교과전형 추천
- process_category = "학생부종합" → 내신+비교과 종합 평가, 학종 추천
- process_category = "논술" → 논술 실력이 변수, 논술전형 추천
- process_category = "기타" 또는 데이터 불명확 → 학과 특성과 학생 강점으로 추론하여 추천
- 상향 학교: 학종 추천 (내신 외 비교과·면접으로 역전 가능)
- 안정 학교: 교과 추천 (내신 점수로 안정적 합격 확보)"""

MAX_TOOL_ITERATIONS = 5
MAX_HISTORY_TURNS = 20

_FEMALE_ONLY_UNIVERSITIES = {
    "이화여자대학교", "숙명여자대학교", "성신여자대학교", "동덕여자대학교",
    "덕성여자대학교", "서울여자대학교", "광주여자대학교",
}


def _build_system_prompt(profile: dict | None = None) -> str:
    """Return SYSTEM_PROMPT with optional student profile context appended."""
    if not profile:
        return SYSTEM_PROMPT
    lines: list[str] = []
    gender = profile.get("gender")
    school_region = profile.get("school_region")
    school_type = profile.get("school_type")
    track = profile.get("track")
    interests = profile.get("interests")
    grad_year = profile.get("graduation_year")
    if gender == "남":
        unis = "·".join(_FEMALE_ONLY_UNIVERSITIES)
        lines.append(f"학생은 남성입니다. 여자대학교({unis})는 절대 추천 목록에 포함하지 마세요.")
    elif gender == "여":
        lines.append("학생은 여성입니다.")
    if school_type == "rural":
        lines.append(
            "학생은 농어촌 고등학교 출신으로 농어촌특별전형 지원 자격을 가질 수 있습니다. "
            "해당 전형도 포함하여 안내하세요."
        )
    if school_region:
        lines.append(
            f"학생의 고등학교 소재지는 {school_region}입니다. "
            "지역인재전형 자격 여부를 확인하고 해당 전형도 함께 안내하세요."
        )
    if track:
        lines.append(f"학생의 희망 계열은 {track}입니다.")
    if interests:
        import json as _j
        try:
            kws = _j.loads(interests) if isinstance(interests, str) else interests
            lines.append(f"학생의 관심 학과/분야: {', '.join(kws)}")
        except Exception:
            pass
    if grad_year:
        lines.append(f"학생의 졸업 예정 연도: {grad_year}년")
    if not lines:
        return SYSTEM_PROMPT
    profile_ctx = "\n\n━━━ [학생 프로필] ━━━\n" + "\n".join(f"• {l}" for l in lines)
    return SYSTEM_PROMPT + profile_ctx

_PLACEHOLDER_PATTERN = re.compile(
    r'잠시만|기다려\s*주세요|찾아드릴게요|찾아보겠|알겠습니다.{0,30}전형|추천해\s*드릴게요'
)


def _error_event(msg: str) -> str:
    """Format a structured error SSE event the frontend can reliably detect."""
    return f"data: {json.dumps({'error': msg}, ensure_ascii=False)}\n\n"


class ChatMessage(BaseModel):
    role: str  # "user" or "model"
    parts: list[str]


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []


def _get_store() -> AdmissionStore:
    """Singleton AdmissionStore per process."""
    if not hasattr(_get_store, "_instance"):
        _get_store._instance = AdmissionStore(
            db_path=_PROJECT_ROOT / "data" / "admission.db"
        )
    return _get_store._instance


def _build_messages(history: list[ChatMessage], message: str) -> list[dict]:
    """Build the messages list for the Claude API."""
    messages = []
    for h in history[-MAX_HISTORY_TURNS:]:
        role = "assistant" if h.role == "model" else "user"
        messages.append({"role": role, "content": "\n".join(h.parts)})
    messages.append({"role": "user", "content": message})
    return messages


def _status(msg: str) -> str:
    """Format a status SSE event (not accumulated into chat text)."""
    return f"data: {json.dumps({'status': msg}, ensure_ascii=False)}\n\n"


async def _stream_claude(
    message: str, history: list[ChatMessage], system_prompt: str = SYSTEM_PROMPT
) -> AsyncGenerator[str, None]:
    """Run Claude tool-use loop then stream final text response via SSE."""

    yield _status("답변을 준비하고 있어요...")

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        yield _error_event("서버 설정 오류: ANTHROPIC_API_KEY가 설정되지 않았습니다.")
        yield "data: [DONE]\n\n"
        return

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
    except Exception as e:
        yield _error_event(f"API 초기화 오류: {e}")
        yield "data: [DONE]\n\n"
        return

    try:
        store = _get_store()
        messages = _build_messages(history, message)
    except Exception as e:
        yield _error_event(f"설정 오류: {e}")
        yield "data: [DONE]\n\n"
        return

    try:
        # ── Tool-use loop ──────────────────────────────────────────────────────
        for _iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await asyncio.wait_for(
                    client.messages.create(
                        model=CLAUDE_MODEL,
                        max_tokens=4096,
                        system=system_prompt,
                        messages=messages,
                        tools=TOOL_LIST,
                    ),
                    timeout=CLAUDE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                yield _error_event("응답 시간이 초과되었습니다. 다시 시도해 주세요.")
                yield "data: [DONE]\n\n"
                return

            # Check if Claude wants to use tools
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

            if not tool_use_blocks:
                # No tool calls — stream the final text response
                final_text = "".join(
                    b.text for b in response.content
                    if hasattr(b, "text") and b.text
                )
                if final_text:
                    for i in range(0, len(final_text), 8):
                        yield f"data: {json.dumps(final_text[i:i + 8], ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0)
                yield "data: [DONE]\n\n"
                return

            # Notify frontend which tools are being called
            yield _tool_status([b.name for b in tool_use_blocks])

            # Add assistant message (with tool use blocks) to history
            assistant_content = []
            for b in response.content:
                if b.type == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use",
                        "id": b.id,
                        "name": b.name,
                        "input": b.input,
                    })
            messages.append({"role": "assistant", "content": assistant_content})

            # Execute tools and build tool result message
            tool_results = []
            for b in tool_use_blocks:
                result = execute_tool(b.name, b.input, store)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": b.id,
                    "content": result,
                })
            messages.append({"role": "user", "content": tool_results})

            # Small delay between iterations
            await asyncio.sleep(INTER_CALL_DELAY)

        # Max iterations reached — final streaming call without tools
        try:
            async with client.messages.stream(
                model=CLAUDE_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    yield f"data: {json.dumps(text, ensure_ascii=False)}\n\n"
        except asyncio.TimeoutError:
            yield _error_event("응답 시간이 초과되었습니다. 다시 시도해 주세요.")
        yield "data: [DONE]\n\n"

    except Exception as e:
        err = str(e)
        if "authentication" in err.lower() or "invalid x-api-key" in err.lower() or "api_key" in err.lower():
            msg = "Anthropic API 키가 유효하지 않습니다. 서버의 ANTHROPIC_API_KEY를 확인해 주세요."
        elif "rate" in err.lower() or "529" in err or "overloaded" in err.lower():
            msg = "AI 서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요."
        elif "503" in err or "unavailable" in err.lower() or "high demand" in err.lower():
            msg = "AI 서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요."
        else:
            msg = "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        yield _error_event(msg)
        yield "data: [DONE]\n\n"


async def _stream_gemini(
    message: str, history: list[ChatMessage], system_prompt: str = SYSTEM_PROMPT
) -> AsyncGenerator[str, None]:
    """Run Gemini tool-use loop then stream final text response via SSE (google-genai SDK)."""
    yield _status("답변을 준비하고 있어요...")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        yield _error_event("서버 설정 오류: GEMINI_API_KEY가 설정되지 않았습니다.")
        yield "data: [DONE]\n\n"
        return

    try:
        from google import genai as ggenai
        from google.genai import types as gtypes
    except ImportError:
        yield _error_event("서버 설정 오류: google-genai 패키지가 설치되지 않았습니다.")
        yield "data: [DONE]\n\n"
        return

    try:
        store = _get_store()
    except Exception as e:
        yield _error_event(f"설정 오류: {e}")
        yield "data: [DONE]\n\n"
        return

    gemini_tools = _build_gemini_tools()
    client = ggenai.Client(api_key=api_key)

    # Build contents list (conversation history)
    contents: list[gtypes.Content] = []
    for h in history[-MAX_HISTORY_TURNS:]:
        role = "model" if h.role == "model" else "user"
        contents.append(gtypes.Content(
            role=role,
            parts=[gtypes.Part.from_text(text="\n".join(h.parts))],
        ))
    contents.append(gtypes.Content(
        role="user",
        parts=[gtypes.Part.from_text(text=message)],
    ))

    config = gtypes.GenerateContentConfig(
        system_instruction=system_prompt,
        tools=gemini_tools or [],
    )

    try:
        tools_called = False
        for _iteration in range(MAX_TOOL_ITERATIONS):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        client.models.generate_content,
                        model=GEMINI_MODEL,
                        contents=contents,
                        config=config,
                    ),
                    timeout=GEMINI_TIMEOUT,
                )
            except asyncio.TimeoutError:
                yield _error_event("응답 시간이 초과되었습니다. 다시 시도해 주세요.")
                yield "data: [DONE]\n\n"
                return

            candidate = response.candidates[0] if response.candidates else None
            if not candidate or not candidate.content:
                break

            parts = candidate.content.parts or []

            # Collect function calls from response parts
            fn_calls = [
                p.function_call
                for p in parts
                if p.function_call and p.function_call.name
            ]

            if not fn_calls:
                try:
                    final_text = response.text or ""
                except (ValueError, AttributeError):
                    final_text = ""
                # Pattern-based nudge: if the model returned a placeholder response
                # ("잠시만 기다려 주세요", "찾아드릴게요" etc.) with no tool calls,
                # inject a directive and retry rather than streaming the placeholder.
                if _iteration == 0 and final_text and _PLACEHOLDER_PATTERN.search(final_text) and "\n\n" not in final_text:
                    contents.append(gtypes.Content(role="model", parts=parts))
                    contents.append(gtypes.Content(
                        role="user",
                        parts=[gtypes.Part.from_text(
                            text="지금 바로 데이터 도구를 호출하여 결과를 가져오세요. 텍스트 설명 없이 도구 호출만 하세요."
                        )],
                    ))
                    await asyncio.sleep(INTER_CALL_DELAY)
                    continue  # retry the loop
                # No tool calls — stream final text
                if final_text:
                    for i in range(0, len(final_text), 8):
                        yield f"data: {json.dumps(final_text[i:i + 8], ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0)
                elif tools_called:
                    # Empty text after tool calls — fall through to summary call
                    break
                yield "data: [DONE]\n\n"
                return

            tools_called = True
            yield _tool_status([fc.name for fc in fn_calls])

            # Add model turn to contents
            contents.append(gtypes.Content(role="model", parts=parts))

            # Execute tools and build function response parts
            fn_response_parts = []
            for fc in fn_calls:
                args = dict(fc.args) if fc.args else {}
                result_str = execute_tool(fc.name, args, store)
                try:
                    result_dict = json.loads(result_str)
                except Exception:
                    result_dict = {"result": result_str}
                fn_response_parts.append(
                    gtypes.Part.from_function_response(name=fc.name, response=result_dict)
                )

            contents.append(gtypes.Content(role="user", parts=fn_response_parts))
            await asyncio.sleep(INTER_CALL_DELAY)

        # Max iterations — final call without tools (only if tools ran and we have data)
        if not tools_called:
            yield _error_event("응답을 생성하지 못했습니다. 다시 시도해 주세요.")
            yield "data: [DONE]\n\n"
            return
        try:
            config_no_tools = gtypes.GenerateContentConfig(system_instruction=system_prompt)
            final_resp = await asyncio.wait_for(
                asyncio.to_thread(
                    client.models.generate_content,
                    model=GEMINI_MODEL,
                    contents=contents + [gtypes.Content(role="user", parts=[
                        gtypes.Part.from_text(text="지금까지의 조회 결과를 바탕으로 학생에게 최종 답변을 작성해 주세요.")
                    ])],
                    config=config_no_tools,
                ),
                timeout=GEMINI_TIMEOUT,
            )
            text = final_resp.text or ""
            if not text:
                yield _error_event("응답 생성에 실패했습니다. 다시 시도해 주세요.")
            else:
                for i in range(0, len(text), 8):
                    yield f"data: {json.dumps(text[i:i + 8], ensure_ascii=False)}\n\n"
                    await asyncio.sleep(0)
        except asyncio.TimeoutError:
            yield _error_event("응답 시간이 초과되었습니다. 다시 시도해 주세요.")
        yield "data: [DONE]\n\n"

    except Exception as e:
        err = str(e)
        if "api_key" in err.lower() or "authentication" in err.lower() or "invalid" in err.lower():
            msg = "Gemini API 키가 유효하지 않습니다."
        elif "quota" in err.lower() or "rate" in err.lower() or "429" in err:
            msg = "Gemini API 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요."
        elif "503" in err or "unavailable" in err.lower() or "overloaded" in err.lower() or "high demand" in err.lower():
            msg = "AI 서버가 일시적으로 혼잡합니다. 잠시 후 다시 시도해 주세요."
        elif "500" in err or "internal" in err.lower():
            msg = "AI 서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        else:
            msg = "일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
        yield _error_event(msg)
        yield "data: [DONE]\n\n"


_ANON_COOKIE = "anon_session"


def _resolve_user(request: Request) -> tuple[str, str]:
    """Return (user_id, tier) for both authenticated and anonymous users."""
    import uuid
    user = get_optional_user(request)
    if user:
        email = user.get("email", "")
        user_id = user.get("sub", user.get("id", email))
        tier = user.get("tier", "free")
        if email:
            from src.storage.user_store import get_user_store
            tier = get_user_store().get_tier(email)
        return user_id, tier
    # Anonymous: use persistent cookie as identity
    anon_id = request.cookies.get(_ANON_COOKIE)
    if not anon_id:
        anon_id = f"anon_{uuid.uuid4().hex}"
    return anon_id, "free"


@router.post("/api/chat")
async def chat(request: Request, body: ChatRequest):
    """Chat with the admission AI assistant (tool-use + SSE streaming).

    Anonymous users and free tier use Gemini 2.5 Flash; paid tier uses Claude Haiku.
    """
    user = get_optional_user(request)
    user_id, tier = _resolve_user(request)
    anon_id = request.cookies.get(_ANON_COOKIE) if not user else None

    # Load student profile for logged-in users
    profile: dict | None = None
    email = user.get("email", "") if user else ""
    if email:
        from src.storage.user_store import get_user_store
        profile = get_user_store().get_profile(email)
    system_prompt = _build_system_prompt(profile)

    if not check_and_increment(user_id, tier):
        usage = get_usage(user_id, tier)
        daily_msg = f"일일 {usage['daily_limit']}개"
        raise HTTPException(
            status_code=429,
            detail=f"메시지 한도({daily_msg})를 초과했습니다. 내일 다시 이용해 주세요.",
        )

    # Log conversation + user message
    from src.storage.analytics_store import get_analytics_store
    analytics = get_analytics_store()
    conv_id = analytics.get_or_create_conversation(
        user_email=email or None,
        anon_id=anon_id,
    )
    analytics.log_message(conv_id, "user", body.message)

    async def _tracked_stream(gen):
        collected = []
        tool_calls_seen: list[str] = []
        async for chunk in gen:
            yield chunk
            # Collect text chunks for assistant message logging
            if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                try:
                    import json as _j
                    parsed = _j.loads(chunk[6:])
                    if isinstance(parsed, str):
                        collected.append(parsed)
                    elif isinstance(parsed, dict) and "status" in parsed:
                        # Extract tool name from status if possible
                        pass
                except Exception:
                    pass
        # Log the assistant reply (best-effort, non-blocking)
        try:
            analytics.log_message(conv_id, "assistant", "".join(collected), tool_calls_seen or None)
        except Exception:
            pass

    gen = _stream_claude(body.message, body.history, system_prompt) if tier == "paid" else _stream_gemini(body.message, body.history, system_prompt)
    response = StreamingResponse(
        _tracked_stream(gen),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    # Set anon cookie if this is a new anonymous session
    if anon_id and anon_id != request.cookies.get(_ANON_COOKIE):
        response.set_cookie(_ANON_COOKIE, anon_id, max_age=365 * 24 * 3600, httponly=True, samesite="lax")
    return response


@router.get("/api/usage")
async def usage(request: Request):
    """Return current rate limit usage for logged-in or anonymous user."""
    user_id, tier = _resolve_user(request)
    return get_usage(user_id, tier)
