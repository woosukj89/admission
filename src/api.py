"""University admission recommendation REST API.

Deterministic recommendation based on 입시결과 data (no LLM).

Run with:
    python api.py
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from difflib import SequenceMatcher
from pathlib import Path
from typing import Generator, Optional

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

DB_PATH = Path(__file__).parent.parent / "data" / "admission.db"

# ── Region mapping ─────────────────────────────────────────────────────────────
UNIVERSITY_REGION: dict[str, list[str]] = {
    "가야대학교": ["경상"],
    "가천대학교": ["경기"],
    "가톨릭관동대학교": ["강원"],
    "가톨릭꽃동네대학교": ["충청"],
    "가톨릭대학교": ["서울", "경기"],
    "강남대학교": ["경기"],
    "강서대학교": ["경상"],
    "강원대학교": ["강원"],
    "건국대학교": ["서울"],
    "건국대학교(글로컬)": ["충청"],
    "건양대학교": ["충청"],
    "경기대학교": ["경기"],
    "경남대학교": ["경상"],
    "경동대학교": ["강원", "경기"],
    "경북대학교": ["경상"],
    "경상국립대학교": ["경상"],
    "경성대학교": ["경상"],
    "경운대학교": ["경상"],
    "경일대학교": ["경상"],
    "경희대학교": ["서울", "경기"],
    "계명대학교": ["경상"],
    "고려대학교": ["서울"],
    "고려대학교(세종)": ["충청"],
    "고신대학교": ["경상"],
    "공주교육대학교": ["충청"],
    "광신대학교": ["전라"],
    "광운대학교": ["서울"],
    "광주가톨릭대학교": ["전라"],
    "광주교육대학교": ["전라"],
    "광주대학교": ["전라"],
    "광주여자대학교": ["전라"],
    "국립강릉원주대학교": ["강원"],
    "국립경국대학교": ["경상"],
    "국립공주대학교": ["충청"],
    "국립군산대학교": ["전라"],
    "국립금오공과대학교": ["경상"],
    "국립목포대학교": ["전라"],
    "국립목포해양대학교": ["전라"],
    "국립부경대학교": ["경상"],
    "국립순천대학교": ["전라"],
    "국립창원대학교": ["경상"],
    "국립한국교통대학교": ["충청", "경기"],
    "국립한국해양대학교": ["경상"],
    "국립한밭대학교": ["충청"],
    "국민대학교": ["서울"],
    "극동대학교": ["충청"],
    "금강대학교": ["충청"],
    "김천대학교": ["경상"],
    "나사렛대학교": ["충청"],
    "남부대학교": ["전라"],
    "남서울대학교": ["충청"],
    "단국대학교": ["경기", "충청"],
    "대구가톨릭대학교": ["경상"],
    "대구교육대학교": ["경상"],
    "대구대학교": ["경상"],
    "대구예술대학교": ["경상"],
    "대구한의대학교": ["경상"],
    "대신대학교": ["경상"],
    "대전대학교": ["충청"],
    "대전신학대학교": ["충청"],
    "대진대학교": ["경기"],
    "덕성여자대학교": ["서울"],
    "동국대학교": ["서울"],
    "동국대학교(WISE)": ["경상"],
    "동덕여자대학교": ["서울"],
    "동명대학교": ["경상"],
    "동서대학교": ["경상"],
    "동신대학교": ["전라"],
    "동아대학교": ["경상"],
    "동양대학교": ["경상"],
    "동의대학교": ["경상"],
    "루터대학교": ["서울"],
    "명지대학교": ["서울", "경기"],
    "목원대학교": ["충청"],
    "목포가톨릭대학교": ["전라"],
    "백석대학교": ["충청"],
    "부산가톨릭대학교": ["경상"],
    "부산교육대학교": ["경상"],
    "부산대학교": ["경상"],
    "부산외국어대학교": ["경상"],
    "부산장신대학교": ["경상"],
    "삼육대학교": ["서울"],
    "상명대학교": ["서울", "충청"],
    "상지대학교": ["강원"],
    "서강대학교": ["서울"],
    "서경대학교": ["서울"],
    "서울과학기술대학교": ["서울"],
    "서울기독대학교": ["서울"],
    "서울대학교": ["서울"],
    "서울시립대학교": ["서울"],
    "서울신학대학교": ["경기"],
    "서울여자대학교": ["서울"],
    "서울장신대학교": ["서울"],
    "서울한영대학교": ["서울"],
    "서원대학교": ["충청"],
    "선문대학교": ["충청"],
    "성결대학교": ["경기"],
    "성공회대학교": ["서울"],
    "성균관대학교": ["서울", "경기"],
    "성신여자대학교": ["서울"],
    "세명대학교": ["충청"],
    "세종대학교": ["서울"],
    "세한대학교": ["전라"],
    "송원대학교": ["전라"],
    "수원대학교": ["경기"],
    "숙명여자대학교": ["서울"],
    "순천향대학교": ["충청"],
    "숭실대학교": ["서울"],
    "신경주대학교": ["경상"],
    "신라대학교": ["경상"],
    "신한대학교": ["경기"],
    "아신대학교": ["경기"],
    "아주대학교": ["경기"],
    "안양대학교": ["경기"],
    "연세대학교": ["서울"],
    "연세대학교(미래)": ["강원"],
    "영남대학교": ["경상"],
    "영남신학대학교": ["경상"],
    "영산대학교": ["경상"],
    "영산선학대학교": ["전라"],
    "예수대학교": ["전라"],
    "예원예술대학교": ["전라", "경기"],
    "용인대학교": ["경기"],
    "우석대학교": ["전라"],
    "우송대학교": ["충청"],
    "울산대학교": ["경상"],
    "원광대학교": ["전라"],
    "위덕대학교": ["경상"],
    "유원대학교": ["충청"],
    "을지대학교": ["경기", "충청"],
    "인제대학교": ["경상"],
    "인천가톨릭대학교": ["인천"],
    "인천대학교": ["인천"],
    "인하대학교": ["인천"],
    "장로회신학대학교": ["서울"],
    "전남대학교": ["전라"],
    "전북대학교": ["전라"],
    "전주대학교": ["전라"],
    "제주국제대학교": ["제주"],
    "제주대학교": ["제주"],
    "조선대학교": ["전라"],
    "중부대학교": ["충청"],
    "중앙대학교": ["서울", "경기"],
    "중앙승가대학교": ["경기"],
    "중원대학교": ["충청"],
    "차의과학대학교": ["경기"],
    "창신대학교": ["경상"],
    "청운대학교": ["충청"],
    "청주교육대학교": ["충청"],
    "청주대학교": ["충청"],
    "초당대학교": ["전라"],
    "총신대학교": ["서울"],
    "추계예술대학교": ["서울"],
    "충남대학교": ["충청"],
    "충북대학교": ["충청"],
    "칼빈대학교": ["경기"],
    "평택대학교": ["경기"],
    "한경국립대학교": ["경기"],
    "한국공학대학교": ["경기"],
    "한국교원대학교": ["충청"],
    "한국기술교육대학교": ["충청"],
    "한국성서대학교": ["서울"],
    "한국외국어대학교": ["서울", "경기"],
    "한국체육대학교": ["서울"],
    "한국침례신학대학교": ["충청"],
    "한국항공대학교": ["경기"],
    "한남대학교": ["충청"],
    "한동대학교": ["경상"],
    "한라대학교": ["강원"],
    "한림대학교": ["강원"],
    "한서대학교": ["충청"],
    "한성대학교": ["서울"],
    "한세대학교": ["경기"],
    "한신대학교": ["경기"],
    "한양대학교": ["서울"],
    "한양대학교(ERICA)": ["경기"],
    "한일장신대학교": ["전라"],
    "협성대학교": ["경기"],
    "호남대학교": ["전라"],
    "호남신학대학교": ["전라"],
    "호서대학교": ["충청"],
    "호원대학교": ["전라"],
    "홍익대학교": ["서울", "충청"],
    "화성의과학대학교": ["경기"],
}

PROCESS_TYPE_DESC: dict[str, str] = {
    "학생부교과": "내신 성적(교과) 위주 전형",
    "학생부종합": "서류·면접 종합 평가 전형",
    "논술위주": "논술 고사 위주 전형",
    "실기/실적위주": "실기 고사 또는 예체능 실적 위주 전형",
    "수능위주": "수능 성적 위주 정시 전형",
    "기타": "기회균형·농어촌·특성화 등 특별 전형",
}

# Section headers that terminate the 지원자격 block
_NEXT_SECTION_PATTERNS = re.compile(
    r'(?:선\s*발\s*(?:원\s*칙|방\s*법)|전\s*형\s*(?:방\s*법|요\s*소|절\s*차|일\s*정|료)|'
    r'평\s*가\s*(?:요\s*소|방\s*법)|수\s*능\s*(?:최\s*저|반\s*영|성\s*적)|'
    r'최\s*저\s*학\s*력|제\s*출\s*서\s*류|합\s*격\s*자|지\s*원\s*방\s*법|반\s*영\s*비\s*율)'
)


# ── Process type inference ─────────────────────────────────────────────────────

def infer_process_type(name: str) -> str:
    """Infer 전형 type from process name string.

    기타 (special) patterns are checked first to avoid false positives like
    '기회균형' matching the '균형' keyword for 학생부교과.
    """
    n = re.sub(r"\s+", "", name or "")

    # Special / 기타 — check FIRST
    if any(k in n for k in [
        "기회균형", "기회", "농어촌", "특성화", "재직자", "특수교육",
        "사회배려", "사회적배려", "기초생활", "차상위", "저소득",
        "수급자", "국가보훈", "만학도", "북한이탈", "장애", "서해5도",
    ]):
        return "기타"

    if any(k in n for k in ["교과", "내신"]) and "종합" not in n:
        return "학생부교과"

    if any(k in n for k in ["종합", "학종", "서류"]):
        return "학생부종합"

    if "논술" in n:
        return "논술위주"

    if any(k in n for k in ["실기", "실적", "예체능", "연기", "음악", "미술"]):
        return "실기/실적위주"

    if any(k in n for k in ["수능", "가군", "나군", "다군"]):
        return "수능위주"

    if any(k in n for k in ["면접", "활동우수", "미래인재", "잠재력", "인재",
                              "다양성", "창의", "자기주도"]):
        return "학생부종합"

    if any(k in n for k in ["지역균형", "지역인재", "추천", "학업우수",
                              "교과우수", "교과성적"]):
        return "학생부교과"

    return "기타"


# ── Pydantic models ────────────────────────────────────────────────────────────

class RecommendItem(BaseModel):
    university: str
    department: str
    process_name: str
    process_type: str           # inferred from process_name
    process_type_desc: str      # human-readable description
    admission_type: Optional[str]
    score_type: Optional[str]
    competition_rate: Optional[float]
    quota: Optional[int]        # from result attributes (81% coverage)
    applicants: Optional[int]   # quota × competition_rate
    average_score: Optional[float]
    cut_70: Optional[float]
    cut_80: Optional[float]
    result_year: int
    안정_level: int  # 1 (aggressive reach) → 5 (very safe); 0 = no grade given
    지원자격: Optional[str]     # eligibility text from 모집요강, if available


class RecommendResponse(BaseModel):
    safe: list[RecommendItem]   # 안정_level 3–5
    reach: list[RecommendItem]  # 안정_level 1–2
    total_candidates: int
    query_summary: str


# ── DB helpers ─────────────────────────────────────────────────────────────────

@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _query_results(
    conn: sqlite3.Connection,
    admission_type: Optional[str],
    year: Optional[int],
    dept_keywords: list[str],
    limit: int = 3000,
) -> list[dict]:
    """Fetch 등급 result rows; quota from result attributes (81% coverage)."""
    clauses: list[str] = [
        "r.score_type = '등급'",
        "(r.cut_70 IS NOT NULL OR r.average_score IS NOT NULL)",
    ]
    params: list = []

    if admission_type:
        clauses.append("r.admission_type = ?")
        params.append(admission_type)

    if year:
        clauses.append("r.result_year = ?")
        params.append(year)

    if dept_keywords:
        dept_clause = " OR ".join("d.name LIKE ?" for _ in dept_keywords)
        clauses.append(f"({dept_clause})")
        params.extend(f"%{kw}%" for kw in dept_keywords)

    where = " AND ".join(clauses)
    params.append(limit)

    sql = f"""
        SELECT r.department_id,
               d.university, d.name AS department,
               r.process_name, r.admission_type, r.score_type,
               r.competition_rate, r.average_score,
               r.cut_70, r.cut_80, r.result_year,
               CAST(json_extract(r.attributes, '$.모집인원') AS INTEGER) AS quota
        FROM admission_result r
        JOIN admission_department d ON d.id = r.department_id
        WHERE {where}
        ORDER BY CASE WHEN r.cut_70 IS NULL THEN 1 ELSE 0 END, r.cut_70 ASC
        LIMIT ?
    """
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _matches_region(university: str, region_keywords: list[str]) -> bool:
    regions = UNIVERSITY_REGION.get(university, [])
    return any(kw in regions for kw in region_keywords)


def _compute_safety_level(row: dict, student_grade: float) -> Optional[int]:
    """Return 안정_level 1–5, or None if row is out of range.

    등급: lower = better.  margin = cut_70 - student_grade (positive = safe).

    cut_70-based levels:
      margin >= 1.5  → 5  (very safe)
      margin >= 0.7  → 4  (safe)
      margin >= 0.0  → 3  (borderline safe: at or just above the cut)
      margin >= -0.5 → 2  (mild reach: just below cut_70, supported by cut_80/avg)
      margin < -0.5 and avg supports → 1  (aggressive reach)
      else → None (too far, excluded)

    average_score fallback (when cut_70 is NULL):
      avg_margin >= 1.5  → 5
      avg_margin >= 0.8  → 4
      avg_margin >= 0.3  → 3
      avg_margin >= -0.3 → 2
      else → None
    """
    cut_70 = row.get("cut_70")
    cut_80 = row.get("cut_80")
    avg = row.get("average_score")

    if cut_70 is not None:
        margin = cut_70 - student_grade
        if margin >= 1.5:
            return 5
        if margin >= 0.7:
            return 4
        if margin >= 0.0:
            return 3
        if margin >= -0.5:
            if (cut_80 is not None and cut_80 >= student_grade) or \
               (avg is not None and avg >= student_grade - 0.5):
                return 2
            return 1  # cut_70 says reach, no supporting data — still include as level 1
        # gap > 0.5 below cut_70
        if avg is not None and avg >= student_grade - 0.5:
            return 1
        return None  # too far, exclude

    if avg is not None:
        avg_margin = avg - student_grade
        if avg_margin >= 1.5:
            return 5
        if avg_margin >= 0.8:
            return 4
        if avg_margin >= 0.3:
            return 3
        if avg_margin >= -0.3:
            return 2
        return None

    return None


def _margin(row: dict, student_grade: float) -> float:
    """Signed margin for sorting (positive = safe, negative = reach)."""
    cut_70 = row.get("cut_70")
    if cut_70 is not None:
        return cut_70 - student_grade
    avg = row.get("average_score") or 0.0
    return avg - student_grade - 0.3  # avg-based: slightly penalised


# ── Process info lookup ────────────────────────────────────────────────────────

def _norm_name(s: str) -> str:
    """Strip spaces and lowercase for fuzzy comparison."""
    return re.sub(r"\s+", "", s or "").lower()


def _extract_지원자격(content: str | None) -> Optional[str]:
    """Extract the 지원자격 section text from admission_process.content.

    The content field has a structured header block followed by:
        --- 원문 (pN) ---
        [raw PDF page text]

    We search for the 지원자격 header (with possible internal spaces from PDF
    extraction), then capture text until the next recognised section header.
    """
    if not content:
        return None

    # Search in the raw-text portion only (after the structured header)
    raw_start = content.find("--- 원문")
    search_in = content[raw_start:] if raw_start != -1 else content

    # Match 지원자격 header — allow spaces between chars (PDF extraction artifact)
    header_m = re.search(r"지\s*원\s*자\s*격", search_in)
    if not header_m:
        return None

    # Text starts right after the header match
    after_header = search_in[header_m.end():]

    # Skip leading punctuation / whitespace that belongs to the header line
    after_header = re.sub(r"^[\s\n.:(확인서)]+", "", after_header)

    # Find the end: next recognised section header
    stop_m = _NEXT_SECTION_PATTERNS.search(after_header)
    end = stop_m.start() if stop_m else min(len(after_header), 800)

    snippet = after_header[:end].strip()

    # Clean up excess whitespace while preserving structure
    snippet = re.sub(r"[ \t]+", " ", snippet)
    snippet = re.sub(r"\n{3,}", "\n\n", snippet)

    if len(snippet) > 600:
        snippet = snippet[:597] + "..."

    return snippet if snippet else None


_GENERIC_PROCESS_TERMS = re.compile(
    r"전형|우수자|학생부|교과|종합|수능|논술|실기|실적|일반|지역|추천|우선|특기"
)


def _process_keyword(name: str) -> Optional[str]:
    """Extract a distinctive keyword from a result process name for content search.

    Returns None for names that are too generic after stripping common terms.
    """
    stripped = _GENERIC_PROCESS_TERMS.sub("", re.sub(r"\s+", "", name or ""))
    stripped = re.sub(r"[()（）\[\]【】]", "", stripped).strip()
    if len(stripped) >= 3:
        return stripped
    # Fallback: use full name if long enough and not purely generic
    clean = re.sub(r"\s+", "", name or "")
    if len(clean) >= 4:
        return clean
    return None


def _lookup_process(
    conn: sqlite3.Connection,
    university: str,
    result_process_name: str,
) -> Optional[dict]:
    """Return the best-matching admission_process row for a result row.

    Uses university-wide search to work around the 0.09% exact match rate
    between 입시결과 process names and 모집요강 process names.

    Two-tier strategy:
    1. Name-fuzzy: compare result process_name against all process records that
       have '지원자격' content.  Threshold 0.6 avoids wrong-전형 matches.
    2. Content-keyword: if name-fuzzy misses, search for a distinctive keyword
       from the result process_name within process content (+ '지원자격').
       This handles university-branded names (e.g. 'KU자기추천') that appear
       in the content text but under a different process record name.
    """
    qual_rows = conn.execute(
        """
        SELECT p.process_name, p.process_type, p.quota, p.content
        FROM admission_process p
        JOIN admission_department d ON d.id = p.department_id
        WHERE d.university = ?
          AND p.content LIKE '%지원자격%'
        """,
        (university,),
    ).fetchall()

    # ── Tier 1: name-fuzzy match ──────────────────────────────────────────────
    if qual_rows:
        target = _norm_name(result_process_name)
        best_row: Optional[sqlite3.Row] = None
        best_score = 0.0

        for row in qual_rows:
            pn = _norm_name(row["process_name"])
            if pn == target:
                return dict(row)
            if target in pn or pn in target:
                score = 0.8 + len(min(target, pn, key=len)) / max(len(target), len(pn), 1) * 0.2
            else:
                score = SequenceMatcher(None, target, pn).ratio()
            if score > best_score:
                best_score = score
                best_row = row

        if best_score >= 0.6 and best_row is not None:
            return dict(best_row)

    # ── Tier 2: content-keyword search ───────────────────────────────────────
    keyword = _process_keyword(result_process_name)
    if keyword:
        row = conn.execute(
            """
            SELECT p.process_name, p.process_type, p.quota, p.content
            FROM admission_process p
            JOIN admission_department d ON d.id = p.department_id
            WHERE d.university = ?
              AND p.content LIKE ?
              AND p.content LIKE '%지원자격%'
            LIMIT 1
            """,
            (university, f"%{keyword}%"),
        ).fetchone()
        if row:
            return dict(row)

    return None


def _to_item(
    row: dict,
    level: int,
    conn: sqlite3.Connection,
) -> RecommendItem:
    ptype = infer_process_type(row["process_name"])
    quota = row.get("quota")
    comp = row.get("competition_rate")
    applicants = round(quota * comp) if quota is not None and comp is not None else None

    # Look up 지원자격 from 모집요강 content (university-wide, threshold 0.6)
    proc = _lookup_process(conn, row["university"], row["process_name"])
    지원자격 = _extract_지원자격(proc.get("content") if proc else None)

    return RecommendItem(
        university=row["university"],
        department=row["department"],
        process_name=row["process_name"],
        process_type=ptype,
        process_type_desc=PROCESS_TYPE_DESC.get(ptype, ""),
        admission_type=row.get("admission_type"),
        score_type=row.get("score_type"),
        competition_rate=comp,
        quota=quota,
        applicants=applicants,
        average_score=row.get("average_score"),
        cut_70=row.get("cut_70"),
        cut_80=row.get("cut_80"),
        result_year=row["result_year"],
        안정_level=level,
        지원자격=지원자격,
    )


def _top_n_deduped(
    triples: list[tuple[dict, float, int]],
    n: int = 5,
) -> list[tuple[dict, int]]:
    """Return up to n (row, level) pairs, deduping by (university, department)."""
    items: list[tuple[dict, int]] = []
    seen: set[tuple[str, str]] = set()
    for row, _, level in triples:
        key = (row["university"], row["department"])
        if key not in seen:
            items.append((row, level))
            seen.add(key)
        if len(items) >= n:
            break
    return items


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="입시 추천 API",
    description=(
        "Korean university admission recommendation based on 입시결과 data. "
        "Returns top 5 안정 (safe, levels 3–5) and 5 도전 (reach, levels 1–2) universities "
        "with process eligibility (지원자격) from 모집요강."
    ),
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Mount web client routes (auth + chat)
from src.web.router import router as _web_router  # noqa: E402
from src.web.middleware import RequestLogMiddleware  # noqa: E402
app.add_middleware(RequestLogMiddleware)
app.include_router(_web_router)


import os as _os

@app.get("/api/admin/stats")
def admin_stats(x_admin_secret: str | None = None):
    """Return analytics overview. Protected by ADMIN_SECRET header."""
    secret = _os.environ.get("ADMIN_SECRET", "")
    if secret and x_admin_secret != secret:
        from fastapi import HTTPException as _H
        raise _H(status_code=403, detail="Forbidden")
    from src.storage.analytics_store import get_analytics_store
    store = get_analytics_store()
    return {
        **store.get_overview(),
        "questions_last_7_days": store.get_daily_questions(7),
    }


@app.get("/recommend", response_model=RecommendResponse, summary="대학 추천")
def recommend(
    year: Optional[int] = Query(None, description="입시년도 필터 (e.g. 2025)"),
    susi_grade: Optional[float] = Query(
        None, ge=1.0, le=9.0,
        description="수시 내신 평균등급 (1.0–9.0, 낮을수록 우수)",
    ),
    jeongsi_grade: Optional[float] = Query(
        None, ge=1.0, le=9.0,
        description="정시 수능 평균등급 (1.0–9.0, 낮을수록 우수)",
    ),
    departments: Optional[str] = Query(
        None,
        description="학과 키워드, 쉼표 구분 (e.g. 컴퓨터공학,소프트웨어)",
    ),
    regions: Optional[str] = Query(
        None,
        description="지역 키워드, 쉼표 구분 (서울/경기/인천/강원/충청/전라/경상/제주)",
    ),
    min_level: Optional[int] = Query(
        None, ge=1, le=5,
        description="최소 안정_level 필터 (1–5). 예: 4 → level 4·5 결과만 반환",
    ),
    max_level: Optional[int] = Query(
        None, ge=1, le=5,
        description="최대 안정_level 필터 (1–5). 예: 2 → level 1·2 결과만 반환",
    ),
) -> RecommendResponse:
    dept_keywords = [d.strip() for d in departments.split(",") if d.strip()] if departments else []
    region_keywords = [r.strip() for r in regions.split(",") if r.strip()] if regions else []

    with _conn() as conn:
        # ── Fetch candidates ──────────────────────────────────────────────────
        if susi_grade is not None and jeongsi_grade is not None:
            candidates = (
                _query_results(conn, "수시", year, dept_keywords)
                + _query_results(conn, "정시", year, dept_keywords)
            )
        elif susi_grade is not None:
            candidates = _query_results(conn, "수시", year, dept_keywords)
        elif jeongsi_grade is not None:
            candidates = _query_results(conn, "정시", year, dept_keywords)
        else:
            candidates = _query_results(conn, None, year, dept_keywords)

        # ── Region filter ─────────────────────────────────────────────────────
        if region_keywords:
            candidates = [
                c for c in candidates
                if _matches_region(c["university"], region_keywords)
            ]

        total = len(candidates)

        # ── No grade: rank by competition_rate ────────────────────────────────
        if susi_grade is None and jeongsi_grade is None:
            candidates.sort(key=lambda c: c.get("competition_rate") or 0.0, reverse=True)
            seen: set[tuple[str, str]] = set()
            top10: list[dict] = []
            for c in candidates:
                key = (c["university"], c["department"])
                if key not in seen:
                    top10.append(c)
                    seen.add(key)
                if len(top10) >= 10:
                    break
            safe_items = [_to_item(c, 0, conn) for c in top10[:5]]
            reach_items = [_to_item(c, 0, conn) for c in top10[5:10]]
            summary_parts = ["성적 미입력 — 경쟁률 순위"]
            if dept_keywords:
                summary_parts.append(f"학과: {', '.join(dept_keywords)}")
            if region_keywords:
                summary_parts.append(f"지역: {', '.join(region_keywords)}")
            return RecommendResponse(
                safe=safe_items,
                reach=reach_items,
                total_candidates=total,
                query_summary=" | ".join(summary_parts),
            )

        # ── Compute safety level for each candidate ───────────────────────────
        safe_triples: list[tuple[dict, float, int]] = []
        reach_triples: list[tuple[dict, float, int]] = []

        for row in candidates:
            adm = row.get("admission_type")
            if adm == "수시":
                grade = susi_grade
            elif adm == "정시":
                grade = jeongsi_grade
            else:
                grade = susi_grade if susi_grade is not None else jeongsi_grade

            if grade is None:
                continue

            level = _compute_safety_level(row, grade)
            if level is None:
                continue

            # Apply level range filter
            if min_level is not None and level < min_level:
                continue
            if max_level is not None and level > max_level:
                continue

            if level >= 3:
                safe_triples.append((row, grade, level))
            else:
                reach_triples.append((row, grade, level))

        # ── Sort ──────────────────────────────────────────────────────────────
        # Safe: smallest margin first (most competitive/prestigious safe school at top)
        safe_triples.sort(key=lambda x: _margin(x[0], x[1]))
        # Reach: largest margin first (closest/most achievable reach at top)
        reach_triples.sort(key=lambda x: _margin(x[0], x[1]), reverse=True)

        # ── Top 5 with dedup, then enrich with 지원자격 ───────────────────────
        safe_items = [
            _to_item(r, lvl, conn) for r, lvl in _top_n_deduped(safe_triples)
        ]
        reach_items = [
            _to_item(r, lvl, conn) for r, lvl in _top_n_deduped(reach_triples)
        ]

    # ── Summary ───────────────────────────────────────────────────────────────
    summary_parts = []
    if susi_grade is not None:
        summary_parts.append(f"수시 내신 {susi_grade}등급")
    if jeongsi_grade is not None:
        summary_parts.append(f"정시 수능 {jeongsi_grade}등급")
    if dept_keywords:
        summary_parts.append(f"학과: {', '.join(dept_keywords)}")
    if region_keywords:
        summary_parts.append(f"지역: {', '.join(region_keywords)}")
    if min_level is not None or max_level is not None:
        lo, hi = min_level or 1, max_level or 5
        summary_parts.append(f"안정레벨 {lo}–{hi}")

    return RecommendResponse(
        safe=safe_items,
        reach=reach_items,
        total_candidates=total,
        query_summary=" | ".join(summary_parts) if summary_parts else "전체",
    )


@app.get("/stats", summary="DB 통계")
def stats() -> dict:
    """Return a summary of what's in the admission database."""
    with _conn() as conn:
        universities = conn.execute(
            "SELECT COUNT(DISTINCT university) FROM admission_department"
        ).fetchone()[0]
        departments = conn.execute(
            "SELECT COUNT(*) FROM admission_department"
        ).fetchone()[0]
        processes = conn.execute(
            "SELECT COUNT(*) FROM admission_process"
        ).fetchone()[0]
        results = conn.execute(
            "SELECT COUNT(*) FROM admission_result"
        ).fetchone()[0]
        result_years = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT result_year FROM admission_result ORDER BY result_year"
            ).fetchall()
        ]
        score_types = {
            r[0]: r[1] for r in conn.execute(
                "SELECT score_type, COUNT(*) FROM admission_result "
                "GROUP BY score_type ORDER BY COUNT(*) DESC"
            ).fetchall()
        }
        admission_types = {
            r[0]: r[1] for r in conn.execute(
                "SELECT admission_type, COUNT(*) FROM admission_result "
                "GROUP BY admission_type ORDER BY COUNT(*) DESC"
            ).fetchall()
        }

    return {
        "universities": universities,
        "departments": departments,
        "processes": processes,
        "results": results,
        "result_years": result_years,
        "score_types": score_types,
        "admission_types": admission_types,
    }


# Serve frontend SPA — must be mounted AFTER all API routes
_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
