"""MCP server for Korean university admission data querying.

Exposes 5 tools for querying admission programs, results, and university metadata.
Run with: python src/mcp_server.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Add project root to sys.path so src.* imports work
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.storage.admission_store import AdmissionStore  # noqa: E402
from src.parse_suneung_min import check_수능최저, summarize_수능최저  # noqa: E402

mcp = FastMCP("admission-db")

# ── Startup: load metadata and store ─────────────────────────────────────────

META_PATH = PROJECT_ROOT / "data" / "university_meta.json"
_UNI_META: dict[str, dict] = {}
if META_PATH.exists():
    with open(META_PATH, encoding="utf-8") as _f:
        _UNI_META = json.load(_f)

# E3: 수능 표준점수 ↔ 등급 conversion table
_GRADE_TABLE_PATH = PROJECT_ROOT / "data" / "suneung_grade_table.json"
_GRADE_TABLE: dict[str, dict] = {}
if _GRADE_TABLE_PATH.exists():
    with open(_GRADE_TABLE_PATH, encoding="utf-8") as _f:
        _GRADE_TABLE = json.load(_f)

_store = AdmissionStore(db_path=PROJECT_ROOT / "data" / "admission.db")

# ── Department category expansion ────────────────────────────────────────────

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "IT/컴퓨터": ["컴퓨터", "소프트웨어", "IT", "정보통신", "데이터", "AI",
                  "인공지능", "정보보안", "전산", "디지털", "사이버", "게임",
                  "빅데이터", "SW", "정보과학"],
    "전기/전자": ["전기", "전자", "반도체", "제어", "통신공학"],
    "기계/로봇": ["기계", "로봇", "항공", "자동차", "산업공학", "모빌리티"],
    "건축/토목": ["건축", "토목", "도시", "건설", "조경"],
    "화학/재료": ["화학", "재료", "신소재", "환경", "에너지", "생명공학"],
    "의약학":    ["의학", "의예", "한의", "간호", "약학", "치의", "수의",
                  "보건", "의료", "임상"],
    "자연과학":  ["물리", "수학", "생물", "통계", "천문", "지구", "생명과학"],
    "인문":      ["국어", "영어", "중어", "일어", "불어", "독어", "철학",
                  "역사", "문학", "언어"],
    "사회/경영": ["경영", "경제", "회계", "무역", "행정", "정치", "사회학",
                  "법학", "금융", "마케팅"],
    "교육":      ["교육", "유아교육", "초등교육", "사범"],
    "예체능":    ["미술", "음악", "체육", "스포츠", "디자인", "무용",
                  "연기", "영화", "패션"],
}

# D5: Cross-category synonym expansion for common search terms
SYNONYMS: dict[str, list[str]] = {
    "컴퓨터공학":  ["컴퓨터", "소프트웨어", "정보공학", "전산", "IT공학",
                   "인공지능", "AI", "빅데이터", "SW", "데이터사이언스",
                   "정보통신", "사이버", "융합소프트웨어"],
    "소프트웨어":  ["소프트웨어", "SW", "컴퓨터", "정보공학", "AI", "인공지능"],
    "인공지능":    ["인공지능", "AI", "데이터", "머신러닝", "딥러닝",
                   "빅데이터", "데이터사이언스", "소프트웨어"],
    "전자공학":    ["전자", "전기전자", "전기", "반도체"],
    "기계공학":    ["기계", "기계공", "자동차", "항공"],
    "화학공학":    ["화공", "화학공학", "화학", "신소재", "재료"],
    "토목공학":    ["토목", "건설", "건축토목"],
    "경영학":      ["경영", "경영학", "비즈니스", "경영정보"],
    "경제학":      ["경제", "경제학", "금융경제"],
    "법학":        ["법학", "법", "법과"],
    "의예과":      ["의학", "의예", "의과"],
    "간호학":      ["간호", "간호학"],
    "약학":        ["약학", "약대", "제약"],
    "국어국문":    ["국어", "국문", "한국어", "한국문학"],
    "영어영문":    ["영어", "영문", "영어영문"],
    "역사학":      ["역사", "사학", "역사교육"],
    "동물":        ["동물", "동물자원", "동물생명", "축산", "수의", "반려동물",
                   "동물보건", "야생동물", "동물응용"],
}


def _expand_keywords(keywords: list[str]) -> list[str]:
    """Expand category names and synonyms to individual sub-keywords.

    Also does reverse lookup: if a keyword is a member of a CATEGORY,
    expand to all members of that category.
    """
    # Build reverse index: sub-keyword → category keywords list
    _reverse: dict[str, list[str]] = {}
    for cat_subs in CATEGORY_KEYWORDS.values():
        for sub in cat_subs:
            _reverse.setdefault(sub, []).extend(cat_subs)

    expanded: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        if kw in CATEGORY_KEYWORDS:
            for sub in CATEGORY_KEYWORDS[kw]:
                if sub not in seen:
                    seen.add(sub)
                    expanded.append(sub)
        elif kw in SYNONYMS:
            for sub in SYNONYMS[kw]:
                if sub not in seen:
                    seen.add(sub)
                    expanded.append(sub)
        elif kw in _reverse:
            # Reverse lookup: kw is a category member — expand to full category
            for sub in _reverse[kw]:
                if sub not in seen:
                    seen.add(sub)
                    expanded.append(sub)
        else:
            if kw not in seen:
                seen.add(kw)
                expanded.append(kw)
    return expanded


def _in_region(uni: str, region: str) -> bool:
    meta = _UNI_META.get(uni, {})
    return region in (meta.get("region", ""), meta.get("region_broad", ""))


def _uni_meta(uni: str) -> dict:
    return _UNI_META.get(uni, {"region": "", "region_broad": "", "tier": 5})


# C2: region-restricted 전형 detection
# 지역균형: mostly for local-region high school graduates (서울 고교 출신 지원 가능하나
#           타 지역 대학 지역균형은 해당 지역 고교 출신만 가능)
# 지역인재/지방인재: explicitly restricted to local-region students
_REGIONAL_MARKERS = ("지역인재", "지역균형", "지방인재")


# D4: 계열 (track) classification and filtering
_SCIENCE_KEYWORDS = frozenset([
    "공학", "과학", "수학", "물리", "화학", "생물", "컴퓨터", "전기", "전자",
    "기계", "반도체", "소프트웨어", "건축", "토목", "환경", "재료", "화공",
    "에너지", "로봇", "항공", "자동차", "산업", "정보통신", "IT", "AI",
    "데이터", "인공지능", "정보보안", "디지털", "SW",
])
_HUMANITIES_KEYWORDS = frozenset([
    "국어", "영어", "중어", "일어", "불어", "독어", "어문", "언어", "문학",
    "역사", "철학", "사학", "윤리", "문화", "심리", "사회", "정치",
    "경제", "경영", "행정", "법학", "법", "무역", "회계", "금융",
])
_MEDICAL_KEYWORDS = frozenset([
    "의학", "의예", "한의", "간호", "약학", "치의", "수의", "보건", "의료",
    "임상", "의대", "의공학", "방사선", "물리치료", "작업치료",
])
_ARTS_KEYWORDS = frozenset([
    "미술", "음악", "체육", "스포츠", "디자인", "무용", "연기", "영화", "패션",
    "공예", "사진", "애니메이션", "예술", "연극",
])

_TRACK_CANONICAL: dict[str, str] = {
    "자연": "자연", "과학": "자연", "공학": "자연", "자연계": "자연",
    "자연 과학": "자연", "이공계": "자연", "과학기술": "자연",
    "인문": "인문", "인문사회": "인문", "사회": "인문", "인문계": "인문",
    "인문 사회": "인문",
    "예체능": "예체능", "예능": "예체능", "체능": "예체능", "예체": "예체능",
    "예체능계": "예체능",
    "의약학": "의약학", "의학": "의약학",
}


def _classify_track(track_raw: str, dept_name: str) -> str:
    """Normalize dept track value to one of: 자연/인문/예체능/의약학/기타."""
    # Collapse spaced Korean text
    import re as _re
    t = _re.sub(r"\s+", " ", (track_raw or "").strip()).lower()
    # Remove trailing numbers/symbols
    t = _re.sub(r"[\s\d]+$", "", t).strip()

    # Direct mapping
    canonical = _TRACK_CANONICAL.get(track_raw.strip() if track_raw else "", "")
    if canonical:
        return canonical
    for k, v in _TRACK_CANONICAL.items():
        if k in t:
            return v

    # Fall back to dept_name keywords
    name = dept_name or ""
    if any(kw in name for kw in _MEDICAL_KEYWORDS):
        return "의약학"
    if any(kw in name for kw in _ARTS_KEYWORDS):
        return "예체능"

    # Strip "문화학" before science-keyword matching to avoid false positives:
    # "중국문화학과" contains "화학" as a coincidental substring of "문화학과".
    # After stripping, "중국문화학과" → "중국과", so "화학" no longer matches.
    name_for_sci = name.replace("문화학", "")
    has_science = any(kw in name_for_sci for kw in _SCIENCE_KEYWORDS)
    has_humanities = any(kw in name for kw in _HUMANITIES_KEYWORDS)

    if has_science and has_humanities:
        # Ambiguous: e.g. "물리교육과" (물리=science, 교육=humanities).
        # Strong unambiguous science keywords override humanities.
        _STRONG_SCIENCE = frozenset([
            "공학", "전자", "전기", "기계", "반도체", "항공", "로봇",
            "소프트웨어", "컴퓨터", "물리", "수학", "생물", "화학", "지구과학",
        ])
        if any(kw in name_for_sci for kw in _STRONG_SCIENCE):
            return "자연"
        return "인문"
    if has_science:
        return "자연"
    if has_humanities:
        return "인문"
    return "기타"


def _matches_track(dept: dict, track_filter: str) -> bool:
    """Return True if the dept matches the requested track."""
    classified = _classify_track(dept.get("track") or "", dept.get("name") or "")
    return classified == track_filter


def _is_regional(process_name: str) -> bool:
    """Return True if the process is restricted to local-region students."""
    return any(m in process_name for m in _REGIONAL_MARKERS)


_SPECIAL_SUBSTRINGS = (
    "기회균형", "고른기회", "사회통합", "사회배려", "경제배려",
    "농어촌", "특성화고", "특성화 고", "직업계고", "마이스터고",
    "특성화",          # catches "Ⅲ특성화", "특성화(고교졸업자)" etc.
    "재직자", "만학도", "성인학습자", "자립지원",
    "특수교육대상자", "장애인",
    "다문화", "북한이탈", "탈북",
    "국가보훈", "보훈",
    "저소득", "차상위", "기초생활",
    "정원외",          # 정원외 특별전형: always restricted-access
    "재외국민",        # 재외국민전형: restricted to overseas Koreans
)

_SPECIAL_WHITELIST = (
    "기회의 균형", "농어촌테마", "농업생명", "농식품",
    "기초의학", "기초과학", "기초교육",
    "특성화대학", "특성화학부", "특성화전공", "특성화사업",  # not 특성화고 전형
)


def _is_valid_dept_name(name: str | None) -> bool:
    """F2: Filter out OCR artifacts and garbage department/process names.

    Returns False for names that are clearly not real Korean academic names:
    - Short column-header words (경쟁률, 예비순위, 최고, 최저, 정원 등)
    - Contains isolated Hangul jamo (ㄱ-ㅎ, ㅏ-ㅣ)
    - Numeric-only or single char
    - Longer names with no valid Korean academic bigram (OCR garble detection)
    """
    if not name:
        return False
    name = name.strip()
    # A3: strip trailing OCR artifact characters (*, †, ‡, ·, footnote digits like "3)")
    name = re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', name).strip()
    if len(name) < 2:
        return False

    # Explicit garbage list
    _GARBAGE = frozenset(["경쟁률", "예비순위", "최고", "최저", "정원(명)", "폐지",
                           "2명", "모집인원", "합격자", "충원합격", "입학인원"])
    if name in _GARBAGE:
        return False

    # Contains isolated Hangul jamo (not part of syllable blocks)
    # Hangul jamo range: ㄱ-ㅎ U+3131-U+314E, ㅏ-ㅣ U+314F-U+3163
    if any('\u3131' <= ch <= '\u3163' for ch in name):
        return False

    # Purely numeric or very short with digits
    if name.replace(",", "").replace(".", "").replace(" ", "").isdigit():
        return False

    # A4: bigram-based OCR garble detection for longer names.
    # Real Korean academic names always contain at least one valid academic bigram.
    # OCR-garbled names (e.g. "거머르트국저스무", "급도더인국릭부") have none.
    # A3 fix: apply to names >= 3 chars (was >= 4, missing 3-char garbage like "화아가").
    _name_no_bracket = name.split("[")[0].split("(")[0].strip()
    if len(_name_no_bracket) >= 3 and " " not in _name_no_bracket:
        _VALID_BIGRAMS = frozenset([
            # Department/major terms
            "학과", "학부", "공학", "경영", "교육", "국어", "영어", "전자", "전기",
            "기계", "소프", "컴퓨", "정보", "수학", "물리", "화학", "생명", "환경",
            "에너", "건축", "토목", "도시", "경제", "법학", "행정", "사회", "심리",
            "역사", "문학", "언론", "미디", "디자", "체육", "예술", "음악", "미술",
            "식품", "간호", "의학", "약학", "치의", "수의", "보건", "의료", "임상",
            "생물", "지구", "지질", "천체", "물리", "화학", "수학", "전산", "정보",
            "전공", "대학", "과학", "자연", "인문", "해양", "항공", "자동", "로봇",
            "반도", "신소", "재료", "섬유", "인공", "데이", "빅데", "통신", "사이",
            "보안", "융합", "국제", "글로", "철학", "스포", "관광", "호텔", "조경",
            "산림", "농업", "축산", "수산", "원예", "세무", "금융", "마케", "무역",
            "회계", "통계", "지리", "지구", "천문", "한국", "중국", "일본", "불어",
            "독어", "서어", "러시", "아랍", "베트", "인도", "현대", "고전", "근대",
            "고고", "민속", "문화", "복지", "치료", "재활", "특수", "유아", "초등",
            "청소", "노인", "여성", "가족", "아동", "상담", "사범", "조리", "제과",
            "영양", "한식", "패션", "의류", "뷰티", "헤어", "AI학", "SW학",
            # Process/전형 name terms (A4 fix: these were missing, causing valid 전형 names to be filtered)
            "전형", "학생", "교과", "종합", "논술", "실기", "수능", "일반", "서류",
            "면접", "지역", "균형", "인재", "기회", "특기", "재외", "편입", "정시",
            "수시", "기숙", "프런", "플러", "미래", "창의", "핵심", "탐구", "자기",
            "추천", "우수", "석좌", "장학", "첨단", "혁신", "글꿈",
        ])
        bigrams = {_name_no_bracket[i:i+2] for i in range(len(_name_no_bracket) - 1)}
        if not bigrams & _VALID_BIGRAMS:
            return False

        # A3: for short names (3–5 chars), also require a valid academic terminal suffix.
        # This catches OCR garbage like "경제하가" which coincidentally has "경제" bigram.
        # Real short names always end with 과/부/학/전/원/교/계/대/형/력/학교/과정/전공/전형 etc.
        _VALID_SUFFIXES = (
            "과", "부", "학", "전", "원", "교", "대", "계", "형", "력", "류", "과학",
            "전공", "전형", "학과", "학부", "대학", "교육", "공학", "과정", "계열",
        )
        if len(_name_no_bracket) <= 5 and not any(_name_no_bracket.endswith(s) for s in _VALID_SUFFIXES):
            return False

    return True


def _enrich_수능최저(sm: dict, student_suneung: dict | None = None) -> dict:
    """H1: Add 원문_요약 and optional 충족 check to a 수능최저 dict.

    Returns a copy of sm with:
    - "원문_요약": human-readable summary string
    - "충족" / "충족_설명": if student_suneung provided
    """
    if not sm:
        return sm
    out = dict(sm)
    out["원문_요약"] = summarize_수능최저(sm)
    if student_suneung and sm.get("있음"):
        check = check_수능최저(student_suneung, sm)
        out["충족"] = check.get("충족")
        out["충족_설명"] = check.get("설명")
    return out


def _is_special_admission(attrs_or_name) -> bool:
    """Return True if this is a restricted-access special 전형 (F1).

    Accepts either an attributes dict (checks 특수전형 tag) or a process_name string.
    Falls back to substring matching when tag absent (covers admission_result rows).
    """
    if isinstance(attrs_or_name, dict):
        # admission_process.attributes: trust the tag
        if attrs_or_name.get("특수전형"):
            return True
        # admission_result.attributes won't have the tag — need process_name
        return False
    # String: direct process_name check
    name: str = attrs_or_name or ""
    for wl in _SPECIAL_WHITELIST:
        if wl in name:
            return False
    return any(sub in name for sub in _SPECIAL_SUBSTRINGS)


# D8: Bulk lookup 수능최저 from admission_process
def _lookup_suneung_min_bulk(
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], dict]:
    """Return {(university, process_name): 수능최저_dict} for the given pairs.

    Matches by exact process_name first; falls back to substring matching
    because 입시결과 and 모집요강 may use slightly different process names
    (e.g., result "논술(일반전형)" vs process "논술전형").
    """
    if not pairs:
        return {}
    unis = list({u for u, _ in pairs})
    ph = ",".join("?" * len(unis))
    # {university: [(process_name, 수능최저_dict), ...]}
    by_uni: dict[str, list[tuple[str, dict]]] = {}
    try:
        with _store._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT d.university, p.process_name, p.attributes
                FROM admission_process p
                JOIN admission_department d ON d.id = p.department_id
                WHERE d.university IN ({ph})
                  AND json_extract(p.attributes, '$.수능최저') IS NOT NULL
                """,
                unis,
            ).fetchall()
            for row in rows:
                attrs = json.loads(row[2]) if isinstance(row[2], str) else {}
                sm = attrs.get("수능최저")
                if sm:
                    uni_key = row[0]
                    by_uni.setdefault(uni_key, []).append((row[1], sm))
    except Exception:
        pass

    result: dict[tuple[str, str], dict] = {}
    for uni, pname in pairs:
        candidates = by_uni.get(uni, [])
        if not candidates:
            continue
        # 1. Exact match
        for p_name, sm in candidates:
            if p_name == pname:
                result[(uni, pname)] = sm
                break
        if (uni, pname) in result:
            continue
        # 2. Fuzzy: substring in either direction (handles naming differences)
        for p_name, sm in candidates:
            if p_name in pname or pname in p_name:
                result[(uni, pname)] = sm
                break
    return result


