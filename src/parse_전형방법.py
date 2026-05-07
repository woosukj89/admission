"""Parse 전형요소 반영비율 (evaluation component weights) from 전형방법 text.

Usage:
    from src.parse_전형방법 import parse_전형요소

    result = parse_전형요소("·학생부교과 100%")
    # Returns {"교과": 100} or multi-stage dict or None
"""

from __future__ import annotations

import re


# ── Component label normalization ─────────────────────────────────────────────

_COMPONENT_MAP: list[tuple[str, str]] = [
    # 교과 / 학생부교과
    (r"학생부\s*(?:위주\s*)?교과|학생부교과|교과성적|지정교과|교과정량|교과정성|교과", "교과"),
    # 서류 / 학생부종합
    (r"학생부종합|서류평가|서류", "서류"),
    # 면접
    (r"면접", "면접"),
    # 논술
    (r"논술", "논술"),
    # 실기 / 실적
    (r"실기", "실기"),
    (r"실적|경기실적|입상실적", "실적"),
    # 수능
    (r"대학수학능력시험|수능", "수능"),
    # 출결
    (r"출결", "출결"),
    # 봉사
    (r"봉사", "봉사"),
    # 1단계 성적 (in 2단계)
    (r"1단계\s*(?:성적|평가)?", "1단계"),
    # 체력검정
    (r"체력검정", "체력검정"),
]

_COMPONENT_PATTERN = re.compile(
    r"(" + "|".join(p for p, _ in _COMPONENT_MAP) + r")"
)


def _normalize_component(text: str) -> str:
    text = text.strip()
    for pattern, label in _COMPONENT_MAP:
        if re.search(pattern, text):
            return label
    return text  # fallback: return as-is


# ── Single-stage parser ────────────────────────────────────────────────────────

_PCT_PATTERN = re.compile(
    r"([\w\s·\(\)/]+?)\s+(\d+(?:\.\d+)?)\s*%"
)

_배수_PATTERN = re.compile(r"\((\d+)배수\)")


def _parse_simple(text: str) -> dict | None:
    """Parse flat ratio text like '교과 100%' or '서류 60% + 면접 40%'.

    Returns dict of {component: pct} with total roughly 100.
    Returns None if no percentages found.
    """
    text = re.sub(r"[·•◦]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    matches = _PCT_PATTERN.findall(text)
    if not matches:
        return None

    result: dict[str, int | float] = {}
    for label_raw, pct_str in matches:
        label = _normalize_component(label_raw.strip())
        pct = float(pct_str)
        if pct <= 0 or pct > 100:
            continue
        if label in result:
            # Accumulate (e.g. "교과 19% + 출결 4.8%" both under 학생부)
            result[label] = result[label] + pct
        else:
            result[label] = round(pct, 1) if pct != int(pct) else int(pct)

    배수 = _배수_PATTERN.search(text)
    if 배수 and result:
        result["배수"] = int(배수.group(1))

    return result if result else None


# ── Multi-stage parser ─────────────────────────────────────────────────────────

_STAGE_SPLIT = re.compile(r"([1-9]단계)\s*[:：]\s*")


def parse_전형요소(text: str) -> dict | None:
    """Parse 전형요소 반영비율 from 전형방법 text.

    Returns:
        None  — no percentage info found / unparseable
        {"교과": 100}  — single-stage
        {"1단계": {"교과": 100, "배수": 7}, "2단계": {"1단계": 50, "면접": 50}}
    """
    if not text:
        return None

    text = text.strip()

    # Skip obviously non-ratio text
    if text in ("일 괄", "일괄 합산", "다단계", "일괄합산"):
        return None

    # Check for multi-stage ("1단계 ... 2단계 ...")
    splits = _STAGE_SPLIT.split(text)
    # splits will be: [prefix, "1단계", content1, "2단계", content2, ...]
    if len(splits) >= 4:
        stages: dict = {}
        i = 1
        while i < len(splits) - 1:
            stage_label = splits[i]  # e.g. "1단계"
            stage_content = splits[i + 1]
            parsed = _parse_simple(stage_content)
            if parsed:
                stages[stage_label] = parsed
            i += 2
        return stages if stages else None

    # Single-stage
    return _parse_simple(text)


# ── Batch extraction ───────────────────────────────────────────────────────────

def extract_전형요소_from_content(content: str) -> str | None:
    """Extract 전형방법 line from structured content header.

    The structured header looks like:
        대학: XXX
        ...
        전형방법: ·학생부교과 100%
        ...
    Returns the 전형방법 value string, or None.
    """
    if not content:
        return None
    # Only look in structured header (before === separator)
    sep = content.find("===")
    header = content[:sep] if sep > 0 else content[:600]
    m = re.search(r"전형방법:\s*([^\n]+)", header)
    return m.group(1).strip() if m else None
