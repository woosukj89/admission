"""Parse 수능최저학력기준 (CSAT minimum score requirements) from 모집요강 content.

Usage:
    from src.parse_suneung_min import parse_수능최저

    result = parse_수능최저(content_text)
    # Returns None if no 수능최저 info found
    # Returns {"있음": False} if 미적용
    # Returns {"있음": True} if present but no specific criteria
    # Returns {"있음": True, "조건": {...}, "원문": "..."} if parsed
"""

from __future__ import annotations

import re


# ── Subject normalization ─────────────────────────────────────────────────────

_SUBJECT_MAP = {
    "국어": "국어", "국": "국어",
    "수학": "수학", "수": "수학",
    "영어": "영어", "영": "영어",
    "탐구": "탐구", "탐": "탐구", "사탐": "탐구", "과탐": "탐구",
    "사회": "탐구", "과학": "탐구",
    "한국사": "한국사",
    "직업탐구": "직업탐구", "직탐": "직업탐구",
    "제2외국어": "제2외국어",
}

_SUBJECT_PATTERN = (
    r"(?:국어|국수영탐|국수영|국수|국|수학|수|영어|영|탐구|탐|사탐|과탐|사회|과학|한국사|직탐|직업탐구)"
)


def _extract_subjects(text: str) -> list[str] | None:
    """Extract subject list from a text window preceding the 합/등급 condition."""
    found = re.findall(_SUBJECT_PATTERN, text)
    subjects = []
    seen: set[str] = set()
    for s in found:
        norm = _SUBJECT_MAP.get(s, s)
        if norm not in seen:
            seen.add(norm)
            subjects.append(norm)
    return subjects if subjects else None


# ── Main Parser ───────────────────────────────────────────────────────────────