# D3: Acceptance percentile estimator
def _estimate_acceptance_pct(grade: float, cut_50, cut_70, cut_90,
                              score_type: str = "등급") -> float | None:
    """Estimate the admission percentile for a student's grade.

    For 등급 (lower=better): student at cut_70 ≈ 30th percentile (top 70% admitted).
    For 표준점수/백분위/환산점수 (higher=better): inverted logic.
    Returns None if insufficient data.
    """
    if cut_70 is None:
        return None

    if score_type == "등급":
        # Lower grade = better rank
        if grade <= (cut_50 or cut_70):
            return 95.0  # comfortably in top 5%
        if cut_90 is not None and cut_50 is not None:
            if grade <= cut_70:
                span = cut_70 - (cut_50 or cut_70)
                pct = 50 + (cut_70 - grade) / span * 20 if span > 0 else 65.0
                return round(min(95.0, max(50.0, pct)), 1)
            elif grade <= cut_90:
                span = cut_90 - cut_70
                pct = 10 + (cut_90 - grade) / span * 40 if span > 0 else 25.0
                return round(min(50.0, max(5.0, pct)), 1)
            else:
                return 5.0
        # Only cut_70: rough estimate
        margin = cut_70 - grade
        if margin >= 0.5:
            return 85.0
        if margin >= 0:
            return 65.0
        return None  # below cut_70, insufficient data
    else:
        # Higher is better
        if cut_90 is not None and cut_50 is not None and cut_90 > cut_70:
            if grade >= cut_90:
                return 95.0
            if grade >= cut_70:
                span = cut_90 - cut_70
                pct = 70 + (grade - cut_70) / span * 25 if span > 0 else 80.0
                return round(min(95.0, max(65.0, pct)), 1)
            if grade >= (cut_50 or cut_70):
                span = cut_70 - (cut_50 or cut_70)
                pct = 20 + (grade - (cut_50 or cut_70)) / span * 50 if span > 0 else 45.0
                return round(min(65.0, max(10.0, pct)), 1)
            return 5.0
        margin = grade - cut_70
        if margin >= 5:
            return 85.0
        if margin >= 0:
            return 65.0
        return None


# E3: 수능 등급 → 표준점수 range conversion
def grade_to_score_range(
    subject: str, grade: int, year: str | None = None
) -> tuple[int, int] | None:
    """Convert a 수능 등급 to the approximate 표준점수 (or 원점수 for 절대평가) range.

    Args:
        subject: "국어", "수학", "영어", "한국사", "사탐", "과탐"
        grade: 1-9
        year: e.g. "2025" (defaults to most recent available year)

    Returns:
        (min_score, max_score) tuple, or None if not found.
        영어/한국사 returns 원점수 range (절대평가).
    """
    # Use most recent year if not specified
    avail = [k for k in _GRADE_TABLE if k.isdigit()]
    if not avail:
        return None
    yr = year if year in _GRADE_TABLE else max(avail)
    subj_data = _GRADE_TABLE.get(yr, {}).get(subject)
    if not subj_data:
        return None
    entry = subj_data.get(str(grade))
    if isinstance(entry, list) and len(entry) == 2:
        return (entry[0], entry[1])
    return None


