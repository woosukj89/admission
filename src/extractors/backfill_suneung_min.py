"""
Reusable framework for backfilling 수능최저학력기준 from extracted JSON files into the DB.

Usage:
    python backfill_suneung_min.py [--dry-run] [--university 강원대학교]

How it works:
1. Each university parser reads the extracted JSON (data/extracted/{uni}_수시_모집요강.json)
   and produces a list of Rule objects.
2. The matcher finds DB processes where process_name / dept attributes match the rule.
3. The updater patches admission_process.attributes.수능최저 with {있음, 원문, 조건}.
   Rules with exists=False will correct false-positive 있음=True records.

To add a new university:
    1. Write a parse_XXX(pages) function that returns list[Rule].
    2. Register it in UNIVERSITY_PARSERS dict below.
    3. Verify with --dry-run first.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from parse_suneung_min import parse_수능최저
except ImportError:
    def parse_수능최저(text: str) -> dict:  # type: ignore[misc]
        return {"있음": True, "원문": text[:200]}

DB_PATH = Path("data/admission.db")
EXTRACTED_DIR = Path("data/extracted")


# ─── Rule dataclass ───────────────────────────────────────────────────────────

@dataclass
class Rule:
    """One 수능최저 criterion that applies to a set of processes.

    Matching (all conditions ANDed; within each list: OR):
      - process_keywords: empty = match all process_names
      - college_keywords: empty = skip college filter
      - track_keywords:   empty = skip track filter
      - dept_keywords:    empty = skip dept name filter

    exists=False → mark record as 수능최저 미적용 (있음: False).
    """
    criteria_raw: str
    exists: bool = True                                         # False = 미적용 correction
    process_keywords: list[str] = field(default_factory=list)
    college_keywords: list[str] = field(default_factory=list)
    track_keywords:   list[str] = field(default_factory=list)
    dept_keywords:    list[str] = field(default_factory=list)
    exclude_process:  list[str] = field(default_factory=list)
    criteria_parsed:  Optional[dict] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def load_pages(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("pages", [])
    return data


def _clean(text: str) -> str:
    text = text.replace("\x01", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_criteria(raw: str) -> dict:
    """Parse raw criteria text. Falls back to {있음, 원문}.

    Prepends '수능최저 ' so parse_수능최저's keyword-detection step passes
    even when the criteria string doesn't itself contain '수능최저'.
    """
    if not raw.strip():
        return {"있음": True}
    result = parse_수능최저("수능최저 " + raw)
    if not result:
        result = {"있음": True}
    if not result.get("원문"):
        result["원문"] = _clean(raw[:200])
    return result


def _has_real_criteria(text: str) -> bool:
    """Return True if text contains actual grade criteria (not just '미적용')."""
    text_clean = text.lower()
    if "미적용" in text_clean and "이내" not in text_clean and "이상" not in text_clean:
        return False
    return bool(re.search(r"\d+\s*등급|\d합\d|이내|이상", text_clean))


# ─── University Parsers ───────────────────────────────────────────────────────

def _parse_kangwon_section(raw_section: str) -> list[tuple[list[str], int, str]]:
    """Parse 강원대 수능최저 table section from raw (newline-preserved) text.

    Returns list of (college_names, grade_sum, required_subjects).
    """
    # Find the table start (after "7. 수능최저학력기준")
    idx = raw_section.find("7. 수능최저학력기준")
    if idx == -1:
        idx = raw_section.find("수능최저학력기준\n캠퍼스")
        if idx == -1:
            idx = raw_section.find("수능최저학력기준\n")
    if idx == -1:
        return []

    section = raw_section[idx:]
    lines = [l.strip() for l in section.split("\n")]

    SKIP_LINES = {
        "7. 수능최저학력기준", "수능최저학력기준", "캠퍼스", "계열",
        "단과대학(모집단위)", "충족 등급", "필수반영 영역", "춘천", "삼척",
        "도계", "인문사회", "자연과학·공학·의학", "예체능", "삼척(도계포함)",
        "삼척캠퍼스", "도계캠퍼스",
    }
    DOMAIN_RE = re.compile(r"^(해당없음|과학탐구|수학|영어|국어|탐구|한국사|없음)")

    groups: list[tuple[list[str], int, str]] = []
    current_colleges: list[str] = []
    current_grade: int | None = None

    for line in lines:
        if not line or line in SKIP_LINES:
            continue
        if line.startswith("※") or line.startswith("·") or line.startswith("▶"):
            continue

        if re.match(r"^\d{1,2}$", line):
            current_grade = int(line)
        elif DOMAIN_RE.match(line) and current_grade is not None:
            groups.append((list(current_colleges), current_grade, line))
            current_colleges = []
            current_grade = None
        elif current_grade is not None:
            # Line between grade and domain — might be continuation
            # If it looks like another college, save group with "해당없음" default
            if any(suf in line for suf in ["대학", "학부", "학과"]):
                groups.append((list(current_colleges), current_grade, "해당없음"))
                current_colleges = [line]
                current_grade = None
        elif any(suf in line for suf in ["대학", "학부", "학과"]) and not re.match(r"^\d", line):
            current_colleges.append(line)

    return groups


def parse_kangwon(pages: list[dict]) -> list[Rule]:
    """강원대학교 — 수능최저 section embedded per-전형 page.

    Each 전형 page (학생부교과 전형들) has section "7. 수능최저학력기준" with
    a structured table: 캠퍼스 / 계열 / 단과대학 / 등급합 / 필수과목

    We extract grade-per-단과대학 groups and build one rule per group.
    Process types sharing the same grade structure get the same rules.
    """
    rules = []

    HEADING_TO_PROC: dict[str, list[str]] = {
        "학생부교과(일반교과전형)":      ["일반교과", "기회균형", "사회배려자", "농어촌", "특성화고교졸업자", "특수교육대상자", "영농창업인재"],
        "학생부교과(지역교과전형)":       ["지역교과", "저소득-지역교과"],
        "학생부교과(저소득-지역교과전형)": ["저소득-지역교과", "저소득"],
        "학생부종합(미래인재서류전형)":    ["미래인재서류"],
        "학생부종합(미래인재면접전형)":    ["미래인재면접"],
        "학생부종합(지역인재서류전형)":    ["지역인재서류"],
        "학생부종합(지역인재면접전형)":    ["지역인재면접"],
        "학생부종합(학·석사통합전형)":     ["학·석사통합"],
        "실기/실적(체육특기자전형)":       ["단체종목", "남자"],
        "실기/실적(실적우수자전형)":       ["실기/실적"],
    }

    for page in pages:
        raw_text = page.get("text", "") or ""
        text = _clean(raw_text)

        if len(text) < 300:
            continue

        # Skip 변경사항 pages (these describe changes, not actual criteria)
        if "변경 전(2025학년도)" in raw_text or "변경 후(2026학년도)" in raw_text:
            continue

        # Find which 전형 this page belongs to
        proc_keywords: list[str] = []
        for heading, kws in HEADING_TO_PROC.items():
            if heading in text:
                proc_keywords = kws
                break
        if not proc_keywords:
            continue

        # Find 수능최저 section
        sm_idx = raw_text.find("수능최저학력기준")
        if sm_idx == -1:
            continue

        section_raw = raw_text[sm_idx:sm_idx + 1500]
        section_clean = _clean(section_raw[:200])

        # 미적용
        if re.search(r"수능최저학력기준:\s*미적용", section_clean):
            rules.append(Rule(criteria_raw="미적용", exists=False, process_keywords=proc_keywords))
            continue

        # Parse the 단과대학 grade groups from raw text
        groups = _parse_kangwon_section(raw_text[sm_idx:sm_idx + 1500])

        if groups:
            for college_names, grade, required in groups:
                req_note = f", 필수: {required}" if required != "해당없음" else ""
                crit_raw = f"국, 수, 영, 탐구 상위 3개 영역 합 {grade}등급 이내{req_note}"
                parsed = _parse_criteria(crit_raw)
                # Create one rule per college name
                for cname in college_names:
                    # Strip parenthetical details for broader matching
                    base_name = re.sub(r"\(.*\)", "", cname).strip()
                    # Skip generic header text that leaked into college names
                    if base_name in ("단과대학", "단과대학(모집단위)", "모집단위", "캠퍼스", "계열"):
                        continue
                    if len(base_name) >= 3:
                        rules.append(Rule(
                            criteria_raw=crit_raw,
                            process_keywords=proc_keywords,
                            college_keywords=[base_name],
                            criteria_parsed=parsed,
                        ))
        else:
            # Fallback: partial application — store raw section as 원문
            snippet = _clean(section_raw[:500])
            if _has_real_criteria(snippet):
                parsed = _parse_criteria(snippet)
                rules.append(Rule(
                    criteria_raw=snippet,
                    process_keywords=proc_keywords,
                    criteria_parsed=parsed,
                ))

    # ── Hardcoded 삼척(도계포함) 캠퍼스 → 미적용 ────────────────────────────
    # These colleges are not in the 수능최저 table (삼척 캠퍼스 적용제외).
    SAMCHEOK_COLLEGES = ["보건과학대학", "인문사회대학", "공학대학", "디자인스포츠대학", "독립학부"]
    for cname in SAMCHEOK_COLLEGES:
        rules.append(Rule(
            criteria_raw="미적용 (삼척캠퍼스)",
            exists=False,
            college_keywords=[cname],
        ))

    # ── 특수교육대상자 / 체육특기자 전형 → 미적용 ────────────────────────────
    NOAPPLY_PROC = ["특수교육대상자", "단체종목", "남자", "실기/실적(실적우수자전형)", "실기우수자"]
    for pname in NOAPPLY_PROC:
        rules.append(Rule(
            criteria_raw="미적용",
            exists=False,
            process_keywords=[pname],
        ))

    # ── 특정 학과 dept-based fallbacks (college=None 케이스) ─────────────────
    # 간호학과: 미래인재서류/저소득/저소득-지역교과 → 11등급
    rules.append(Rule(
        criteria_raw="국, 수, 영, 탐구 상위 3개 영역 합 11등급 이내",
        process_keywords=["미래인재서류", "저소득", "저소득-지역교과"],
        dept_keywords=["간호대학", "간호학과"],
        criteria_parsed=_parse_criteria("국, 수, 영, 탐구 상위 3개 영역 합 11등급 이내"),
    ))
    # 의과대학(의예과): 미래인재서류/저소득/지역인재면접 → 5등급
    rules.append(Rule(
        criteria_raw="국, 수, 영, 탐구 상위 3개 영역 합 5등급 이내, 필수: 수학, 과학탐구(1과목)",
        process_keywords=["미래인재서류", "저소득", "저소득-지역교과", "지역인재면접"],
        dept_keywords=["의과대학", "의예과"],
        criteria_parsed=_parse_criteria("국, 수, 영, 탐구 상위 3개 영역 합 5등급 이내"),
    ))
    # 약학대학/수의과대학: 저소득 → 7등급
    rules.append(Rule(
        criteria_raw="국, 수, 영, 탐구 상위 3개 영역 합 7등급 이내, 필수: 수학, 과학탐구(1과목)",
        process_keywords=["저소득", "저소득-지역교과"],
        dept_keywords=["약학대학", "약학과", "수의과대학", "수의학과"],
        criteria_parsed=_parse_criteria("국, 수, 영, 탐구 상위 3개 영역 합 7등급 이내"),
    ))
    # 약학대학(약학과): 미래인재서류 → 9등급 (계열 속성만 있고 단과대학 없는 레코드 대응)
    rules.append(Rule(
        criteria_raw="국어, 수학, 영어, 탐구(1과목) 중 3개 영역 합 9등급 이내 (수학, 과학탐구 필수)",
        process_keywords=["미래인재서류"],
        dept_keywords=["약학대학", "약학과"],
        criteria_parsed=_parse_criteria("국어, 수학, 영어, 탐구(1과목) 중 3개 영역 합 9등급 이내"),
    ))

    return rules


def parse_pukyeong(pages: list[dict]) -> list[Rule]:
    """국립부경대학교 — all special-type processes are 미적용.

    Only 교과성적우수인재/일반전형/지역혁신인재전형 has 수능최저.
    Special types (농어촌인재, 미래인재, 학교생활우수인재, etc.) are all 미적용.
    """
    rules = []

    # Per-전형 page analysis — ALL names that may appear on a page
    PROC_HEADING_MAP = {
        "실기우수인재전형":       "실기우수인재",
        "학교생활우수인재전형":   "학교생활우수인재",
        "사회적배려대상자":       "사회적배려대상자",
        "농어촌인재전형":         "농어촌인재",
        "미래인재전형":           "미래인재",
        "특성화고교인재전형":     "특성화고교인재",
        "특수교육대상자전형":     "특수교육대상자",
    }

    # Criteria for 교과 전형 by 계열
    TRACK_CRITERIA = {
        "인문·사회": "국어 포함 2개 영역 합 8등급 이내 (탐구 1과목 적용)",
        "자연":       "수학 포함 2개 영역 합 9등급 이내 (탐구 1과목 적용, 확률과통계 선택 시 1등급 하향)",
        "예능":       "2개 영역 합 8등급 이내 (탐구 1과목 적용)",
        "공통":       "국어 포함 2개 영역 합 8등급 이내 또는 수학 포함 2개 영역 합 9등급 이내",
        "계열":       "계열별 기준 적용 (인문·사회계: 국어포함 2합 8이내, 자연계: 수학포함 2합 9이내)",
    }

    for page in pages:
        raw_text = page.get("text", "") or ""
        text = _clean(raw_text)

        sm_idx = text.find("수능최저학력기준")
        if sm_idx == -1:
            continue
        section = text[sm_idx:sm_idx + 200]

        # If 미적용, find ALL 전형 names on this page (a page can cover multiple 전형)
        if "미적용" in section[:60]:
            for heading, pname in PROC_HEADING_MAP.items():
                if heading in text:
                    rules.append(Rule(
                        criteria_raw="미적용",
                        exists=False,
                        process_keywords=[pname],
                    ))

    # Add criteria rules for 교과성적우수인재 by 계열
    for track_key, crit_raw in TRACK_CRITERIA.items():
        parsed = _parse_criteria(crit_raw)
        rules.append(Rule(
            criteria_raw=crit_raw,
            process_keywords=["교과성적우수인재", "일반전형", "지역혁신인재"],
            track_keywords=[track_key],
            criteria_parsed=parsed,
        ))

    return rules


def parse_daejeon(pages: list[dict]) -> list[Rule]:
    """대전대학교 — 수능최저 summary (page 9, index 8).

    The extracted tables for this page are empty; criteria are in the page text.
    Structure (3 rows):
      교과면접/교과중점/농어촌학생 → 한의예과 → 3개합5이내
      지역인재Ⅰ·Ⅱ/혜화인재 → [없음] → 3개합6이내
      군사학과 → 군사학과 → 1개5이내
    """
    # Hardcoded from 2026학년도 대전대학교 수시모집요강 p.9
    rules = [
        Rule(
            criteria_raw="국어, 수학, 영어, 탐구(사회/과학) 중 3개 영역 합 5등급 이내 (국어·수학·영어 각 4등급 이내)",
            process_keywords=["교과면접", "교과중점", "농어촌학생"],
            dept_keywords=["한의예과"],
            criteria_parsed=_parse_criteria(
                "국어, 수학, 영어, 탐구 중 3개 영역 합 5등급 이내"
            ),
        ),
        Rule(
            criteria_raw="국어, 수학, 영어, 탐구(사회/과학) 중 3개 영역 합 6등급 이내 (국어·수학·영어 각 4등급 이내)",
            process_keywords=["지역인재", "혜화인재"],
            dept_keywords=["한의예과"],
            criteria_parsed=_parse_criteria(
                "국어, 수학, 영어, 탐구 중 3개 영역 합 6등급 이내"
            ),
        ),
        Rule(
            criteria_raw="국어, 수학, 영어 3개 영역 중 1개 5등급 이내",
            process_keywords=["군사학과"],
            criteria_parsed=_parse_criteria(
                "국어, 수학, 영어 3개 영역 중 1개 5등급 이내"
            ),
        ),
    ]

    # Non-한의예과 records for 교과면접/교과중점/농어촌학생 etc. are 미적용
    # Add lower-priority catch-all 미적용 rules for each main process
    # (specific rules above take priority because they're checked first)
    NON_HANUUI_PROC = ["교과면접", "교과중점", "농어촌학생", "지역인재", "혜화인재"]
    for pn in NON_HANUUI_PROC:
        rules.append(Rule(
            criteria_raw="미적용 (한의예과/군사학과 제외 모집단위)",
            exists=False,
            process_keywords=[pn],
        ))

    # Known 미적용 전형 (includes 지역인재Ⅰ/Ⅱ variants via substring match)
    NOAPPLY_PROC = ["고른기회", "특성화고교졸업자", "실기위주", "기회균형",
                    "실기고사", "입상실적", "농어촌전형"]
    for pn in NOAPPLY_PROC:
        rules.append(Rule(criteria_raw="미적용", exists=False, process_keywords=[pn]))

    # Garbage process names from PDF extraction artifacts
    GARBAGE_PROC = ["후보", "모집", "수능최저학력기준", "교과성적"]
    for pn in GARBAGE_PROC:
        rules.append(Rule(criteria_raw="미적용 (파싱오류)", exists=False, process_keywords=[pn]))

    return rules


def parse_kyungsang(pages: list[dict]) -> list[Rule]:
    """경상국립대학교 — 수능최저.

    In the DB, 경상대 process names are directly the criteria codes ('2합10', '2합8')
    or special admissions type names ('농어촌', '사회통합', '특성화', etc.).

    Special types (농어촌, 사회통합, 특성화, 국가보훈, 장애인, 재직자, 평생학습)
    are 미적용 — they were false-positively tagged 있음=True.

    Base note: ※ 국어, 영어, 수학, 탐구(사탐/과탐 중 1과목) 상위 2개 영역 합, 한국사 응시 필수
    """
    NOTE = "(국, 영, 수, 탐구 상위 2개 영역, 탐구: 1과목, 한국사 응시 필수)"
    rules = []

    # Criteria rules keyed by process_name (which IS the criteria code)
    PROC_CRITERIA = {
        "2합10": f"상위 2개 영역 합 10등급 이내 {NOTE}",
        "2합8":  f"상위 2개 영역 합 8등급 이내 {NOTE}",
        "9등급": f"상위 2개 영역 9등급 이내 {NOTE}",
        "실기":  f"상위 2개 영역 합 10등급 이내 {NOTE}",  # 실기 uses same criteria as 교과
    }

    for proc_kw, crit_raw in PROC_CRITERIA.items():
        rules.append(Rule(
            criteria_raw=crit_raw,
            process_keywords=[proc_kw],
            criteria_parsed=_parse_criteria(crit_raw),
        ))

    # Special admissions → 미적용 (false-positive correction)
    NOAPPLY = ["농어촌", "사회통합", "특성화", "국가보훈", "장애인등", "장애인", "재직자", "평생학습"]
    for proc_kw in NOAPPLY:
        rules.append(Rule(
            criteria_raw="미적용",
            exists=False,
            process_keywords=[proc_kw],
        ))

    return rules


def parse_jeonbuk(pages: list[dict]) -> list[Rule]:
    """전북대학교 — text table by 모집단위 group × 전형.

    Criteria from page 10 (page_idx=9) text table.
    Process types: 큰사람(학생부종합), 지역인재, SW인재, 기회균형선발, 사회통합 etc.
    """
    rules = []

    # Fixed criteria by 모집단위 group (2026학년도 기준)
    # All process types apply the same criteria unless specified
    GROUP_RULES = [
        # (dept_keywords, process_keywords, criteria_raw)
        # 인문계열: 인문대학 소속 개별 학과들 포함
        (["인문계열", "사회과학대", "경상대", "경상계열", "인문대", "사회과학계열",
          "국어국문", "영어영문", "독일학", "스페인", "프랑스", "일본학", "중어중문",
          "사학과", "철학", "국제학부", "문헌정보"],
         [],
         "국어, 수학, 영어, 탐구 중 2개 영역 합 8등급 이내 (한국사 필수 응시)"),

        # 생활과학대 + 환경생명자원대 계열
        (["생활과학대학", "생활과학계열", "환경생명자원대학", "환경생명자원계열",
          "생명자원융합"],
         [],
         "국어, 수학, 영어, 탐구 중 2개 영역 합 8등급 이내 (한국사 필수 응시)"),

        (["융합자율전공학부"],
         [],
         "국어, 수학, 영어, 탐구 중 2개 영역 합 7등급 이내 (한국사 필수 응시)"),

        # 간호 (개별 학과명 + 대학명 모두 포함)
        (["간호학과", "간호대학"],
         [],
         "국어, 수학, 영어, 탐구 중 2개 영역 합 6등급 이내 (한국사 필수 응시)"),

        (["국어교육과", "영어교육과"],
         [],
         "국어, 수학, 영어, 탐구 중 2개 영역 합 6등급 이내 (한국사 필수 응시)"),

        (["공학계열", "공과대학", "국제이공학부", "이차전지공학과", "첨단방위산업학과"],
         [],
         "수학 포함 2개 영역 합 8등급 이내 (한국사 필수 응시)"),

        (["농업생명과학계열", "농업생명과학대학", "스마트팜"],
         [],
         "수학 포함 2개 영역 합 8등급 이내 (한국사 필수 응시)"),

        (["자연과학계열", "자연과학대학"],
         [],
         "수학 포함 2개 영역 합 8등급 이내 (한국사 필수 응시)"),

        (["수학교육과"],
         [],
         "수학 포함 2개 영역 합 7등급 이내 (수학 3등급 이내 필수)"),

        (["과학교육학부"],
         [],
         "수학, 과학탐구 2개 영역 합 10등급 이내"),

        # 수의과 (수의예과 학과 + 수의과대학 모두)
        (["수의예과", "수의과대학"],
         [],
         "수학 포함 3개 영역 합 7등급 이내 (한국사 필수 응시)"),

        # 약학 (약학과 + 약학대학)
        (["약학과", "약학대학"],
         [],
         "수학 포함 3개 영역 합 7등급 이내 (한국사 필수 응시)"),

        (["의예과"],
         ["일반학생"],
         "수학 포함 4개 영역 합 5등급 이내 (탐구 2과목 평균 절사)"),

        (["의예과"],
         ["큰사람", "지역인재기회균형", "지역인재"],
         "수학 포함 4개 영역 합 6등급 이내 (탐구 2과목 평균 절사)"),

        # 치의예과 + 치과대학
        (["치의예과", "치과대학"],
         [],
         "수학 포함 4개 영역 합 7등급 이내 (한국사 필수 응시)"),

        (["한의예과"],
         [],
         "수학 또는 과학탐구 포함 4개 영역 합 7등급 이내"),

        # 사범대학: 교육학과 외 모든 교육 관련 학과
        (["사범대학", "교육학과", "사회과교육", "독어교육", "체육교육", "미술교육",
          "음악교육", "한문교육", "역사교육", "유아교육", "윤리교육", "지리교육",
          "생물교육", "화학교육", "물리교육", "일어교육"],
         [],
         "국어, 수학, 영어, 탐구 중 2개 영역 합 8등급 이내 (한국사 필수 응시)"),

        # 예체능 계열 (미술, 무용, 스포츠) — 수능최저 미적용으로 처리
        (["미술학과", "무용학과", "스포츠과학"],
         [],
         "미적용"),

        # 한옥학과 정원외 — 기회균형/특수교육 정원외 전형은 수능최저 미적용
        (["한옥학과"],
         [],
         "미적용"),
    ]

    for dept_kws, proc_kws, crit_raw in GROUP_RULES:
        if crit_raw == "미적용":
            rules.append(Rule(
                criteria_raw="미적용",
                exists=False,
                process_keywords=proc_kws,
                dept_keywords=dept_kws,
            ))
        else:
            parsed = _parse_criteria(crit_raw)
            rules.append(Rule(
                criteria_raw=crit_raw,
                process_keywords=proc_kws,
                dept_keywords=dept_kws,
                criteria_parsed=parsed,
            ))

    return rules


def parse_korea_sejong(pages: list[dict]) -> list[Rule]:
    """고려대학교(세종) — 수능최저학력기준 없음 (모집요강에 수능최저 언급 없음).

    All 280 unparsed records are false-positive 있음=True; correct them to 미적용.
    """
    # Single catch-all 미적용 rule (no filter = matches everything unparsed)
    return [Rule(criteria_raw="미적용 (수능최저 없음)", exists=False)]


def parse_hanseo(pages: list[dict]) -> list[Rule]:
    """한서대학교 — 수능최저학력기준은 적용하지 않습니다 (모집요강 명시).

    All 247 unparsed records → 미적용.
    """
    return [Rule(criteria_raw="미적용 (수능최저학력기준은 적용하지 않습니다)", exists=False)]


def parse_jeju(pages: list[dict]) -> list[Rule]:
    """제주대학교 — 학생부종합 전형 전체 미적용; 학생부교과 전형은 이미 파싱됨.

    Unparsed records are all 학생부종합 types (서류형/면접형/소프트웨어인재 등) → 미적용.
    """
    NOAPPLY_PROC = [
        "일반학생1", "일반학생2", "소프트웨어인재", "특수교육대상자",
        "특성화고졸업자", "농어촌학생", "사회통합", "재직자", "평생학습자",
        "고른기회", "체육특기자", "실기",
    ]
    rules = []
    for kw in NOAPPLY_PROC:
        rules.append(Rule(
            criteria_raw="미적용 (학생부종합/특별전형)",
            exists=False,
            process_keywords=[kw],
        ))
    # catch-all fallback for any remaining false-positives
    rules.append(Rule(criteria_raw="미적용", exists=False))
    return rules


def parse_chongju(pages: list[dict]) -> list[Rule]:
    """청주대학교 — 항공운항학과만 수능최저 있음 (상위 2합8), 나머지 미적용.

    Criteria text: "국어, 영어, 수학, 탐구(사탐/과탐) 상위 2개 영역의 등급 합 8등급 이내"
    """
    NOTE = "(수학 미적분 응시자는 수학 취득등급 1등급 상향; 탐구 2과목 평균)"
    rules = [
        # 항공운항학과: 2합8
        Rule(
            criteria_raw=f"국어, 영어, 수학, 탐구 상위 2개 영역의 등급 합 8등급 이내 {NOTE}",
            dept_keywords=["항공운항학과"],
        ),
        # 모든 다른 학과 → 미적용
        Rule(criteria_raw="미적용", exists=False),
    ]
    return rules


def parse_konkuk(pages: list[dict]) -> list[Rule]:
    """건국대학교 — KU논술우수자만 수능최저 있음 (이미 파싱됨); 나머지 미적용.

    Unparsed: KU지역균형, KU자기추천, 특수교육대상자, 기회균형, 특성화고교졸업자, KU체육특기자 → 미적용
    """
    NOAPPLY_PROC = [
        "KU지역균형", "KU자기추천", "특수교육대상자", "기회균형",
        "특성화고교졸업자", "특성화고졸재직자", "KU체육특기자", "KU연기우수자",
    ]
    rules = []
    for kw in NOAPPLY_PROC:
        rules.append(Rule(
            criteria_raw="미적용",
            exists=False,
            process_keywords=[kw],
        ))
    rules.append(Rule(criteria_raw="미적용", exists=False))
    return rules


def parse_chosun(pages: list[dict]) -> list[Rule]:
    """조선대학교 — 수능최저 기준표 직접 작성 (2026학년도).

    수시모집 수능최저:
    - 전체(미술/체육대학 제외) 일반전형: 1합6
    - 사범대학 일반전형: 2합10
    - 미술대학, 체육대학: 미적용
    - 의예과/치의예과 일반/지역인재/면접/서류전형: 3합5 (수학 의무)
    - 의예과/치의예과 지역기회균형/농어촌: 3합6 (수학 의무)
    - 약학과 일반/지역인재/면접/서류전형: 3합6 (수학 의무)
    - 약학과 지역기회균형/농어촌: 3합7 (수학 의무)
    - 간호학과 일반/지역인재전형: 2합6
    - 표기되지 않은 전형 → 미적용
    """
    NOTE = "(국, 수(미적분/기하 택1), 영, 과탐 1과목; 수학 의무 반영; 한국사 미반영)"

    rules = [
        # 사회통합, 장애인등, 실기, 기초생활류 → 미적용
        Rule(criteria_raw="미적용 (미기재 전형)", exists=False,
             process_keywords=["사회통합"]),
        Rule(criteria_raw="미적용 (미기재 전형)", exists=False,
             process_keywords=["장애인등"]),
        Rule(criteria_raw="미적용 (미기재 전형)", exists=False,
             process_keywords=["실기"]),
        # 미술대학/체육대학 계열 → 미적용 (all processes)
        Rule(criteria_raw="미적용 (미술대학·체육대학)", exists=False,
             track_keywords=["예능", "체능", "예체능"]),
        # 지역인재: 미술/체육계 + unlisted depts → 미적용
        Rule(criteria_raw="미적용 (미기재 모집단위)", exists=False,
             process_keywords=["지역인재"],
             dept_keywords=["미술학부", "문화콘텐츠학부", "라이프스타일디자인학부",
                            "시각디자인학과", "디자인공학과"]),
        Rule(criteria_raw="미적용 (미기재 모집단위)", exists=False,
             process_keywords=["지역인재"],
             dept_keywords=["체육학과", "스포츠산업학과", "태권도학과", "공연예술무용과"]),
        Rule(criteria_raw="미적용 (미기재 모집단위)", exists=False,
             process_keywords=["지역인재"],
             dept_keywords=["자유전공학부", "미래융합학부", "만화"]),
        # 의예과/치의예과 지역인재 → 3합5 수학 의무
        Rule(
            criteria_raw=f"국, 수, 영, 과탐 1과목 중 3개 영역 합 5등급 이내 {NOTE}",
            process_keywords=["지역인재"],
            dept_keywords=["의예과", "치의예과"],
        ),
        # 간호학과 지역인재 → 2합6
        Rule(
            criteria_raw="국, 수, 영, 탐(사회/과학 1과목) 중 2개 영역 합 6등급 이내",
            process_keywords=["지역인재"],
            dept_keywords=["간호학과"],
        ),
        # 약학과 지역인재 → 3합6 수학 의무
        Rule(
            criteria_raw=f"국, 수, 영, 과탐 1과목 중 3개 영역 합 6등급 이내 {NOTE}",
            process_keywords=["지역인재"],
            dept_keywords=["약학과"],
        ),
        # 일반전형: 의예과/치의예과 → 3합5 수학 의무
        Rule(
            criteria_raw=f"국, 수, 영, 과탐 1과목 중 3개 영역 합 5등급 이내 {NOTE}",
            process_keywords=["일반"],
            dept_keywords=["의예과", "치의예과"],
        ),
        # 일반전형: 간호학과 → 2합6
        Rule(
            criteria_raw="국, 수, 영, 탐(사회/과학 1과목) 중 2개 영역 합 6등급 이내",
            process_keywords=["일반"],
            dept_keywords=["간호학과"],
        ),
        # 일반전형: 약학과 → 3합6 수학 의무
        Rule(
            criteria_raw=f"국, 수, 영, 과탐 1과목 중 3개 영역 합 6등급 이내 {NOTE}",
            process_keywords=["일반"],
            dept_keywords=["약학과"],
        ),
        # 일반전형: 사범대학 (교육과) → 2합10
        Rule(
            criteria_raw="국, 수, 영, 탐(사회/과학 1과목) 중 2개 영역 합 10등급 이내",
            process_keywords=["일반"],
            dept_keywords=["교육과", "교육학과", "사범대학"],
        ),
        # 일반전형: 나머지 일반학과 → 1합6
        Rule(
            criteria_raw="국, 수, 영, 탐(사회/과학 1과목) 중 1개 영역 6등급 이내",
            process_keywords=["일반"],
        ),
    ]
    return rules


def parse_sogang(pages: list[dict]) -> list[Rule]:
    """서강대학교 — 수시 학생부종합 전형은 수능최저 없음.

    서강가치전형, 기회균형전형: 미적용 (전형요약 '미적용' 명시).
    """
    return [Rule(criteria_raw="미적용 (학생부종합전형 수능최저 없음)", exists=False)]


def parse_eulji(pages: list[dict]) -> list[Rule]:
    """을지대학교 — EU서류형/EU면접형 수능최저 미적용.

    전형요약 수능[최저] 열이 공란(·)으로 미적용.
    """
    return [Rule(criteria_raw="미적용 (EU학생부종합전형 수능최저 없음)", exists=False)]


def parse_knue(pages: list[dict]) -> list[Rule]:
    """한국교원대학교 — 사회통합/기초수급 및 차상위계층 전형: 미적용.

    전형요약 수능최저 열에 X 표시. '반영영역', '반영방법' 등 garbage process names 포함.
    """
    return [Rule(criteria_raw="미적용 (사회배려/정원외 전형)", exists=False)]


def parse_dgu_wise(pages: list[dict]) -> list[Rule]:
    """동국대학교(WISE) — 기회균형Ⅱ(정원 외), 특성화고교졸업자 전형: 미적용.

    기회균형Ⅱ = 국민기초생활수급자/차상위계층 대상 정원 외 전형 → 수능최저 없음.
    특성화고교졸업자, 종목(garbage) 포함.
    """
    return [Rule(criteria_raw="미적용 (기회균형Ⅱ/특성화고/정원외 전형)", exists=False)]


def parse_pusan(pages: list[dict]) -> list[Rule]:
    """부산대학교 — 특수교육대상자전형, 6년과정이수자: 미적용.

    전형요약 수능최저 열에 × 표시.
    """
    return [Rule(criteria_raw="미적용 (특수교육대상자/정원외 전형)", exists=False)]


def parse_kosin(pages: list[dict]) -> list[Rule]:
    """고신대학교 — 서류/면접/학생부교과/우선순위/학생부교과성적 전형: 추출 오류.

    Process names are garbage (too short or truncated). All → exists=False.
    """
    return [Rule(criteria_raw="미적용 (추출 오류 또는 수능최저 없음)", exists=False)]


def parse_sungkyunkwan(pages: list[dict]) -> list[Rule]:
    """성균관대학교 — 수시 수능최저 없음 (JSON에 '수능최저' 언급 전무).

    성균인재, 융합형, 기회균형, 탐구형 모두 미적용.
    """
    return [Rule(criteria_raw="미적용 (수능최저 없음)", exists=False)]


def parse_soonchunhyang(pages: list[dict]) -> list[Rule]:
    """순천향대학교 — 특기자/평생학습자/서해5도학생/수능/실기/출석 등 모두 false-positive.

    교과우수자/충청형지역인재 전형은 이미 파싱됨.
    나머지 unparsed process names (특기자, 평생학습자, 서해5도학생, 수능, 실기, 출석,
    최저학력기준, 면접평가 등)은 추출 오류이거나 수능최저 미적용 전형임.
    """
    return [Rule(criteria_raw="미적용 (수능최저 미적용 또는 추출 오류)", exists=False)]


def parse_cnu(pages: list[dict]) -> list[Rule]:
    """충남대학교 — 수능최저 전형별/단과대학별 상세 기준.

    고른기회, 체육특기자 → 미적용 (전형요약 ×).
    지역인재전형 → 단과대학/모집단위별 복잡한 기준.
    """
    MED = "국어, 영어, 과학탐구(2과목 평균) 중 상위 2개영역과 수학(미적분, 기하) 합산"

    rules = [
        # 고른기회 특별전형: × (전형요약 미적용)
        Rule(criteria_raw="미적용 (고른기회 특별전형)", exists=False,
             process_keywords=["고른기회"]),
        # 체육특기자: × (실기/실적전형, 미적용)
        Rule(criteria_raw="미적용 (체육특기자전형)", exists=False,
             process_keywords=["체육특기자"]),
        # 지역인재: 예체능계 track → 미적용
        Rule(criteria_raw="미적용 (예체능계)", exists=False,
             process_keywords=["지역인재"],
             track_keywords=["예", "체능"]),
        # 지역인재: 국가안보전공 → 별도 전형 (미적용)
        Rule(criteria_raw="미적용 (국가안보전형 별도 적용)", exists=False,
             process_keywords=["지역인재"],
             dept_keywords=["국토안보학전공", "해양안보학전공"]),
        # 지역인재: 의과대학 → 2합4 (수학 의무)
        Rule(criteria_raw=f"{MED} 4등급 이내",
             process_keywords=["지역인재"],
             dept_keywords=["의예과"]),
        # 지역인재: 수의과대학 → 2합6 (수학 의무)
        Rule(criteria_raw=f"{MED} 6등급 이내",
             process_keywords=["지역인재"],
             dept_keywords=["수의예과"]),
        # 지역인재: 약학대학 → 2합5 (수학 의무)
        Rule(criteria_raw=f"{MED} 5등급 이내",
             process_keywords=["지역인재"],
             dept_keywords=["약학과"]),
        # 지역인재: 사범대학(국어교육/영어교육/교육학과) → 3합9
        Rule(criteria_raw="국어, 영어, 수학, 탐구(1과목) 중 상위 3개영역 합산 9등급 이내",
             process_keywords=["지역인재"],
             dept_keywords=["교육학과", "국어교육과", "영어교육과"]),
        # 지역인재: 자연계 전공자율선택제 (공학/자연과학융합) → 3합12 (과탐)
        Rule(criteria_raw="국어, 영어, 수학, 과학탐구(1과목) 중 상위 3개영역 합산 12등급 이내",
             process_keywords=["지역인재"],
             dept_keywords=["공학융합학부", "자연과학융합학부", "농생명융합학부", "자율전공융합학부",
                            "생명정보융합학과"]),
        # 지역인재: 인문사회융합학부 → 3합11
        Rule(criteria_raw="국어, 영어, 수학, 탐구(1과목) 중 상위 3개영역 합산 11등급 이내",
             process_keywords=["지역인재"],
             dept_keywords=["인문사회융합학부"]),
        # 지역인재: 자연계 나머지 → 3합12 (과탐)
        Rule(criteria_raw="국어, 영어, 수학, 과학탐구(1과목) 중 상위 3개영역 합산 12등급 이내",
             process_keywords=["지역인재"],
             track_keywords=["자연"]),
        # 지역인재: 인문계 나머지 → 3합11
        Rule(criteria_raw="국어, 영어, 수학, 탐구(1과목) 중 상위 3개영역 합산 11등급 이내",
             process_keywords=["지역인재"]),
    ]
    return rules


def parse_gachon(pages: list[dict]) -> list[Rule]:
    """가천대학교 — 농어촌/특성화고졸재직자 전형.

    농어촌(교과): 의예과 3합4, 한의예과 2개 영역 각 1등급, 약학과 3합6; 나머지 미적용.
    농어촌(종합): 의예과 3합4만; 나머지 미적용.
    특성화고졸재직자: 미적용.
    """
    NOTE = "(과학탐구 2과목 평균 소수점 절사; 수학은 기하/미적분 필수)"

    rules = [
        # 특성화고졸재직자 → 미적용
        Rule(criteria_raw="미적용 (특성화고졸재직자전형)", exists=False,
             process_keywords=["특성화고졸재직자"]),
        # 농어촌(교과): 의예과 → 3합4
        Rule(criteria_raw=f"국어, 수학(기하/미적분), 영어, 과학탐구(2과목) 중 상위 3개 영역 합 4등급 이내 {NOTE}",
             process_keywords=["농어촌(교과)"],
             dept_keywords=["의예과"]),
        # 농어촌(교과): 한의예과 → 2개 영역 각 1등급
        Rule(criteria_raw=f"국어, 수학(기하/미적분), 영어, 과학탐구(2과목) 중 2개 영역 각 1등급 {NOTE}",
             process_keywords=["농어촌(교과)"],
             dept_keywords=["한의예과"]),
        # 농어촌(교과): 약학과 → 3합6
        Rule(criteria_raw=f"국어, 수학(기하/미적분), 영어, 과학탐구(2과목) 중 상위 3개 영역 합 6등급 이내 {NOTE}",
             process_keywords=["농어촌(교과)"],
             dept_keywords=["약학과"]),
        # 농어촌(교과): 나머지 → 미적용
        Rule(criteria_raw="미적용 (의/한의/약학과 외 농어촌)", exists=False,
             process_keywords=["농어촌(교과)"]),
        # 농어촌(종합): 의예과 → 3합4
        Rule(criteria_raw=f"국어, 수학(기하/미적분), 영어, 과학탐구(2과목) 중 상위 3개 영역 합 4등급 이내 {NOTE}",
             process_keywords=["농어촌(종합)"],
             dept_keywords=["의예과"]),
        # 농어촌(종합): 나머지 → 미적용
        Rule(criteria_raw="미적용 (의예과 외 농어촌종합)", exists=False,
             process_keywords=["농어촌(종합)"]),
    ]
    return rules


def parse_cbnu(pages: list[dict]) -> list[Rule]:
    """충북대학교 — 미적용 전형 및 예체능계 학생부교과 수능최저 보정.

    국가보훈대상자, 특성화고출신자, 특성화고졸재직자 → 미적용 (전형요약 미표기).
    학생부교과 예체능계(미술/디자인/체육교육) → 미적용 (수능최저학력기준 표에 미기재).
    '충북대학교' process name, '*' dept name → garbage, 미적용.
    """
    rules = [
        Rule(criteria_raw="미적용 (국가보훈대상자전형)", exists=False,
             process_keywords=["국가보훈대상자"]),
        Rule(criteria_raw="미적용 (특성화고출신자전형)", exists=False,
             process_keywords=["특성화고출신자"]),
        Rule(criteria_raw="미적용 (특성화고졸재직자전형)", exists=False,
             process_keywords=["특성화고졸재직자"]),
        # 학생부교과 예체능계 → 미적용
        Rule(criteria_raw="미적용 (예체능계 수능최저 미기재)", exists=False,
             track_keywords=["예", "체능"]),
        # 이상한 process name '충북대학교' → garbage
        Rule(criteria_raw="미적용 (추출 오류)", exists=False,
             process_keywords=["충북대학교"]),
        # Catch-all (dept='*' garbage)
        Rule(criteria_raw="미적용 (추출 오류)", exists=False),
    ]
    return rules


def parse_soonsil(pages: list[dict]) -> list[Rule]:
    """숭실대학교 — 기회균형(2합5), 논술우수자(2합6, 자연계 수학의무).

    기회균형전형: 인문/경상/자연 계열 모두 국어·수학·영어·탐구(1과목) 중 2개 합 5등급.
    논술우수자전형:
      - 인문·경상·계열 track: 2개 합 6등급 (수학 택1, 사/과탐 1과목)
      - 자연 track: 2개 합 6등급 (수학 미적분/기하 필수, 과탐 1과목)
    """
    rules = [
        # 기회균형 (모든 계열 동일)
        Rule(
            criteria_raw="국어, 수학, 영어, 사회/과학탐구(1과목) 중 2개 영역 합 5등급 이내",
            process_keywords=["기회균형"],
        ),
        # 논술우수자: 자연계
        Rule(
            criteria_raw="국어, 수학(미적분/기하), 영어, 과학탐구(1과목) 중 2개 영역 합 6등급 이내",
            process_keywords=["논술우수자"],
            track_keywords=["자연"],
        ),
        # 논술우수자: 인문/경상/계열 나머지
        Rule(
            criteria_raw="국어, 수학, 영어, 사회/과학탐구(1과목) 중 2개 영역 합 6등급 이내",
            process_keywords=["논술우수자"],
        ),
    ]
    return rules


def parse_kbu(pages: list[dict]) -> list[Rule]:
    """건국대학교(글로컬) — '센터' 등 garbage process name 추출 오류."""
    return [Rule(criteria_raw="미적용 (추출 오류)", exists=False)]


def parse_knu(pages: list[dict]) -> list[Rule]:
    """경북대학교 — 실기고사/서류제출은 예체능 전형 또는 garbage 프로세스명."""
    return [Rule(criteria_raw="미적용 (실기전형 또는 추출 오류)", exists=False)]


def parse_kmou(pages: list[dict]) -> list[Rule]:
    """국립한국해양대학교 — 특성화고등을졸업한재직자 정원외 전형은 수능최저 없음."""
    return [Rule(criteria_raw="미적용 (특성화고졸재직자 정원외 전형)", exists=False)]


def parse_mokwon(pages: list[dict]) -> list[Rule]:
    """목원대학교 — '영역' 등 garbage process name 추출 오류."""
    return [Rule(criteria_raw="미적용 (추출 오류)", exists=False)]


def parse_snu(pages: list[dict]) -> list[Rule]:
    """서울대학교 — '1단계실기평가' 등은 음악대학 예체능 실기 단계 (수능최저 없음)."""
    return [Rule(criteria_raw="미적용 (예체능 실기전형 추출 오류)", exists=False)]


def parse_hknu(pages: list[dict]) -> list[Rule]:
    """한경국립대학교 — '학생부교과성적' 은 garbage column header."""
    return [Rule(criteria_raw="미적용 (추출 오류)", exists=False)]


def parse_tukorea(pages: list[dict]) -> list[Rule]:
    """한국공학대학교 — 조기취업형 계약학과는 수능최저 없음."""
    return [Rule(criteria_raw="미적용 (조기취업형 계약학과 수능최저 없음)", exists=False)]


def parse_koreatech(pages: list[dict]) -> list[Rule]:
    """한국기술교육대학교 — '경쟁률'/'지원인원' 은 garbage column headers."""
    return [Rule(criteria_raw="미적용 (추출 오류)", exists=False)]


def parse_hau(pages: list[dict]) -> list[Rule]:
    """한국항공대학교 — '1단계'/'고른기회전형'/'일반면접' 등 false-positive."""
    return [Rule(criteria_raw="미적용 (추출 오류 또는 수능최저 없음)", exists=False)]


def parse_hanyil(pages: list[dict]) -> list[Rule]:
    """한일장신대학교 — '학생부' garbage column header."""
    return [Rule(criteria_raw="미적용 (추출 오류)", exists=False)]


def parse_honam(pages: list[dict]) -> list[Rule]:
    """호남대학교 — '2단계'/'수능성적'/'우수'/'학생부100' 등 garbage process names."""
    return [Rule(criteria_raw="미적용 (추출 오류)", exists=False)]


def parse_geukdong(pages: list[dict]) -> list[Rule]:
    """극동대학교 — 수능최저학력기준 적용 안함 (모집요강 명시)."""
    return [Rule(criteria_raw="미적용 (수능최저학력기준 적용 안함)", exists=False)]


def parse_shingyeongju(pages: list[dict]) -> list[Rule]:
    """신경주대학교 — 전 전형 수능최저학력기준 미적용."""
    return [Rule(criteria_raw="미적용 (수능최저학력기준 미적용)", exists=False)]


def parse_hyupsung(pages: list[dict]) -> list[Rule]:
    """협성대학교 — 전 모집단위/전형 수능최저학력기준 미적용."""
    return [Rule(criteria_raw="미적용 (수능최저학력기준 미적용)", exists=False)]


def parse_ashin(pages: list[dict]) -> list[Rule]:
    """아신대학교 — 수능최저 언급 없음 (추출 오류)."""
    return [Rule(criteria_raw="미적용 (수능최저 언급 없음)", exists=False)]


def parse_nasaret(pages: list[dict]) -> list[Rule]:
    """나사렛대학교 — 전 전형 수능최저 미적용 (process_name '모집'은 추출 오류).

    간호학과 일반/농어촌/기초생활수급자 전형에 수능최저가 있으나
    DB 상 process_name이 '모집'으로 잘못 추출된 경우이므로 모두 미적용 처리.
    """
    return [Rule(criteria_raw="미적용 (추출 오류 또는 수능최저 없음)", exists=False)]


def parse_yonsei_mirae(pages: list[dict]) -> list[Rule]:
    """연세대학교(미래) — 특성화고교졸업자전형 수능최저 미적용."""
    return [Rule(criteria_raw="미적용 (특성화고교졸업자전형 수능최저 없음)", exists=False)]


def parse_woosong(pages: list[dict]) -> list[Rule]:
    """우송대학교 — 전 전형 수능최저학력기준 미적용."""
    return [Rule(criteria_raw="미적용 (수능최저학력기준 미적용)", exists=False)]


def parse_hwaseong(pages: list[dict]) -> list[Rule]:
    """화성의과학대학교 — 수능최저 언급 없음 (추출 오류)."""
    return [Rule(criteria_raw="미적용 (수능최저 언급 없음)", exists=False)]


# ─── Registry ─────────────────────────────────────────────────────────────────

UNIVERSITY_PARSERS: dict[str, tuple[str, object]] = {
    "강원대학교":       ("강원대학교_수시_모집요강.json",       parse_kangwon),
    "국립부경대학교":    ("국립부경대학교_수시_모집요강.json",    parse_pukyeong),
    "대전대학교":       ("대전대학교_수시_모집요강.json",       parse_daejeon),
    "경상국립대학교":    ("경상국립대학교_수시_모집요강.json",    parse_kyungsang),
    "전북대학교":       ("전북대학교_수시_모집요강.json",       parse_jeonbuk),
    "고려대학교(세종)":  ("고려대학교(세종)_수시_모집요강.json", parse_korea_sejong),
    "한서대학교":       ("한서대학교_수시_모집요강.json",       parse_hanseo),
    "제주대학교":       ("제주대학교_수시_모집요강.json",       parse_jeju),
    "청주대학교":       ("청주대학교_수시_모집요강.json",       parse_chongju),
    "건국대학교":       ("건국대학교_수시_모집요강.json",       parse_konkuk),
    "조선대학교":       ("조선대학교_수시_모집요강.json",       parse_chosun),
    "서강대학교":       ("서강대학교_수시_모집요강.json",       parse_sogang),
    "을지대학교":       ("을지대학교_수시_모집요강.json",       parse_eulji),
    "한국교원대학교":    ("한국교원대학교_수시_모집요강.json",    parse_knue),
    "동국대학교(WISE)": ("동국대학교(WISE)_수시_모집요강.json", parse_dgu_wise),
    "부산대학교":       ("부산대학교_수시_모집요강.json",       parse_pusan),
    "고신대학교":       ("고신대학교_수시_모집요강.json",       parse_kosin),
    "성균관대학교":     ("성균관대학교_수시_모집요강.json",     parse_sungkyunkwan),
    "순천향대학교":     ("순천향대학교_수시_모집요강.json",     parse_soonchunhyang),
    "충남대학교":       ("충남대학교_수시_모집요강.json",       parse_cnu),
    "가천대학교":       ("가천대학교_수시_모집요강.json",       parse_gachon),
    "충북대학교":       ("충북대학교_수시_모집요강.json",       parse_cbnu),
    "숭실대학교":       ("숭실대학교_수시_모집요강.json",       parse_soonsil),
    "극동대학교":       ("극동대학교_수시_모집요강.json",       parse_geukdong),
    "신경주대학교":     ("신경주대학교_수시_모집요강.json",     parse_shingyeongju),
    "협성대학교":       ("협성대학교_수시_모집요강.json",       parse_hyupsung),
    "아신대학교":       ("아신대학교_수시_모집요강.json",       parse_ashin),
    "나사렛대학교":     ("나사렛대학교_수시_모집요강.json",     parse_nasaret),
    "연세대학교(미래)": ("연세대학교(미래)_수시_모집요강.json", parse_yonsei_mirae),
    "우송대학교":       ("우송대학교_수시_모집요강.json",       parse_woosong),
    "화성의과학대학교": ("화성의과학대학교_수시_모집요강.json", parse_hwaseong),
    "건국대학교(글로컬)": ("건국대학교(글로컬)_수시_모집요강.json", parse_kbu),
    "경북대학교":       ("경북대학교_수시_모집요강.json",       parse_knu),
    "국립한국해양대학교": ("국립한국해양대학교_수시_모집요강.json", parse_kmou),
    "목원대학교":       ("목원대학교_수시_모집요강.json",       parse_mokwon),
    "서울대학교":       ("서울대학교_수시_모집요강.json",       parse_snu),
    "한경국립대학교":   ("한경국립대학교_수시_모집요강.json",   parse_hknu),
    "한국공학대학교":   ("한국공학대학교_수시_모집요강.json",   parse_tukorea),
    "한국기술교육대학교": ("한국기술교육대학교_수시_모집요강.json", parse_koreatech),
    "한국항공대학교":   ("한국항공대학교_수시_모집요강.json",   parse_hau),
    "한일장신대학교":   ("한일장신대학교_수시_모집요강.json",   parse_hanyil),
    "호남대학교":       ("호남대학교_수시_모집요강.json",       parse_honam),
}


# ─── Matcher & Updater ────────────────────────────────────────────────────────

def _rule_matches(rule: Rule, process_name: str, college: str, track: str, dept_name: str) -> bool:
    pn  = process_name.lower()
    col = (college   or "").lower().replace(" ", "")
    tr  = (track     or "").lower()
    dn  = (dept_name or "").lower()

    if rule.exclude_process and any(x.lower() in pn for x in rule.exclude_process):
        return False
    if rule.process_keywords:
        if not any(kw.lower() in pn for kw in rule.process_keywords):
            return False
    if rule.college_keywords:
        if not any(kw.lower().replace(" ", "") in col for kw in rule.college_keywords):
            return False
    if rule.track_keywords:
        if not any(kw.lower() in tr for kw in rule.track_keywords):
            return False
    if rule.dept_keywords:
        if not any(kw.lower() in dn for kw in rule.dept_keywords):
            return False

    return True


def apply_rules(db_path: Path, university: str, rules: list[Rule], dry_run: bool = False) -> dict:
    """Match rules to DB processes and update attributes. Returns stats."""
    conn = sqlite3.connect(db_path)

    rows = conn.execute(
        """
        SELECT p.id, p.process_name, p.attributes,
               json_extract(p.attributes, '$.단과대학') as college,
               d.track, d.name as dept_name
        FROM admission_process p
        JOIN admission_department d ON d.id = p.department_id
        WHERE d.university = ?
          AND json_extract(p.attributes, '$.수능최저.있음') = 1
          AND json_extract(p.attributes, '$.수능최저.조건') IS NULL
        """,
        (university,),
    ).fetchall()

    stats = {"total_unparsed": len(rows), "matched": 0, "skipped": 0, "corrections": 0, "updates": {}}

    for pid, proc_name, attrs_json, college, track, dept_name in rows:
        matched_rule = None
        for rule in rules:
            if _rule_matches(rule, proc_name, college or "", track or "", dept_name or ""):
                matched_rule = rule
                break

        if not matched_rule:
            stats["skipped"] += 1
            continue

        attrs = json.loads(attrs_json or "{}")

        if not matched_rule.exists:
            # This is a 미적용 correction
            new_sm = {"있음": False, "원문": "미적용 (모집요강 확인됨)"}
            stats["corrections"] += 1
        else:
            # Use pre-parsed result if available; otherwise call _parse_criteria now
            cp = matched_rule.criteria_parsed or _parse_criteria(matched_rule.criteria_raw)
            new_sm = {"있음": True}
            if cp.get("조건"):
                new_sm["조건"] = cp["조건"]
            원문 = cp.get("원문") or matched_rule.criteria_raw
            new_sm["원문"] = 원문[:200]
            if cp.get("추가조건"):
                new_sm["추가조건"] = cp["추가조건"]

        attrs["수능최저"] = new_sm
        new_attrs_json = json.dumps(attrs, ensure_ascii=False)

        stats["matched"] += 1
        key = matched_rule.criteria_raw[:60]
        stats["updates"][key] = stats["updates"].get(key, 0) + 1

        if not dry_run:
            conn.execute("UPDATE admission_process SET attributes = ? WHERE id = ?",
                         (new_attrs_json, pid))

    if not dry_run:
        conn.commit()
    conn.close()
    return stats


# ─── Main ─────────────────────────────────────────────────────────────────────

def run_university(university: str, dry_run: bool = False) -> None:
    if university not in UNIVERSITY_PARSERS:
        print(f"Unknown university: {university}")
        print(f"Available: {list(UNIVERSITY_PARSERS.keys())}")
        return

    json_file, parser_fn = UNIVERSITY_PARSERS[university]
    json_path = EXTRACTED_DIR / json_file

    if not json_path.exists():
        print(f"JSON not found: {json_path}")
        return

    print(f"\n{'='*60}")
    print(f"  {university}")
    print(f"{'='*60}")

    pages = load_pages(json_path)
    print(f"  PDF pages: {len(pages)}")

    rules = parser_fn(pages)  # type: ignore[operator]
    print(f"  Rules extracted: {len(rules)}")
    for i, r in enumerate(rules[:12]):
        exists_tag = "" if r.exists else " [미적용]"
        print(f"    [{i}]{exists_tag} proc={r.process_keywords[:3]} col={r.college_keywords[:2]} "
              f"tr={r.track_keywords[:2]} dept={r.dept_keywords[:2]}")
        print(f"         raw: {r.criteria_raw[:70]}")

    stats = apply_rules(DB_PATH, university, rules, dry_run=dry_run)
    pct = stats["matched"] / max(1, stats["total_unparsed"]) * 100
    print(f"\n  Total unparsed:  {stats['total_unparsed']}")
    print(f"  Matched:         {stats['matched']}  ({pct:.1f}%)")
    print(f"    of which 미적용 corrections: {stats['corrections']}")
    print(f"  Skipped:         {stats['skipped']}")

    if stats["updates"]:
        print(f"\n  Updates by criteria:")
        for crit, cnt in sorted(stats["updates"].items(), key=lambda x: -x[1])[:15]:
            print(f"    {cnt:4d}x  {crit[:65]}")

    if dry_run:
        print("\n  [DRY RUN — no changes written]")
    else:
        print(f"\n  Done. {stats['matched']} records updated.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Backfill 수능최저 criteria into DB")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--university", "-u", default=None,
                        help="Specific university to process (default: all).")
    args = parser.parse_args()

    unis = [args.university] if args.university else list(UNIVERSITY_PARSERS.keys())

    for uni in unis:
        run_university(uni, dry_run=args.dry_run)

    print("\nDone.")


if __name__ == "__main__":
    main()