def parse_수능최저(content: str) -> dict | None:
    """Parse 수능최저학력기준 from admission_process content.

    Returns:
        None  — no 수능최저 info found in content
        {"있음": False}  — 수능최저 미적용
        {"있음": True}  — applies but specific criteria not parseable
        {"있음": True, "조건": {...}, "원문": str}  — fully parsed
    """
    if not content:
        return None

    # ── Step 1: check structured section (before ===) ─────────────────────────
    sep_idx = content.find("===")
    struct = content[:sep_idx] if sep_idx > 0 else content[:800]

    # 비고 field detection
    bibiko_m = re.search(r"비고\s*:?\s*([^\n]{0,100})", struct)
    bibiko_text = bibiko_m.group(1).strip() if bibiko_m else ""

    # Check for 없음/미적용 in 비고
    if re.search(r"수능최저.{0,15}없음|최저학력기준.{0,10}없음|최저.{0,5}미적용", bibiko_text):
        return {"있음": False}

    # 없음 anywhere in full content (reliable pattern)
    if re.search(r"수능최저학력기준\s*(없음|미적용)|최저학력기준\s*(없음|미적용)", content):
        return {"있음": False}

    # ── Step 2: check for presence ────────────────────────────────────────────
    has_suneung = bool(
        re.search(r"수능최저|최저학력기준", content)
    )
    if not has_suneung:
        return None

    # ── Step 3: try to parse specific criteria ────────────────────────────────
    conditions: list[dict] = []

    # Pattern A: "N개 합 M이내" / "N개합M" / "N개 영역의 합이 M이내"
    # e.g. "국수영탐 중 2개 합 6 이내", "2개 등급 합 5", "합이 8등급 이내"
    for m in re.finditer(
        r"(\d)\s*개.{0,20}합이?\s*(\d+)\s*(?:등급\s*)?(?:이내|이하)?",
        content,
    ):
        n_subj = int(m.group(1))
        threshold = int(m.group(2))
        if n_subj < 1 or n_subj > 4 or threshold < 2 or threshold > 20:
            continue
        window = content[max(0, m.start() - 80): m.start()]
        subjects = _extract_subjects(window)
        cond: dict = {"방식": "합", "개수": n_subj, "기준": threshold}
        if subjects:
            cond["과목"] = subjects
        raw_text = content[max(0, m.start() - 20): m.end() + 30].strip()
        cond["원문"] = re.sub(r"\s+", " ", raw_text)
        conditions.append(cond)

    # Pattern B: "N개 각 M등급 이상/이내" / "N개 M등급 이상"
    for m in re.finditer(
        r"(\d)\s*개.{0,15}(?:각\s*)?(\d)\s*등급\s*(이상|이내|이하)",
        content,
    ):
        n_subj = int(m.group(1))
        threshold = int(m.group(2))
        direction = m.group(3)
        if n_subj < 1 or n_subj > 4 or threshold < 1 or threshold > 9:
            continue
        window = content[max(0, m.start() - 80): m.start()]
        subjects = _extract_subjects(window)
        cond: dict = {"방식": direction, "개수": n_subj, "기준": threshold}
        if subjects:
            cond["과목"] = subjects
        raw_text = content[max(0, m.start() - 20): m.end() + 30].strip()
        cond["원문"] = re.sub(r"\s+", " ", raw_text)
        conditions.append(cond)

    # Pattern D: Fragmented table format — "개 영역 합 \n이내\nN\nM"
    # PDF table cells extracted per-line: "개 영역 등급 합 \n이내\n3\n7" → 3개 합 7이내
    for m in re.finditer(
        r"개\s*영역[^\n]{0,20}합\s*\n\s*이내\s*\n\s*(\d+)\s*\n\s*(\d+)",
        content,
    ):
        n_subj = int(m.group(1))
        threshold = int(m.group(2))
        if n_subj < 1 or n_subj > 4 or threshold < 2 or threshold > 20:
            continue
        window = content[max(0, m.start() - 80): m.start()]
        subjects = _extract_subjects(window)
        cond: dict = {"방식": "합", "개수": n_subj, "기준": threshold}
        if subjects:
            cond["과목"] = subjects
        raw_text = content[max(0, m.start() - 20): m.end() + 30].strip()
        cond["원문"] = re.sub(r"\s+", " ", raw_text)
        conditions.append(cond)

    # Pattern E: Fragmented "N\n개 영역 각 등급 이내\nM" format
    # e.g. "4\n개 영역 각 등급 이내한국사 등급 이내\n3" → 4개 영역 각 3등급 이내
    for m in re.finditer(
        r"(\d)\s*\n개\s*영역\s*각\s*등급\s*이내[^\n]*\n\s*(\d)",
        content,
    ):
        n_subj = int(m.group(1))
        threshold = int(m.group(2))
        if n_subj < 1 or n_subj > 4 or threshold < 1 or threshold > 9:
            continue
        window = content[max(0, m.start() - 80): m.start()]
        subjects = _extract_subjects(window)
        cond: dict = {"방식": "이내", "개수": n_subj, "기준": threshold}
        if subjects:
            cond["과목"] = subjects
        raw_text = content[max(0, m.start()): m.end() + 30].strip()
        cond["원문"] = re.sub(r"\s+", " ", raw_text)
        conditions.append(cond)

    # Pattern F: Fragmented "한국사 등급 이내\nN" format
    for m in re.finditer(
        r"한국사\s*등급\s*이내\s*\n\s*(\d)",
        content,
    ):
        threshold = int(m.group(1))
        if threshold < 1 or threshold > 9:
            continue
        cond: dict = {"방식": "필수", "과목": ["한국사"], "기준": threshold}
        raw_text = content[m.start(): m.end() + 10].strip()
        cond["원문"] = re.sub(r"\s+", " ", raw_text)
        conditions.append(cond)

    # Pattern C: special "영어 N등급 이상" / "한국사 N등급"
    for subj, subj_name in (("영어", "영어"), ("한국사", "한국사")):
        for m in re.finditer(
            rf"{subj}\s+(\d)\s*등급\s*(이상|이내|이하)?",
            content,
        ):
            threshold = int(m.group(1))
            if threshold < 1 or threshold > 9:
                continue
            cond: dict = {"방식": "필수", "과목": [subj_name], "기준": threshold}
            raw_text = content[m.start(): m.end() + 20].strip()
            cond["원문"] = re.sub(r"\s+", " ", raw_text)
            conditions.append(cond)

    if conditions:
        # Deduplicate by (방식, 개수, 기준) — same condition may appear multiple times in content
        seen_keys: set[tuple] = set()
        deduped = []
        for c in conditions:
            key = (c.get("방식"), c.get("개수"), c.get("기준"))
            if key not in seen_keys:
                seen_keys.add(key)
                deduped.append(c)
        primary = deduped[0]
        result: dict = {
            "있음": True,
            "조건": {k: v for k, v in primary.items() if k != "원문"},
            "원문": primary.get("원문", ""),
        }
        if len(deduped) > 1:
            result["추가조건"] = deduped[1:]
        return result

    # ── Presence confirmed but criteria not parseable ─────────────────────────
    if bibiko_text and "수능최저" in bibiko_text and "적용" in bibiko_text:
        return {"있음": True, "원문": re.sub(r"\s+", " ", bibiko_text[:120])}

    # Raw text confirms presence — save the snippet that triggered detection
    m_pres = re.search(r"수능최저.{0,10}적용|최저학력기준.{0,10}적용", content)
    if m_pres:
        snippet = content[max(0, m_pres.start() - 20): m_pres.end() + 60].strip()
        return {"있음": True, "원문": re.sub(r"\s+", " ", snippet[:120])}

    return None