# G7: 수능 등급 → 백분위 범위 (top of each grade band — used for 백분위 DB query)
# Source: approximate KICE cumulative percentile boundaries
_GRADE_TO_PCTILE: dict[int, tuple[int, int]] = {
    1: (96, 100),
    2: (89, 95),
    3: (77, 88),
    4: (60, 76),
    5: (40, 59),
    6: (23, 39),
    7: (11, 22),
    8: (4, 10),
    9: (0, 3),
}


def _grade_to_pctile_range(grade: float) -> tuple[int, int]:
    """Return the approximate 백분위 range for a given (possibly fractional) 수능등급."""
    g = round(grade)
    g = max(1, min(9, g))
    return _GRADE_TO_PCTILE.get(g, (0, 100))


def _grade_to_표준점수_sum(grade: float, track: str | None = None) -> float | None:
    """Estimate the sum of 표준점수 across 3 subjects for the given average 수능등급.

    Uses midpoint of 국어 + 수학 + 탐구 표준점수 ranges for 자연계,
    국어 + 수학 + 영어 원점수 for 인문계 (rough estimate).
    Returns None if grade table not available.
    """
    if not _GRADE_TABLE:
        return None
    g = round(grade)
    g = max(1, min(9, g))
    subjects = ["국어", "수학"]
    # Use 탐구 as proxy for 과탐/사탐 (표준점수 approx same as 국어/수학)
    subjects.append("국어")  # proxy for 탐구 (same scale)
    total = 0.0
    for subj in subjects:
        r = grade_to_score_range(subj, g)
        if r is None:
            return None
        total += (r[0] + r[1]) / 2.0
    return round(total, 1)


# ── Tool 1: search_programs ───────────────────────────────────────────────────

@mcp.tool()
def search_programs(
    major_keywords: list[str],
    university: str | None = None,
    region: str | None = None,
    admission_type: str | None = None,
    process_type: str | None = None,
    track: str | None = None,
    exclude_regional: bool = False,
    exclude_special: bool = True,
    limit: int = 50,
) -> list[dict]:
    """Search for university admission programs by major keywords and filters.

    Args:
        major_keywords: Department name keywords (e.g., ["컴퓨터공학"]) or category
                        names (e.g., ["IT/컴퓨터"] expands to all CS-related keywords).
                        Multiple keywords are OR-combined. Pass [] to search all programs
                        (use with university filter).
        university: Optional university name filter (partial match). Use to list all
                    programs at a specific school, e.g., "광운대학교".
        region: Filter by region string. Accepts specific region (e.g., "서울", "경기",
                "부산") or broad region ("수도권", "지방").
        admission_type: "수시" or "정시".
        process_type: One of "학생부교과", "학생부종합", "논술위주", "수능위주",
                      "실기/실적위주", "기타".
        track: Filter by 계열: "자연", "인문", "예체능", "의약학". Uses both dept.track
               field and dept_name keyword matching to handle inconsistent DB values.
        exclude_regional: If True, exclude 지역인재 전형s (restricted to local-region
                          students only). Default False.
        exclude_special: If True (default), exclude restricted-access special 전형s
                         (기회균형, 농어촌, 특성화고, 장애인, 재직자, 만학도 등).
                         Set False to include all 전형s.
        limit: Maximum number of results (default 50).

    Returns:
        List of programs. Each item: university, region, tier (1=SKY … 5=기타),
        campus, department, process_name, process_type, admission_type, quota,
        수능최저 (parsed CSAT requirement when available — {"있음": bool, "조건": {...}}).
    """
    keywords = _expand_keywords(major_keywords)

    dept_map: dict[int, dict] = {}
    if keywords:
        for kw in keywords:
            for dept in _store.find_departments(name=kw):
                dept_map[dept["id"]] = dept
    elif university:
        # No keywords but university specified — fetch all depts for that university
        import sqlite3 as _sqlite3
        with _store._conn() as _conn:
            _rows = _conn.execute(
                "SELECT id, university, campus, year, track, name FROM admission_department WHERE university LIKE ? LIMIT 500",
                (f"%{university}%",)
            ).fetchall()
            for _r in _rows:
                dept_map[_r[0]] = {
                    "id": _r[0], "university": _r[1], "campus": _r[2],
                    "year": _r[3], "track": _r[4], "name": _r[5]
                }

    results: list[dict] = []
    seen: set[int] = set()

    for dept_id, dept in dept_map.items():
        uni = dept["university"]
        if university and university not in uni:
            continue
        if region and not _in_region(uni, region):
            continue
        # D4: filter by 계열/track
        if track and not _matches_track(dept, track):
            continue

        meta = _uni_meta(uni)
        processes = _store.find_processes(
            department_id=dept_id,
            admission_type=admission_type,
            process_type=process_type,
            limit=200,
        )

        for p in processes:
            pid = p["id"]
            if pid in seen:
                continue
            seen.add(pid)
            # F2: skip OCR artifacts / garbage names
            if not _is_valid_dept_name(p.get("department_name")) or not _is_valid_dept_name(p.get("process_name")):
                continue
            p_attrs = p.get("attributes") or {}
            # F1: skip restricted special 전형s
            if exclude_special and _is_special_admission(p_attrs):
                continue
            # C2: skip 지역인재 전형s if requested
            if exclude_regional and _is_regional(p["process_name"]):
                continue
            # D8: include 수능최저 from attributes when available
            entry_sp: dict = {
                "university": uni,
                "region": meta.get("region", ""),
                "tier": meta.get("tier", 5),
                "campus": p.get("campus", ""),
                "department": p["department_name"],
                "process_name": p["process_name"],
                "process_type": p.get("process_type", ""),
                "admission_type": p.get("admission_type", ""),
                "quota": p.get("quota"),
            }
            sm = p_attrs.get("수능최저")
            if sm is not None:
                entry_sp["수능최저"] = _enrich_수능최저(sm)  # H1: adds 원문_요약
            results.append(entry_sp)

    results.sort(key=lambda x: (x["tier"], x["university"], x["department"]))
    return results[:limit]


def _enrich_result(r: dict) -> dict:
    """Build a result entry for get_process_detail with derived fields (B4)."""
    attrs = r.get("attributes") or {}
    quota = attrs.get("모집인원")
    waitlist = attrs.get("충원합격인원")
    rate = r.get("competition_rate")

    # 실질 경쟁률: adjusts for wait-list fills
    # effective_rate = rate * quota / (quota + waitlist)
    실질_경쟁률 = None
    if rate and quota and waitlist:
        try:
            실질_경쟁률 = round(rate * quota / (quota + waitlist), 2)
        except (ZeroDivisionError, TypeError):
            pass

    entry: dict = {
        "result_year": r["result_year"],
        "process_name": r["process_name"],
        "admission_type": r.get("admission_type"),
        "score_type": r.get("score_type"),
        "competition_rate": rate,
        "average_score": r.get("average_score"),
        "cut_50": r.get("cut_50"),
        "cut_70": r.get("cut_70"),
        "cut_80": r.get("cut_80"),
        "cut_90": r.get("cut_90"),
    }
    if waitlist is not None:
        entry["충원합격인원"] = waitlist
    if 실질_경쟁률 is not None:
        entry["실질_경쟁률"] = 실질_경쟁률
    return entry


# ── Tool 2: get_process_detail ────────────────────────────────────────────────

@mcp.tool()
def get_process_detail(
    university: str,
    process_name: str,
    department: str | None = None,
    student_grade: float | None = None,
) -> dict:
    """Get full details of an admission process, including cut scores and competition rates.

    Args:
        university: University name (exact or partial — LIKE match used).
        process_name: Admission process name, e.g., "가천바람개비", "논술전형".
        department: Optional department name to narrow down multiple matches.
        student_grade: Optional student grade (등급: lower=better). When provided,
            returns a required_improvement field showing gap to cut scores and verdict.

    Returns:
        Process details: attributes (includes 수능최저 parsed criteria when available),
        content_preview (first 4000 chars), matched_count (how many processes matched),
        admission_results list with cut_50/cut_70/cut_80/cut_90 scores, competition_rate,
        충원합격인원, 실질_경쟁률 (sorted newest first),
        and optionally required_improvement when student_grade is given.
        Note: attributes["수능최저"] = {"있음": bool, "조건": {...}} when parsed.
    """
    processes = _store.find_processes(university=university, process_name=process_name)
    if department:
        # G2: expand department keyword to synonyms before filtering
        dept_kws = _expand_keywords([department])
        filtered = [
            p for p in processes
            if any(kw in (p.get("department_name") or "") for kw in dept_kws)
        ]
        if filtered:
            processes = filtered

    if not processes:
        # G2: fall back to admission_result data when admission_process has no match
        # (happens when the process table has garbage entries from PDF column headers)
        fallback_results = _store.find_results(
            university=university,
            process_name=process_name,
        )
        if fallback_results and department:
            dept_kws = _expand_keywords([department])
            filtered_fb = [
                r for r in fallback_results
                if any(kw in (r.get("department_name") or "") for kw in dept_kws)
            ]
            if filtered_fb:
                fallback_results = filtered_fb

        if fallback_results:
            # Build a synthetic response from admission_result data only.
            # A5: group by department, show each dept as {department, years: [...], trend}
            fb_meta = _uni_meta(university)

            # Build dept → sorted year list
            dept_map: dict[str, list[dict]] = {}
            for r in fallback_results:
                dept_key = r.get("department_name") or ""
                entry = _enrich_result(r)
                entry["department"] = dept_key
                dept_map.setdefault(dept_key, []).append(entry)

            # Sort years within each dept newest first; compute trend
            dept_groups: list[dict] = []
            for dept_key, yr_entries in dept_map.items():
                yr_entries.sort(key=lambda e: e.get("result_year") or 0, reverse=True)
                # Trend: compare most-recent vs previous year cut_70
                trend_dir: str | None = None
                cuts_with_year = [
                    (e.get("result_year", 0), e.get("cut_70"))
                    for e in yr_entries
                    if e.get("cut_70") is not None
                ]
                cuts_with_year.sort(key=lambda t: t[0])
                if len(cuts_with_year) >= 2:
                    first_cut = cuts_with_year[0][1]
                    last_cut = cuts_with_year[-1][1]
                    diff = last_cut - first_cut  # 등급: positive = easier
                    if diff <= -0.2:
                        trend_dir = "harder"   # cut fell → harder to get in
                    elif diff >= 0.2:
                        trend_dir = "easier"
                    else:
                        trend_dir = "stable"
                group: dict = {
                    "department": dept_key,
                    "years": yr_entries,
                }
                if trend_dir:
                    group["trend_direction"] = trend_dir
                dept_groups.append(group)

            # Sort groups by most-recent cut_70 ASC (most selective first)
            dept_groups.sort(key=lambda g: (g["years"][0].get("cut_70") or 9.0))

            return {
                "university": university,
                "region": fb_meta.get("region"),
                "tier": fb_meta.get("tier"),
                "process_name": process_name,
                "department_filter": department,
                "note": "admission_process에 해당 전형 없음 — admission_result에서 직접 조회",
                "matched_dept_count": len(dept_groups),
                "departments": dept_groups[:20],
            }

        # G5: if university has no cut score data at all, report data gap immediately
        with _store._conn() as _conn:
            _cut_row = _conn.execute(
                """SELECT 1 FROM admission_result r
                   JOIN admission_department d ON d.id = r.department_id
                   WHERE d.university = ? AND r.cut_70 IS NOT NULL LIMIT 1""",
                (university,),
            ).fetchone()
        if not _cut_row:
            return {
                "error": f"No process found for '{university}' / '{process_name}'",
                "data_coverage": {
                    "has_cut_scores": False,
                    "data_note": (
                        f"{university}의 등급 컷 점수 데이터가 없습니다. "
                        "공식 입학처 또는 어디가(adiga.kr)를 참고하세요."
                    ),
                },
            }

        # Neither process nor result found — show suggestions from admission_result
        result_suggestions = _store.find_results(university=university, limit=50)
        if result_suggestions:
            seen_keys: set[tuple] = set()
            unique_suggestions = []
            for s in result_suggestions:
                pn = s.get("process_name", "")
                dept = s.get("department_name", "")
                # Skip garbage column-header process names and placeholder names
                if not pn or len(pn) < 3 or pn in ("경쟁률", "예비순위", "최고", "최저", "정원(명)", "폐지", "2명", "(전형미상)"):
                    continue
                key = (dept, pn)
                if key not in seen_keys:
                    seen_keys.add(key)
                    unique_suggestions.append({
                        "department": dept,
                        "process_name": pn,
                        "cut_70": s.get("cut_70"),
                    })
            if unique_suggestions:
                return {
                    "error": f"No process found for '{university}' / '{process_name}'",
                    "hint": "대학 이름은 맞지만 전형명/학과명이 다를 수 있습니다.",
                    "suggestions": unique_suggestions[:10],
                }

        # Fall back to admission_process suggestions (may have garbage names)
        proc_suggestions = _store.find_processes(university=university, limit=20)
        if proc_suggestions:
            seen_names: set[str] = set()
            unique_suggestions = []
            for s in proc_suggestions:
                pn = s.get("process_name", "")
                if not pn or pn in ("경쟁률", "예비순위", "최고", "최저", "정원(명)", "폐지"):
                    continue
                key = (s.get("department_name", ""), pn)
                if key not in seen_names:
                    seen_names.add(key)
                    unique_suggestions.append({
                        "department": s.get("department_name", ""),
                        "process_name": pn,
                        "process_type": s.get("process_type", ""),
                    })
            if unique_suggestions:
                return {
                    "error": f"No process found for '{university}' / '{process_name}'",
                    "hint": "대학 이름은 맞지만 전형명/학과명이 다를 수 있습니다.",
                    "suggestions": unique_suggestions[:10],
                }
        return {"error": f"No process found for '{university}' / '{process_name}'"}

    p = processes[0]
    meta = _uni_meta(p["university"])
    content = p.get("content") or ""
    results = _store.find_results(
        university=p["university"],
        process_name=p["process_name"],
    )

    # Sort results newest year first
    results_sorted = sorted(results, key=lambda r: r.get("result_year", 0), reverse=True)

    # D7: compute year_trend — one entry per year, sorted oldest→newest
    year_map: dict[int, dict] = {}
    for r in results_sorted:
        yr = r.get("result_year")
        if yr and yr not in year_map and r.get("cut_70") is not None:
            year_map[yr] = r
    year_trend: list[dict] = [
        {
            "year": yr,
            "cut_70": year_map[yr].get("cut_70"),
            "cut_50": year_map[yr].get("cut_50"),
            "cut_90": year_map[yr].get("cut_90"),
            "competition_rate": year_map[yr].get("competition_rate"),
        }
        for yr in sorted(year_map.keys())
    ]
    trend_direction: str | None = None
    if len(year_trend) >= 2:
        first_cut = year_trend[0]["cut_70"]
        last_cut = year_trend[-1]["cut_70"]
        _st = (results_sorted[0].get("score_type") or "등급") if results_sorted else "등급"
        if _st == "등급":
            diff = last_cut - first_cut  # negative → lower cut → harder
            if diff <= -0.2:
                trend_direction = "rising"   # getting harder each year
            elif diff >= 0.2:
                trend_direction = "falling"  # getting easier each year
            else:
                trend_direction = "stable"
        else:
            diff = last_cut - first_cut  # positive → higher cut → harder
            if diff >= 5:
                trend_direction = "rising"
            elif diff <= -5:
                trend_direction = "falling"
            else:
                trend_direction = "stable"

    # D6: compute required_improvement if student_grade provided
    improvement = None
    if student_grade is not None:
        for r in results_sorted:
            cut_70_val = r.get("cut_70")
            cut_50_val = r.get("cut_50")
            if cut_70_val is None:
                continue
            st = r.get("score_type", "등급")
            if st == "등급":
                gap_70 = round(student_grade - cut_70_val, 2)
                if gap_70 > 0:
                    verdict = "도전"
                    summary = f"현재 {gap_70}등급 부족. 합격하려면 {cut_70_val}등급까지 올려야 함."
                elif gap_70 > -0.5:
                    verdict = "추천"
                    summary = f"cut_70 범위 내. {abs(gap_70):.2f}등급 여유."
                else:
                    verdict = "안정"
                    summary = f"{abs(gap_70):.2f}등급 여유 (안정권)."
                improvement = {
                    "verdict": verdict,
                    "gap_to_cut70": gap_70,
                    "gap_to_cut50": round(student_grade - cut_50_val, 2) if cut_50_val else None,
                    "to_추천": gap_70,
                    "to_안정": round(student_grade - (cut_70_val - 0.5), 2),
                    "summary": summary,
                    "based_on_year": r.get("result_year"),
                }
            break

    # G5: data coverage info
    has_cut_scores = any(r.get("cut_70") is not None for r in results_sorted)
    has_competition_rate = any(r.get("competition_rate") is not None for r in results_sorted)
    data_coverage: dict = {
        "has_cut_scores": has_cut_scores,
        "has_competition_rate": has_competition_rate,
    }
    if not has_cut_scores:
        data_coverage["data_note"] = (
            "이 전형의 등급 컷 점수 데이터가 없습니다. "
            "공식 입학처 또는 어디가(adiga.kr)를 참고하세요."
        )

    response = {
        "university": p["university"],
        "region": meta.get("region", ""),
        "tier": meta.get("tier", 5),
        "campus": p.get("campus", ""),
        "department": p["department_name"],
        "process_name": p["process_name"],
        "process_type": p.get("process_type", ""),
        "admission_type": p.get("admission_type", ""),
        "quota": p.get("quota"),
        "attributes": p.get("attributes", {}),
        "content_preview": content[:4000],
        "matched_count": len(processes),
        "data_coverage": data_coverage,
        "admission_results": [
            _enrich_result(r)
            for r in results_sorted[:20]
        ],
    }
    if year_trend:
        response["year_trend"] = year_trend
        if trend_direction:
            response["trend_direction"] = trend_direction
    if improvement is not None:
        response["required_improvement"] = improvement
    return response


# ── Tool 3: match_by_grade ────────────────────────────────────────────────────