# ── 수능최저 요약 텍스트 생성 (H1) ─────────────────────────────────────────────

def summarize_수능최저(수능최저: dict) -> str:
    """Convert parsed 수능최저 dict to a human-readable summary string.

    Examples:
        {"있음": False}                                  → "수능최저 없음"
        {"있음": True}                                   → "수능최저 있음 (기준 미파싱)"
        {"있음": True, "조건": {"방식":"합","개수":4,"기준":8}}  → "4개 합 8이내"
        {"있음": True, "조건": {"방식":"이상","개수":3,"기준":3,"과목":["국어","수학","영어"]}}
                                                         → "국어+수학+영어 3개 각 3등급 이상"
        {"있음": True, "조건": {"방식":"필수","과목":["영어"],"기준":2}} → "영어 2등급 이상 필수"
    """
    if not 수능최저:
        return "수능최저 없음"
    if not 수능최저.get("있음"):
        return "수능최저 없음"

    조건 = 수능최저.get("조건")
    if not 조건:
        return "수능최저 있음 (기준 미파싱)"

    방식 = 조건.get("방식", "합")
    기준 = 조건.get("기준")
    개수 = 조건.get("개수")
    과목 = 조건.get("과목") or []
    과목_str = "+".join(과목) if 과목 else ""

    if 방식 == "합":
        parts: list[str] = []
        if 과목_str:
            parts.append(f"{과목_str} 중")
        if 개수:
            parts.append(f"{개수}개")
        parts.append(f"합 {기준}이내")
        return " ".join(parts)

    if 방식 in ("이상", "이내"):
        parts = []
        if 과목_str:
            parts.append(과목_str)
        if 개수 and 개수 > 1:
            parts.append(f"{개수}개 각")
        parts.append(f"{기준}등급 {방식}")
        return " ".join(parts)

    if 방식 == "필수":
        subj = 과목_str if 과목_str else "지정과목"
        return f"{subj} {기준}등급 이상 필수"

    return f"{방식} {기준}"


# ── 수능최저 충족 여부 체크 ────────────────────────────────────────────────────

# Canonical subject names understood by the checker
# Student can pass any of these keys; normalization maps aliases
_STUDENT_SUBJECT_ALIASES: dict[str, str] = {
    "국어": "국어", "국": "국어",
    "수학": "수학", "수": "수학",
    "영어": "영어", "영": "영어",
    "탐구": "탐구", "사탐": "탐구", "과탐": "탐구",
    "사회": "탐구", "과학": "탐구",
    "한국사": "한국사",
    "직업탐구": "직업탐구",
    "제2외국어": "제2외국어",
}


def _normalize_student_grades(raw: dict[str, int | float]) -> dict[str, float]:
    """Normalize student grade dict keys to canonical subject names."""
    out: dict[str, float] = {}
    for k, v in raw.items():
        norm = _STUDENT_SUBJECT_ALIASES.get(k, k)
        # Keep the better (lower) grade if the same canonical subject appears twice
        if norm not in out or v < out[norm]:
            out[norm] = float(v)
    return out


def _best_n_sum(grades: dict[str, float], subjects: list[str] | None, n: int) -> float:
    """Return the sum of the best (lowest) N grades from the allowed subjects.

    If subjects list is empty/None, all provided grades are candidates.
    탐구 counts once even if student provided two탐 grades separately.
    """
    if subjects:
        # Filter to only subjects in the allowed list
        candidates = [grades[s] for s in subjects if s in grades]
    else:
        candidates = list(grades.values())

    if not candidates:
        return float("inf")  # cannot evaluate — assume not satisfiable

    # Sort ascending (lower grade = better rank)
    candidates.sort()
    return sum(candidates[:n])