@mcp.tool()
def match_by_grade(
    grade: float,
    score_type: str = "등급",
    grade_type: str = "내신",
    admission_type: str | None = None,
    region: str | None = None,
    university: str | None = None,
    major_keywords: list[str] = [],
    track: str | None = None,
    risk_tolerance: str = "all",
    min_result_year: int = 2024,
    exclude_regional: bool = False,
    exclude_special: bool = True,
    student_suneung: dict | None = None,
    limit: int = 30,
) -> list[dict]:
    """Find admission programs matching a student's score against historical cut scores.

    For 등급 type: lower values are better (1.0 = best, 9.0 = worst).
    For 표준점수/백분위/환산점수: higher values are better.

    Verdict logic (등급):
      안정 — cut_70 is at least 0.5 grade points above student grade (comfortable).
      추천 — cut_70 is 0–0.5 points above student grade (on-target).
      도전 — cut_50 covers but cut_70 does not (reach school).

    Args:
        grade: Student's score (등급: 1.0–9.0; 표준점수: typically 200–900).
        score_type: "등급" (default), "표준점수", "백분위", or "환산점수".
        grade_type: "내신" (default, 학생부 교과 등급 → filters 수시 results) or
                    "수능등급" (수능 등급 → filters 정시 results).
                    Note: 내신 3등급 ≠ 수능 3등급 — always specify this correctly.
        admission_type: Explicit override for "수시" or "정시" (overrides grade_type default).
        region: Region filter (e.g., "서울", "수도권").
        university: Filter to a specific university name (substring match, e.g. "서강대학교").
                    Useful for "○○대학교에서 내 성적으로 갈 수 있는 학과" queries.
        major_keywords: Optional department name keywords (OR-combined, category expansion applies).
        track: Filter by 계열: "자연", "인문", "예체능", "의약학". Matches against
               the department's track field and department name keywords.
        risk_tolerance: "safe" (안정 only), "recommended" (안정+추천),
                        "reach" or "all" (includes 도전).
        min_result_year: Only include results from this year or later (default 2024).
                         Pass 0 to include all years.
        exclude_regional: If True, exclude 지역인재 전형s. Default False.
        exclude_special: If True (default), exclude restricted-access special 전형s
                         (기회균형, 농어촌, 특성화고, 장애인, 재직자 등).
        student_suneung: Optional dict of student's 수능 grades per subject, e.g.
                         {"국어": 3, "수학": 2, "영어": 1, "탐구": 4, "한국사": 3}.
                         When provided, each result includes "수능최저_충족" field showing
                         whether the student satisfies the 전형's 수능최저 requirement.
                         Processes where 수능최저 is confirmed NOT satisfied are moved to
                         verdict "도전(수능최저미충족)" regardless of cut score.
        limit: Maximum results (default 30).

    Returns:
        Programs sorted by tier then cut score proximity, each with verdict, margin,
        cut scores, result_year, competition_rate, acceptance_pct (when computable),
        수능최저 (CSAT minimum requirement from 모집요강, when available), and
        수능최저_충족 (True/False/None when student_suneung provided).
    """
    # D1/E1: map grade_type to filter. With E1 grade_type column, we can filter
    # directly by grade_type for 등급 scores (more precise than admission_type).
    effective_adm_type = admission_type  # used only as fallback
    db_grade_type: str | None = None
    if score_type == "등급":
        if grade_type in ("내신", "수능등급"):
            db_grade_type = grade_type  # E1: use grade_type column directly
        elif admission_type is None:
            # fallback: D1 approach — derive admission_type from grade_type label
            if grade_type == "내신":
                effective_adm_type = "수시"
            elif grade_type == "수능등급":
                effective_adm_type = "정시"

    # Use cut_50 as primary filter to capture 도전 cases too.
    # When university is specified: fetch all results for that university regardless of
    # cut score (the user wants to see every program, including hard-to-reach ones).
    if university:
        with _store._conn() as _uconn:
            _clauses = ["d.university LIKE ?"]
            _params: list = [f"%{university}%"]
            if db_grade_type:
                _clauses.append("r.grade_type = ?")
                _params.append(db_grade_type)
            elif effective_adm_type:
                _clauses.append("r.admission_type = ?")
                _params.append(effective_adm_type)
            if min_result_year:
                _clauses.append("r.result_year >= ?")
                _params.append(min_result_year)
            _where = " AND ".join(_clauses)
            _urows = _uconn.execute(f"""
                SELECT r.id, r.result_year, r.process_name, r.admission_type,
                       r.score_type, r.grade_type, r.cut_50, r.cut_70, r.cut_90,
                       r.competition_rate, r.attributes, r.department_id,
                       d.university, d.name as department_name, d.track
                FROM admission_result r
                JOIN admission_department d ON d.id = r.department_id
                WHERE {_where}
                ORDER BY r.cut_70 ASC NULLS LAST
                LIMIT 2000
            """, _params).fetchall()
        raw = [
            {
                "id": row[0], "result_year": row[1], "process_name": row[2],
                "admission_type": row[3], "score_type": row[4], "grade_type": row[5],
                "cut_50": row[6], "cut_70": row[7], "cut_90": row[8],
                "competition_rate": row[9],
                "attributes": json.loads(row[10]) if isinstance(row[10], str) else {},
                "department_id": row[11], "university": row[12],
                "department_name": row[13], "track": row[14],
            }
            for row in _urows
        ]
    else:
        raw = _store.find_results_by_score(
            student_grade=grade,
            score_type=score_type,
            admission_type=effective_adm_type if db_grade_type is None else None,
            grade_type=db_grade_type,
            use_cut="cut_50",
            limit=500,
        )

    # G7: when querying 수능등급, also pull 백분위 results by converting grade → 백분위
    # (Skip when university is specified — already fetched all records for that uni)
    if not university and db_grade_type == "수능등급" and score_type == "등급":
        pctile_lo, pctile_hi = _grade_to_pctile_range(grade)
        # Student's estimated 백분위 = top of their grade band (best-case)
        pctile_estimate = pctile_hi
        raw_pctile = _store.find_results_by_score(
            student_grade=float(pctile_estimate),
            score_type="백분위",
            grade_type="백분위",
            use_cut="cut_50",
            limit=200,
        )
        # Merge: avoid duplicates by (university, process_name, department_id)
        seen_ids = {r.get("id") for r in raw}
        for r in raw_pctile:
            if r.get("id") not in seen_ids:
                seen_ids.add(r.get("id"))
                raw.append(r)

    # B5: filter to recent years only (default: 2024+)
    # Skip when university mode: already applied in the SQL query above.
    if not university and min_result_year:
        raw_filtered = [r for r in raw if (r.get("result_year") or 0) >= min_result_year]
        # Fallback: if filtering removes everything, use most recent available year
        if not raw_filtered and raw:
            best_year = max(r.get("result_year") or 0 for r in raw)
            raw_filtered = [r for r in raw if r.get("result_year") == best_year]
        raw = raw_filtered

    expanded_kws = _expand_keywords(major_keywords) if major_keywords else []

    results: list[dict] = []
    for r in raw:
        uni = r["university"]
        if region and not _in_region(uni, region):
            continue

        # A4: university substring filter
        if university and university not in uni:
            continue

        dept_name = r.get("department_name") or ""
        # F2: skip OCR artifact / garbage names
        if not _is_valid_dept_name(dept_name) or not _is_valid_dept_name(r.get("process_name")):
            continue

        if expanded_kws and not any(kw in dept_name for kw in expanded_kws):
            continue

        # G3: track filter (자연/인문/예체능/의약학)
        if track:
            dept_row = {"track": r.get("track") or "", "name": dept_name}
            if not _matches_track(dept_row, track):
                continue

        # F1: skip restricted special 전형s (기회균형, 농어촌, 특성화고 등)
        if exclude_special and _is_special_admission(r.get("process_name") or ""):
            continue

        # C2: skip 지역인재 전형s if requested
        if exclude_regional and _is_regional(r.get("process_name") or ""):
            continue

        # A2: when querying 수능등급, exclude records tagged as 수시 admission_type.
        # Some universities (부산대 etc.) report 수능등급 averages for 수시 합격생,
        # which contaminates 정시 queries. grade_type='수능등급'+admission_type='수시'
        # is structurally ambiguous — skip unless caller explicitly asked for it.
        if db_grade_type == "수능등급" and r.get("admission_type") == "수시" and admission_type is None:
            continue

        cut_70 = r.get("cut_70")
        cut_50 = r.get("cut_50")

        # G7: use record's actual score_type for verdict (may differ for merged 백분위 records)
        r_score_type = r.get("score_type") or score_type
        r_is_grade = r_score_type == "등급" or r.get("grade_type") in ("내신", "수능등급", "등급")

        # Compute verdict and margin using appropriate 등급 for comparison
        # For 백분위 records merged via G7, compare student's estimated 백분위 vs cut
        if r_is_grade:
            # Lower is better: margin > 0 means student beats cut
            compare_grade = grade  # student's 수능 등급
            if cut_70 is not None:
                margin = cut_70 - compare_grade
                if margin >= 0.5:
                    verdict = "안정"
                elif margin >= 0:
                    verdict = "추천"
                else:
                    verdict = "도전"
            else:
                margin = (cut_50 or 0.0) - compare_grade
                verdict = "추천" if margin >= 0 else "도전"
        else:
            # Higher is better (백분위/표준점수): use student's estimated 백분위
            if r_score_type == "백분위":
                _, student_pctile = _grade_to_pctile_range(grade)
                compare_score = float(student_pctile)
            else:
                compare_score = grade  # for 표준점수 etc.
            if cut_70 is not None:
                margin = compare_score - cut_70
                if margin >= 5:
                    verdict = "안정"
                elif margin >= 0:
                    verdict = "추천"
                else:
                    verdict = "도전"
            else:
                margin = compare_score - (cut_50 or 0.0)
                verdict = "추천" if margin >= 0 else "도전"

        # Apply risk_tolerance filter
        if risk_tolerance == "safe" and verdict != "안정":
            continue
        if risk_tolerance == "recommended" and verdict == "도전":
            continue

        # D3: estimate acceptance percentile
        cut_90 = r.get("cut_90")
        acc_pct = _estimate_acceptance_pct(grade, cut_50, cut_70, cut_90, r_score_type)

        meta = _uni_meta(uni)
        proc_name = r.get("process_name", "")
        # Strip trailing OCR artifact chars from output names
        dept_name_clean = re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', dept_name).strip()
        entry: dict = {
            "university": uni,
            "region": meta.get("region", ""),
            "tier": meta.get("tier", 5),
            "department": dept_name_clean,
            "process_name": proc_name,
            "admission_type": r.get("admission_type", ""),
            "score_type": r.get("score_type", ""),
            "result_year": r.get("result_year"),
            "cut_70": cut_70,
            "cut_50": cut_50,
            "competition_rate": r.get("competition_rate"),
            "verdict": verdict,
            "margin": round(margin, 2),
        }
        if _is_regional(proc_name):
            entry["is_regional_restricted"] = True
        if acc_pct is not None:
            entry["acceptance_pct"] = acc_pct
        results.append(entry)

    # D8: bulk-lookup 수능최저 from admission_process for all matched entries
    sm_pairs = [(e["university"], e["process_name"]) for e in results]
    sm_map = _lookup_suneung_min_bulk(sm_pairs)
    if sm_map:
        for e in results:
            sm = sm_map.get((e["university"], e["process_name"]))
            if sm is not None:
                e["수능최저"] = _enrich_수능최저(sm, student_suneung)  # H1

    # G1: check 수능최저 충족 when student_suneung provided
    if student_suneung:
        for e in results:
            sm_raw = sm_map.get((e["university"], e["process_name"])) if sm_map else None
            if sm_raw is None:
                continue
            check = check_수능최저(student_suneung, sm_raw)
            e["수능최저_충족"] = check.get("충족")
            e["수능최저_설명"] = check.get("설명", "")
            # Override verdict if 수능최저 confirmed not satisfied
            if check.get("충족") is False:
                e["verdict"] = "도전(수능최저미충족)"

    # Sort: tier ASC, then by cut proximity (closest matching cut first)
    if score_type == "등급":
        results.sort(key=lambda x: (x["tier"], x.get("cut_70") or 9.0))
    else:
        results.sort(key=lambda x: (x["tier"], -(x.get("cut_70") or 0.0)))

    return results[:limit]


# ── Tool 4: list_universities ─────────────────────────────────────────────────

@mcp.tool()
def list_universities(
    region: str | None = None,
    max_tier: int | None = None,
) -> list[dict]:
    """List universities with region, prestige tier, and DB statistics.

    Tier scale: 1=SKY, 2=서성한+주요국립, 3=인서울중위+지역거점,
                4=인서울하위+수도권+지방사립, 5=기타.

    Args:
        region: Filter by region (e.g., "서울", "경기", "수도권", "지방").
        max_tier: Only return universities with tier <= max_tier.

    Returns:
        Universities sorted by tier then name, with dept_count and process_count from DB.
    """
    with _store._conn() as conn:
        rows = conn.execute("""
            SELECT d.university,
                   COUNT(DISTINCT d.id)  AS dept_count,
                   COUNT(DISTINCT p.id)  AS process_count
            FROM admission_department d
            LEFT JOIN admission_process p ON p.department_id = d.id
            GROUP BY d.university
        """).fetchall()
        # G5: which universities have cut score data in admission_result
        result_rows = conn.execute("""
            SELECT d.university, COUNT(*) as has_cuts
            FROM admission_result r
            JOIN admission_department d ON d.id = r.department_id
            WHERE r.cut_70 IS NOT NULL
            GROUP BY d.university
        """).fetchall()
    db_counts = {r[0]: {"dept_count": r[1], "process_count": r[2]} for r in rows}
    unis_with_cuts: set[str] = {r[0] for r in result_rows}

    results: list[dict] = []
    for uni, meta in _UNI_META.items():
        if region and not _in_region(uni, region):
            continue
        if max_tier is not None and meta.get("tier", 5) > max_tier:
            continue

        counts = db_counts.get(uni, {"dept_count": 0, "process_count": 0})
        results.append({
            "university": uni,
            "region": meta.get("region", ""),
            "region_broad": meta.get("region_broad", ""),
            "tier": meta.get("tier", 5),
            "dept_count": counts["dept_count"],
            "process_count": counts["process_count"],
            "has_result_data": uni in unis_with_cuts,  # G5
        })

    results.sort(key=lambda x: (x["tier"], x["university"]))
    return results


@mcp.tool()
def list_departments(
    year: int = 2025,
    keyword: str | None = None,
    limit: int = 200,
) -> dict:
    """List all distinct 학과/전공 names in the DB for a given year.

    Use this when a student mentions a field vaguely (e.g. "컴퓨터 학과")
    to discover exact department names, then pass the best matches as
    major_keywords to match_by_grade or search_programs.

    Args:
        year: Admission year (default 2025).
        keyword: Optional filter — returns only names containing this string.
                 Example: "컴퓨터" → ["AI컴퓨터공학부", "컴퓨터공학과", ...]
        limit: Max distinct names to return (default 200).

    Returns:
        {"year", "keyword", "total", "departments": [sorted list of dept names]}
    """
    names = _store.list_distinct_departments(year=year, keyword=keyword, limit=limit)
    return {"year": year, "keyword": keyword, "total": len(names), "departments": names}


@mcp.tool()
def check_university_feasibility(
    university: str,
    grade: float,
    grade_type: str = "내신",
    major_keywords: list[str] = [],
    min_result_year: int = 2024,
) -> dict:
    """Check feasibility of entering a specific university given a student's score.

    Unlike match_by_grade(university=X), this shows ALL 전형s at the university
    including out-of-reach ones, labeling each with 안정/추천/도전/불가/데이터없음.
    Also returns a summary count across verdict categories.

    Use this for questions like "서울대 갈 수 있어?", "연세대 가능성은?".

    Args:
        university: Target university name (substring match, e.g. "서울대학교").
        grade: Student's score as 등급 (1.0=best, 9.0=worst).
        grade_type: "내신" (수시 학생부교과/종합) or "수능등급" (정시).
        major_keywords: Optional dept name filter — OR-combined, category expansion applies.
        min_result_year: Only include results from this year or later (default 2024).

    Verdict thresholds (등급, lower=better):
        안정   — cut_70 >= student_grade + 0.5
        추천   — cut_70 >= student_grade
        도전   — cut_70 >= student_grade - 1.0
        불가   — cut_70 < student_grade - 1.0
        데이터없음 — no cut_70 available
    """
    with _store._conn() as conn:
        # Prefer exact match; fall back to substring if no exact match found
        exact_rows = conn.execute("""
            SELECT r.*, d.university, d.campus, d.track, d.name as department_name
            FROM admission_result r
            JOIN admission_department d ON d.id = r.department_id
            WHERE d.university = ? AND r.grade_type = ? AND r.result_year >= ?
            ORDER BY r.cut_70 ASC
            LIMIT 500
        """, (university, grade_type, min_result_year)).fetchall()
        if exact_rows:
            rows = exact_rows
        else:
            rows = conn.execute("""
                SELECT r.*, d.university, d.campus, d.track, d.name as department_name
                FROM admission_result r
                JOIN admission_department d ON d.id = r.department_id
                WHERE d.university LIKE ? AND r.grade_type = ? AND r.result_year >= ?
                ORDER BY r.cut_70 ASC
                LIMIT 500
            """, (f"%{university}%", grade_type, min_result_year)).fetchall()

    results = [dict(r) for r in rows]

    if major_keywords:
        expanded: list[str] = []
        for kw in major_keywords:
            expanded.extend(SYNONYMS.get(kw, [kw]))
        results = [r for r in results
                   if any(kw in (r.get("department_name") or "") for kw in expanded)]

    summary = {"안정": 0, "추천": 0, "도전": 0, "불가": 0, "데이터없음": 0}
    items = []
    for r in results:
        cut_70 = r.get("cut_70")
        if cut_70 is None:
            verdict = "데이터없음"
            margin = None
        else:
            margin = cut_70 - grade
            if margin >= 0.5:
                verdict = "안정"
            elif margin >= 0:
                verdict = "추천"
            elif margin >= -1.0:
                verdict = "도전"
            else:
                verdict = "불가"
        summary[verdict] += 1
        dept_clean = re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', r.get("department_name") or "").strip()
        items.append({
            "department": dept_clean,
            "process_name": r.get("process_name"),
            "admission_type": r.get("admission_type"),
            "cut_70": cut_70,
            "cut_50": r.get("cut_50"),
            "margin": round(margin, 2) if margin is not None else None,
            "verdict": verdict,
            "competition_rate": r.get("competition_rate"),
            "result_year": r.get("result_year"),
        })

    _order = {"안정": 0, "추천": 1, "도전": 2, "불가": 3, "데이터없음": 4}
    items.sort(key=lambda x: (_order.get(x["verdict"], 9), x.get("cut_70") or 99))
    actual_univ = results[0].get("university", university) if results else university

    return {
        "university": actual_univ,
        "student_grade": grade,
        "grade_type": grade_type,
        "summary": summary,
        "total": len(items),
        "results": items,
    }


# ── Tool 5: search_fulltext ───────────────────────────────────────────────────

@mcp.tool()
def search_fulltext(
    query: str,
    limit: int = 20,
) -> list[dict]:
    """Full-text search across admission program content using FTS5.

    Supports Korean text and FTS5 operators (AND, OR, NEAR).
    Example queries: "논술 자연계", "수능최저 없음 컴퓨터".

    Args:
        query: FTS5 search query string.
        limit: Maximum results (default 20).

    Returns:
        Programs with snippet (first 200 chars of matching content),
        university, region, tier, department, process_name, process_type.
    """
    rows = _store.search(query, table="process", limit=limit)
    results: list[dict] = []
    for r in rows:
        content = r.get("content") or ""
        meta = _uni_meta(r["university"])
        results.append({
            "university": r["university"],
            "region": meta.get("region", ""),
            "tier": meta.get("tier", 5),
            "department": r.get("department_name", ""),
            "process_name": r.get("process_name", ""),
            "process_type": r.get("process_type", ""),
            "snippet": content[:200],
        })
    return results


# ── Tool 6: compare_universities ──────────────────────────────────────────────

@mcp.tool()
def compare_universities(
    department_keyword: str,
    universities: list[str] | None = None,
    region: str | None = None,
    max_tier: int | None = None,
    score_type: str = "등급",
    grade_type: str | None = None,
    min_result_year: int = 2024,
    exclude_special: bool = True,
    exclude_regional: bool = False,
    student_grade: float | None = None,
    student_suneung: dict | None = None,
    limit: int = 50,
) -> dict:
    """Compare universities side-by-side by department cut scores.

    Useful for answering "전국 컴퓨터공학과 입결 순위" or "서울 의예과 경쟁률 비교".
    Returns one representative row per university (best-matching department/process).

    Args:
        department_keyword: Department name keyword (e.g., "컴퓨터공학", "의예").
        universities: Optional list of specific university names to include.
        region: Filter by region ("서울", "수도권", "지방", etc.).
        max_tier: Only include universities with tier <= this value.
        score_type: "등급" (default), "표준점수", "백분위", "환산점수".
        grade_type: "내신" or "수능등급" — refines 등급 type (E1).
        min_result_year: Only use results from this year or later (default 2024).
        exclude_special: If True (default), exclude restricted-access special 전형s.
        exclude_regional: If True, exclude 지역균형/지역인재 전형s (regional-only 전형s).
        student_grade: Optional student's grade (등급). When provided, each row
                       includes verdict, margin, and acceptance_pct (H2).
        student_suneung: Optional 수능 grades dict for 수능최저 충족 check (G1).
        limit: Maximum number of universities to return (default 50).

    Returns:
        One row per university, sorted by cut_70 ASC (most selective first).
        Fields: university, tier, region, department, process_name, admission_type,
                cut_50, cut_70, cut_90, competition_rate, 충원합격인원, result_year.
        When student_grade provided: also verdict, margin, acceptance_pct.
        When student_suneung provided: also 수능최저_충족, 수능최저_설명.
    """
    # Resolve effective grade_type for DB query
    db_grade_type: str | None = None
    if score_type == "등급" and grade_type in ("내신", "수능등급"):
        db_grade_type = grade_type

    # Fetch all results matching score_type, filtered to recent years
    # We query broadly then filter by dept keyword in Python
    with _store._conn() as conn:
        # Build WHERE clause
        clauses = ["1=1"]
        params: list = []

        if db_grade_type:
            clauses.append("r.grade_type = ?")
            params.append(db_grade_type)
        else:
            clauses.append("r.score_type = ?")
            params.append(score_type)

        if min_result_year:
            clauses.append("r.result_year >= ?")
            params.append(min_result_year)

        clauses.append("r.cut_70 IS NOT NULL")

        where = " AND ".join(clauses)
        rows = conn.execute(f"""
            SELECT r.id, r.result_year, r.process_name, r.admission_type,
                   r.score_type, r.grade_type, r.cut_50, r.cut_70, r.cut_90,
                   r.competition_rate, r.attributes,
                   d.university, d.name as department_name
            FROM admission_result r
            JOIN admission_department d ON d.id = r.department_id
            WHERE {where}
            ORDER BY r.cut_70 ASC
        """, params).fetchall()

    # Filter by department_keyword (case-insensitive substring)
    # Also expand via SYNONYMS / CATEGORY_KEYWORDS
    expanded_kws = _expand_keywords([department_keyword]) if department_keyword else []
    expanded_lower = [k.lower() for k in expanded_kws]

    filtered = []
    for row in rows:
        if not department_keyword:
            filtered.append(row)  # empty keyword → include all
        else:
            dept_name = (row[12] or "").lower()
            if any(k in dept_name for k in expanded_lower):
                filtered.append(row)

    # A5: when a single university is specified with no keyword, return all departments
    # (not just the best per university). Otherwise deduplicate to 1 row per university.
    single_uni_mode = (not department_keyword) and universities and len(universities) == 1

    # Apply university / region / tier filters; keep best result per university
    best: dict[str, dict] = {}   # {university: best_row_dict} for normal mode
    all_rows_list: list[dict] = []  # for single_uni_mode
    _seen_dept_proc: set = set()    # dedup by (dept_name, process_name) in single_uni_mode

    for row in filtered:
        uni = row[11]
        if universities and uni not in universities:
            continue
        if region and not _in_region(uni, region):
            continue
        meta = _uni_meta(uni)
        tier = meta.get("tier", 5)
        if max_tier and tier > max_tier:
            continue

        # F2: skip OCR garbage names
        if not _is_valid_dept_name(row[12]) or not _is_valid_dept_name(row[2]):
            continue

        # F1: skip restricted special 전형s — use process_name (row[2])
        if exclude_special and _is_special_admission(row[2]):
            continue

        # F3: skip regional-restricted 전형s if requested
        if exclude_regional and _is_regional(row[2]):
            continue

        attrs = json.loads(row[10]) if isinstance(row[10], str) else {}
        cut_70 = row[7]
        proc_name_cmp = row[2]
        dept_clean = re.sub(r'(\d+\))+$|[*†‡··∙•]+$', '', (row[12] or "")).strip()

        entry: dict = {
            "university": uni,
            "tier": tier,
            "region": meta.get("region", ""),
            "department": dept_clean,
            "process_name": proc_name_cmp,
            "admission_type": row[3],
            "score_type": row[4],
            "grade_type": row[5],
            "result_year": row[1],
            "cut_50": row[6],
            "cut_70": cut_70,
            "cut_90": row[8],
            "competition_rate": row[9],
        }
        if _is_regional(proc_name_cmp):
            entry["is_regional_restricted"] = True
        waitlist = attrs.get("충원합격인원")
        if waitlist is not None:
            entry["충원합격인원"] = waitlist

        if single_uni_mode:
            # Include all departments for single-university queries (dedup by dept+process)
            key = (dept_clean, proc_name_cmp, row[1])
            if key not in _seen_dept_proc:
                _seen_dept_proc.add(key)
                all_rows_list.append(entry)
        else:
            # Keep the row with lowest cut_70 per university
            if uni not in best or (cut_70 is not None and (best[uni].get("cut_70") is None or cut_70 < best[uni]["cut_70"])):
                best[uni] = entry

    results = all_rows_list if single_uni_mode else list(best.values())

    # H2: add student grade comparison fields
    if student_grade is not None:
        for e in results:
            cut_70_val = e.get("cut_70")
            if cut_70_val is None:
                continue
            if score_type == "등급":
                margin = cut_70_val - student_grade
                if margin >= 0.5:
                    e["verdict"] = "안정"
                elif margin >= 0:
                    e["verdict"] = "추천"
                else:
                    e["verdict"] = "도전"
                e["margin"] = round(margin, 2)
            else:
                margin = student_grade - cut_70_val
                e["verdict"] = "안정" if margin >= 5 else ("추천" if margin >= 0 else "도전")
                e["margin"] = round(margin, 2)
            acc_pct = _estimate_acceptance_pct(
                student_grade, e.get("cut_50"), cut_70_val, e.get("cut_90"), score_type
            )
            if acc_pct is not None:
                e["acceptance_pct"] = acc_pct

    # G1: bulk-lookup 수능최저 and check against student_suneung
    if student_suneung:
        sm_pairs = [(e["university"], e["process_name"]) for e in results]
        sm_map = _lookup_suneung_min_bulk(sm_pairs)
        for e in results:
            sm = sm_map.get((e["university"], e["process_name"]))
            if sm is None:
                continue
            e["수능최저"] = _enrich_수능최저(sm, student_suneung)  # H1: adds 원문_요약
            check = check_수능최저(student_suneung, sm)
            e["수능최저_충족"] = check.get("충족")
            e["수능최저_설명"] = check.get("설명", "")
            if check.get("충족") is False and "verdict" in e:
                e["verdict"] = "도전(수능최저미충족)"

    # Sort: cut_70 ASC for 등급 (lower=harder), DESC for 표준점수 (higher=harder)
    if score_type == "등급":
        results.sort(key=lambda x: (x.get("cut_70") or 9.0, x["tier"]))
    else:
        results.sort(key=lambda x: (-(x.get("cut_70") or 0.0), x["tier"]))

    rows = results[:limit]
    return {
        "keyword": department_keyword,
        "total": len(rows),
        "grade_type": grade_type or score_type,
        "student_grade": student_grade,
        "rows": rows,
    }