def check_수능최저(
    student_grades: dict[str, int | float],
    수능최저: dict,
) -> dict:
    """Check whether a student's 수능 grades satisfy a 수능최저 condition.

    Args:
        student_grades: Dict of subject→grade, e.g.
            {"국어": 3, "수학": 2, "영어": 1, "탐구": 4, "한국사": 3}.
            Keys may use aliases (수=수학, 영=영어, 사탐/과탐=탐구 etc.).
        수능최저: Parsed 수능최저 dict as returned by parse_수능최저().
            Expected keys: "있음", optionally "조건" with "방식"/"개수"/"기준"/"과목".

    Returns:
        {
          "충족": bool,
          "설명": str,          # human-readable explanation
          "달성": float | None, # student's best-N sum or best grade achieved
          "기준": float | None, # required threshold
        }
    """
    if not 수능최저 or not 수능최저.get("있음"):
        return {"충족": True, "설명": "수능최저 없음"}

    조건 = 수능최저.get("조건")
    if not 조건:
        # 수능최저 있음 but no parseable criteria — assume applies
        return {
            "충족": None,
            "설명": "수능최저 있음 (구체적 기준 미파싱 — 학교 요강 직접 확인 필요)",
        }

    grades = _normalize_student_grades(student_grades)
    방식 = 조건.get("방식", "합")
    기준 = 조건.get("기준")
    개수 = 조건.get("개수")
    과목 = 조건.get("과목")  # list of canonical subject names or None

    if 기준 is None:
        return {"충족": None, "설명": "기준 등급/점수 정보 없음"}

    # ── "합" method: sum of best N subjects ──────────────────────────────────
    if 방식 == "합":
        n = 개수 if 개수 else len(grades)
        actual_sum = _best_n_sum(grades, 과목, n)
        충족 = actual_sum <= 기준

        subj_str = "+".join(과목) if 과목 else "전체"
        설명 = (
            f"{subj_str} 중 상위 {n}개 합 {actual_sum:.0f} "
            f"{'≤' if 충족 else '>'} 기준 {기준} → "
            f"{'✅ 충족' if 충족 else '❌ 미충족'}"
        )
        return {"충족": 충족, "설명": 설명, "달성": actual_sum, "기준": float(기준)}

    # ── "이상"/"이내" method: each of N subjects must meet grade threshold ───
    if 방식 in ("이상", "이내"):
        n = 개수 if 개수 else 1
        if 과목:
            candidates = sorted([grades[s] for s in 과목 if s in grades])
        else:
            candidates = sorted(grades.values())

        top_n = candidates[:n]
        if len(top_n) < n:
            return {
                "충족": None,
                "설명": f"성적 정보 부족 ({n}개 과목 필요, {len(top_n)}개 입력됨)",
            }

        # 등급은 낮을수록 좋으므로 "이상" = ≤ threshold
        all_ok = all(g <= 기준 for g in top_n)
        worst = max(top_n)
        subj_str = "+".join(과목) if 과목 else "제공 과목"
        설명 = (
            f"{subj_str} 중 {n}개 각 {기준}등급 이상 — "
            f"최저 {worst}등급 → {'✅ 충족' if all_ok else '❌ 미충족'}"
        )
        return {"충족": all_ok, "설명": 설명, "달성": worst, "기준": float(기준)}

    # ── "필수" method: specific subject must meet grade ───────────────────────
    if 방식 == "필수":
        subj_list = 과목 if 과목 else []
        results = []
        for subj in subj_list:
            grade = grades.get(subj)
            if grade is None:
                results.append((subj, None, None))
            else:
                results.append((subj, grade, grade <= 기준))

        if not results:
            return {"충족": None, "설명": "필수 과목 정보 없음"}

        all_ok = all(ok for _, _, ok in results if ok is not None)
        parts = [
            f"{s} {g}등급 {'✅' if ok else '❌'}"
            for s, g, ok in results if g is not None
        ]
        설명 = f"필수: {기준}등급 이상 — {', '.join(parts)}"
        return {"충족": all_ok, "설명": 설명, "달성": None, "기준": float(기준)}

    return {"충족": None, "설명": f"알 수 없는 방식: {방식}"}