# ── Tool 7: match_by_subjects ─────────────────────────────────────────────────

def _compute_effective_grade(
    grades: dict[str, float],
    전형요소: dict | None,
) -> tuple[float, dict]:
    """Compute effective 내신 grade from individual subject grades.

    For 교과 100% processes: simple average of provided subjects.
    For mixed processes: notes that non-교과 components are excluded.

    Returns:
        (effective_grade, breakdown_dict)
    """
    if not grades:
        raise ValueError("No grades provided")

    avg = round(sum(grades.values()) / len(grades), 2)

    breakdown: dict = {
        "subjects": grades,
        "average": avg,
    }

    if 전형요소 and not any(k.endswith("단계") for k in 전형요소):
        교과_pct = 전형요소.get("교과", 0)
        if 교과_pct >= 100:
            breakdown["note"] = "교과 100% 반영 — 평균 등급이 실질 점수"
        elif 교과_pct > 0:
            breakdown["note"] = (
                f"교과 {교과_pct}% 반영 "
                f"(나머지 {100 - 교과_pct}%는 서류/면접 등 — 수치 추정 불가)"
            )
        else:
            breakdown["note"] = "교과 비중 낮음 — 서류·면접 위주 전형"
    else:
        breakdown["note"] = "전형요소 미상 — 단순 과목 평균 사용"

    return avg, breakdown


# G4: default 수능 반영비율 by 계열
_SUNEUNG_WEIGHTS: dict[str, dict[str, float]] = {
    "자연": {"수학": 0.40, "과학": 0.30, "국어": 0.20, "영어": 0.10},
    "인문": {"국어": 0.35, "영어": 0.30, "수학": 0.25, "사회": 0.10},
    "의약학": {"수학": 0.35, "과학": 0.35, "국어": 0.20, "영어": 0.10},
    "예체능": {"국어": 0.30, "영어": 0.30, "수학": 0.20, "사회": 0.20},
}
_SUNEUNG_WEIGHTS_DEFAULT = {"국어": 0.30, "수학": 0.30, "영어": 0.20, "과학": 0.10, "사회": 0.10}


def _compute_suneung_effective_grade(
    grades: dict[str, float],
    track: str | None = None,
) -> tuple[float, dict]:
    """G4: Compute effective 수능등급 using track-specific weights.

    For 자연계: 수학 40%, 탐구(과학) 30%, 국어 20%, 영어 10%
    For 인문계: 국어 35%, 영어 30%, 수학 25%, 탐구(사회) 10%

    Returns (effective_grade, breakdown_dict).
    Lower effective grade = better (같은 등급 척도).
    """
    weights = _SUNEUNG_WEIGHTS.get(track or "", _SUNEUNG_WEIGHTS_DEFAULT)

    weighted_sum = 0.0
    total_weight = 0.0
    matched: dict[str, dict] = {}

    for subj, w in weights.items():
        grade_val = grades.get(subj)
        if grade_val is not None:
            weighted_sum += grade_val * w
            total_weight += w
            matched[subj] = {"등급": grade_val, "비중": f"{int(w*100)}%"}

    if not matched:
        # fallback: simple average
        avg = round(sum(grades.values()) / len(grades), 2)
        return avg, {"subjects": grades, "average": avg, "note": "가중치 과목 없어 단순 평균 사용"}

    # Normalize
    effective = round(weighted_sum / total_weight, 2)
    missing = [s for s in weights if s not in grades]
    note = f"{'·'.join(weights)} 가중치 적용 ({track or '기본'}계열)"
    if missing:
        note += f" — {', '.join(missing)} 미입력 (해당 과목 평균 등급으로 대체될 수 있음)"

    breakdown = {
        "subjects": matched,
        "effective_grade": effective,
        "weights": {s: f"{int(w*100)}%" for s, w in weights.items()},
        "track": track or "기본",
        "note": note,
    }
    return effective, breakdown


@mcp.tool()
def match_by_subjects(
    korean: float | None = None,
    math: float | None = None,
    english: float | None = None,
    science: float | None = None,
    social: float | None = None,
    history: float | None = None,
    grade_type: str = "내신",
    region: str | None = None,
    major_keywords: list[str] = [],
    track: str | None = None,
    risk_tolerance: str = "all",
    min_result_year: int = 2024,
    exclude_regional: bool = False,
    exclude_special: bool = True,
    student_suneung: dict | None = None,
    limit: int = 30,
) -> list[dict]:
    """Find programs based on per-subject grades by computing an effective average.

    Better than match_by_grade when the student's subject grades differ significantly.
    Uses 전형요소 data (when available) to note if the process weights 교과 < 100%.

    Args:
        korean: 국어 내신 등급 (1.0–9.0) — 수능등급 시 수능 국어 등급 입력.
        math: 수학 내신 등급 — 수능등급 시 수능 수학 등급 입력.
        english: 영어 내신 등급 — 수능등급 시 수능 영어 등급 입력.
        science: 과학 내신 등급 — 수능등급 시 수능 과탐 등급 입력.
        social: 사회 내신 등급 — 수능등급 시 수능 사탐 등급 입력.
        history: 한국사 내신 등급.
        grade_type: "내신" (default) or "수능등급".
            When "수능등급": uses track-specific weights (G4):
            - 자연계: 수학 40%, 과학 30%, 국어 20%, 영어 10%
            - 인문계: 국어 35%, 영어 30%, 수학 25%, 사회 10%
        region: Region filter (e.g., "서울", "수도권").
        major_keywords: Department name keywords (OR-combined, category expansion applies).
        track: Filter by 계열: "자연", "인문", "예체능", "의약학".
        risk_tolerance: "safe", "recommended", or "all" (default).
        min_result_year: Minimum result year (default 2024).
        exclude_regional: Exclude 지역인재 전형s. Default False.
        exclude_special: If True (default), exclude restricted-access special 전형s.
        student_suneung: Optional 수능 grades for 수능최저 충족 check, e.g.
                         {"국어": 3, "수학": 2, "영어": 1, "탐구": 4}.
                         When provided, each result includes "수능최저_충족" field.
        limit: Maximum results (default 30).

    Returns:
        Same as match_by_grade plus: effective_grade (computed average of provided
        subjects) and grade_breakdown (per-subject detail and 전형요소 note).
        Each result also includes 전형요소 when available (from 모집요강 attributes).
    """
    # Build grades dict from provided subjects
    grades: dict[str, float] = {}
    if korean is not None:
        grades["국어"] = korean
    if math is not None:
        grades["수학"] = math
    if english is not None:
        grades["영어"] = english
    if science is not None:
        grades["과학"] = science
    if social is not None:
        grades["사회"] = social
    if history is not None:
        grades["한국사"] = history

    if not grades:
        return [{"error": "최소 1개 이상의 과목 등급을 입력하세요."}]

    # G4: For 수능등급, use track-specific weighted average
    if grade_type == "수능등급":
        effective_grade, base_breakdown = _compute_suneung_effective_grade(grades, track)
    else:
        # For 내신: simple average
        effective_grade = round(sum(grades.values()) / len(grades), 2)
        _, base_breakdown = _compute_effective_grade(grades, None)

    # Delegate to match_by_grade logic using effective_grade
    raw_results = match_by_grade(
        grade=effective_grade,
        score_type="등급",
        grade_type=grade_type,
        region=region,
        major_keywords=major_keywords,
        track=track,
        risk_tolerance=risk_tolerance,
        min_result_year=min_result_year,
        exclude_regional=exclude_regional,
        exclude_special=exclude_special,
        student_suneung=student_suneung,
        limit=limit * 2,  # fetch extra since we'll re-rank
    )

    # Enrich with 전형요소 and per-process effective grade
    enriched_results: list[dict] = []
    uni_proc_pairs = [(r["university"], r["process_name"]) for r in raw_results]

    # Bulk-fetch 전형요소 from admission_process attributes
    proc_전형요소: dict[tuple[str, str], dict] = {}
    if uni_proc_pairs:
        unis = list({u for u, _ in uni_proc_pairs})
        ph = ",".join("?" * len(unis))
        try:
            with _store._conn() as conn:
                rows = conn.execute(
                    f"""
                    SELECT d.university, p.process_name, p.attributes
                    FROM admission_process p
                    JOIN admission_department d ON d.id = p.department_id
                    WHERE d.university IN ({ph})
                      AND json_extract(p.attributes, '$.전형요소') IS NOT NULL
                    """,
                    unis,
                ).fetchall()
                for row in rows:
                    attrs = json.loads(row[2]) if isinstance(row[2], str) else {}
                    te = attrs.get("전형요소")
                    if te:
                        key = (row[0], row[1])
                        if key not in proc_전형요소:
                            proc_전형요소[key] = te
        except Exception:
            pass

    for r in raw_results:
        key = (r["university"], r["process_name"])
        전형요소 = proc_전형요소.get(key)

        # Compute process-specific effective grade
        # G4: for 수능등급, use track-weighted computation (not 교과 전형요소)
        if grade_type == "수능등급":
            proc_grade, proc_breakdown = _compute_suneung_effective_grade(grades, track)
        else:
            proc_grade, proc_breakdown = _compute_effective_grade(grades, 전형요소)

        entry = dict(r)
        entry["effective_grade"] = proc_grade
        entry["grade_breakdown"] = proc_breakdown
        if 전형요소:
            entry["전형요소"] = 전형요소
        enriched_results.append(entry)

    # Re-sort by tier then cut_70
    enriched_results.sort(key=lambda x: (x["tier"], x.get("cut_70") or 9.0))
    return enriched_results[:limit]


# ── Tool 8: suggest_portfolio ─────────────────────────────────────────────────

@mcp.tool()
def suggest_portfolio(
    grade: float,
    grade_type: str = "내신",
    region: str | None = None,
    major_keywords: list[str] = [],
    track: str | None = None,
    n_safe: int = 2,
    n_target: int = 3,
    n_reach: int = 1,
    student_suneung: dict | None = None,
    exclude_special: bool = True,
    min_result_year: int = 2024,
) -> dict:
    """G6: Suggest an optimized 수시 portfolio of 6 applications (안정/추천/도전).

    수시 allows up to 6 applications total. This tool allocates them across
    three risk buckets to maximize both safety and reach.

    Args:
        grade: Student's 내신 average grade (1.0–9.0, lower=better).
        grade_type: "내신" (default) or "수능등급".
        region: Region filter (e.g., "서울", "수도권").
        major_keywords: Department name keywords (OR-combined, category expansion).
        track: 계열 filter ("자연", "인문", "예체능", "의약학").
        n_safe: Number of 안정 (safe) applications (default 2).
        n_target: Number of 추천 (on-target) applications (default 3).
        n_reach: Number of 도전 (reach) applications (default 1).
        student_suneung: Student's 수능 grades for 수능최저 충족 check.
            e.g. {"국어": 3, "수학": 2, "영어": 1, "탐구": 4}.
        exclude_special: Exclude restricted-access 전형s (default True).
        min_result_year: Minimum result year to use (default 2024).

    Returns:
        {
          "안정": [...n_safe universities],
          "추천": [...n_target universities],
          "도전": [...n_reach universities],
          "summary": "6개 카드 배분: 안정 2, 추천 3, 도전 1",
          "note": "같은 대학은 각 카테고리에서 한 번만 포함됨",
        }
        Each entry: university, tier, region, department, process_name, cut_70,
                    verdict, acceptance_pct, 수능최저 (when available).
    """
    total_cards = n_safe + n_target + n_reach
    if total_cards > 6:
        return {"error": f"총 지원 카드 수({total_cards})가 6을 초과합니다. n_safe+n_target+n_reach ≤ 6"}

    # Get a large pool from match_by_grade
    pool = match_by_grade(
        grade=grade,
        score_type="등급",
        grade_type=grade_type,
        region=region,
        major_keywords=major_keywords,
        track=track,
        risk_tolerance="all",
        min_result_year=min_result_year,
        exclude_special=exclude_special,
        student_suneung=student_suneung,
        limit=200,
    )

    # Separate by verdict bucket
    safe_pool = [r for r in pool if r.get("verdict") == "안정"]
    target_pool = [r for r in pool if r.get("verdict") == "추천"]
    reach_pool = [r for r in pool if r.get("verdict") in ("도전", "도전(수능최저미충족)")]

    def _pick_diverse(candidates: list[dict], n: int) -> list[dict]:
        """Pick n entries from candidates with no duplicate universities."""
        seen_unis: set[str] = set()
        picked: list[dict] = []
        for c in candidates:
            if len(picked) >= n:
                break
            uni = c.get("university", "")
            if uni and uni not in seen_unis:
                seen_unis.add(uni)
                picked.append(c)
        return picked

    # Sort each bucket: safe → best tier first (lowest cut), target → by tier+cut
    safe_pool.sort(key=lambda x: (x.get("tier", 5), x.get("cut_70") or 9.0))
    target_pool.sort(key=lambda x: (x.get("tier", 5), x.get("cut_70") or 9.0))
    # Reach: prefer 수능최저 satisfied ones first if student_suneung given
    if student_suneung:
        reach_pool.sort(key=lambda x: (
            0 if x.get("verdict") == "도전" else 1,  # 수능최저미충족 last
            x.get("tier", 5),
            x.get("cut_70") or 9.0,
        ))
    else:
        reach_pool.sort(key=lambda x: (x.get("tier", 5), x.get("cut_70") or 9.0))

    # Track used universities across all buckets to prevent cross-category duplicates
    used_unis: set[str] = set()

    def _pick_no_dup(candidates: list[dict], n: int) -> list[dict]:
        picked: list[dict] = []
        for c in candidates:
            if len(picked) >= n:
                break
            uni = c.get("university", "")
            if uni and uni not in used_unis:
                used_unis.add(uni)
                picked.append(c)
        return picked

    selected_safe = _pick_no_dup(safe_pool, n_safe)
    selected_target = _pick_no_dup(target_pool, n_target)
    selected_reach = _pick_no_dup(reach_pool, n_reach)

    # A6: region expansion fallback — if any bucket is underfilled and a region was set,
    # widen the region progressively (서울→수도권→전국) to fill the bucket.
    region_expanded: str | None = None
    if region and (len(selected_safe) < n_safe or len(selected_target) < n_target or len(selected_reach) < n_reach):
        # Build expansion chain: 서울/인천/경기 → 수도권 → 전국
        _sub_region_chain: dict[str, str] = {"서울": "수도권", "인천": "수도권", "경기": "수도권"}
        _expansion_steps: list[str | None] = []
        _mid = _sub_region_chain.get(region)
        if _mid:
            _expansion_steps.append(_mid)
        _expansion_steps.append(None)  # None = 전국 (always the final fallback)

        for _exp_region in _expansion_steps:
            if len(selected_safe) >= n_safe and len(selected_target) >= n_target and len(selected_reach) >= n_reach:
                break
            expanded_pool = match_by_grade(
                grade=grade,
                score_type="등급",
                grade_type=grade_type,
                region=_exp_region,
                major_keywords=major_keywords,
                track=track,
                risk_tolerance="all",
                min_result_year=min_result_year,
                exclude_special=exclude_special,
                student_suneung=student_suneung,
                limit=200,
            )
            exp_safe = [r for r in expanded_pool if r.get("verdict") == "안정"]
            exp_target = [r for r in expanded_pool if r.get("verdict") == "추천"]
            exp_reach = [r for r in expanded_pool if r.get("verdict") in ("도전", "도전(수능최저미충족)")]
            exp_safe.sort(key=lambda x: (x.get("tier", 5), x.get("cut_70") or 9.0))
            exp_target.sort(key=lambda x: (x.get("tier", 5), x.get("cut_70") or 9.0))
            exp_reach.sort(key=lambda x: (x.get("tier", 5), x.get("cut_70") or 9.0))

            if len(selected_safe) < n_safe:
                selected_safe += _pick_no_dup(exp_safe, n_safe - len(selected_safe))
            if len(selected_target) < n_target:
                selected_target += _pick_no_dup(exp_target, n_target - len(selected_target))
            if len(selected_reach) < n_reach:
                selected_reach += _pick_no_dup(exp_reach, n_reach - len(selected_reach))

            region_expanded = f"{region} → {_exp_region or '전국'}"

    # Build slim output fields
    def _slim(entries: list[dict]) -> list[dict]:
        out = []
        for e in entries:
            item: dict = {
                "university": e.get("university"),
                "tier": e.get("tier"),
                "region": e.get("region"),
                "department": e.get("department"),
                "process_name": e.get("process_name"),
                "cut_70": e.get("cut_70"),
                "verdict": e.get("verdict"),
            }
            if e.get("acceptance_pct") is not None:
                item["acceptance_pct"] = e["acceptance_pct"]
            if e.get("수능최저"):
                item["수능최저"] = e["수능최저"]
            if e.get("수능최저_충족") is not None:
                item["수능최저_충족"] = e["수능최저_충족"]
            out.append(item)
        return out

    result: dict = {
        "안정": _slim(selected_safe),
        "추천": _slim(selected_target),
        "도전": _slim(selected_reach),
        "summary": (
            f"총 {len(selected_safe)+len(selected_target)+len(selected_reach)}개 카드 배분: "
            f"안정 {len(selected_safe)}, 추천 {len(selected_target)}, 도전 {len(selected_reach)}"
        ),
        "note": "같은 대학은 카테고리 간 중복 없이 배분됨",
    }
    if region_expanded:
        result["note_region_expanded"] = (
            f"'{region}' 내 데이터 부족 → {region_expanded}으로 확장하여 추가 결과 포함"
        )
    if len(selected_safe) < n_safe:
        result["warning_safe"] = (
            f"안정권 결과 {n_safe}개 요청 중 {len(selected_safe)}개만 찾음 — "
            f"등급을 낮추거나 지역 범위를 넓히세요"
        )
    if len(selected_target) < n_target:
        result["warning_target"] = f"추천권 결과 {n_target}개 요청 중 {len(selected_target)}개만 찾음"
    if len(selected_reach) < n_reach:
        result["warning_reach"] = (
            f"도전권 결과 {n_reach}개 요청 중 {len(selected_reach)}개만 찾음 — "
            f"현재 성적 기준 도전권 입시결과 데이터가 부족합니다"
        )

    return result


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
