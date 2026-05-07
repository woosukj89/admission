"""Batch extract admission data from all pre-extracted JSON files into AdmissionStore.

Finds "matrix tables" where departments are rows and 전형s are columns,
extracts (department, 전형, quota) triples, and stores them.

Content strategy: store ALL raw text from pages that mention a 전형, plus
the overview table row (전형방법, 비고). This ensures no information is lost
regardless of each university's unique formatting.
"""

import io
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from src.storage.admission_store import AdmissionStore
from src.parse_suneung_min import parse_수능최저


# ── Normalization helpers ──────────────────────────────────

def norm(s: str | None) -> str:
    """Remove all whitespace from a string for comparison."""
    if not s:
        return ""
    return re.sub(r"\s+", "", str(s))


def clean(s: str | None) -> str:
    """Clean text: collapse whitespace, strip."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def collapse_spaced_text(text: str) -> str:
    """Collapse character-spaced Korean text from pdfplumber font artifacts.

    e.g. '정 보 통 신 공 학 과' → '정보통신공학과'
    Only collapses when every non-space segment is exactly 1 character.
    """
    stripped = text.strip()
    if re.fullmatch(r'(\S )+\S', stripped):
        return stripped.replace(' ', '')
    return text


def parse_quota(s: str | None) -> int | None:
    """Parse a quota cell value to int. Returns None if not numeric."""
    if not s:
        return None
    s = str(s).strip()
    if s in ("", "-", "·", "‧", "∙", ".", "0", "*", "―"):
        return None
    # Remove commas and extract first number
    m = re.search(r"\d[\d,]*", s.replace(" ", ""))
    if m:
        cleaned = m.group().replace(",", "")
        if cleaned:
            val = int(cleaned)
            # Reasonable quota range: 1 to 99999
            return val if 0 < val < 100000 else None
    return None


STRUCTURAL_KEYWORDS = {
    "대학", "단과대학", "단과", "모집단위", "학과", "학부", "계열",
    "합계", "소계", "계", "정원", "입학정원", "모집인원", "수시모집인원",
    "정시모집인원", "비고", "캠퍼스", "전공", "학과및전공", "정원구분",
    "정원내", "정원외", "전공선택기준정원", "전공선택", "기준정원",
    "모집시기", "구분", "총모집인원", "전체모집인원", "모집인원합계",
    "번호", "연번", "순번", "전화번호", "전화", "문의", "고사시간",
    "고사장", "면접시간", "일정", "비율", "배점", "반영비율",
    "국어", "수학", "영어", "탐구", "과학", "사회", "한국사",
    "백분위", "등급", "표준점수", "원점수", "평균", "총점",
}

TOTAL_ROW_KEYWORDS = {"합계", "소계", "계", "합", "total", "전체", "총", "수시계", "정시계"}


def is_structural_col(header: str) -> bool:
    """Check if a column header is structural (not a 전형 name)."""
    h = norm(header)
    if not h:
        return True
    # Direct match
    if h in STRUCTURAL_KEYWORDS:
        return True
    # Partial match for common patterns
    for kw in ["모집단위", "단과대학", "학과", "계열", "합계", "소계", "캠퍼스",
               "정원구분", "정원내", "정원외", "입학정원", "수시모집인원", "정시모집인원",
               "비고", "모집시기", "구분", "모집인원", "총모집", "전체모집",
               "전화번호", "고사시간", "면접시간", "고사장", "반영비율", "배점",
               "일정", "문의", "등록금", "장학", "수시A", "수시B", "수시C", "수시D", "수시E",
               "정시A", "정시B", "정시C"]:
        if kw in h:
            return True
    return False


def is_valid_process_name(name: str) -> bool:
    """Check if a process name looks like a real 전형 name."""
    n = norm(name)
    if not n:
        return False
    # Reject purely numeric names
    if re.match(r"^[\d\s.,·\-―]+$", name.strip()):
        return False
    # Reject time/duration patterns
    if re.search(r"\d+분", n) or "내외" in n:
        return False
    # Reject phone number patterns
    if re.search(r"\d{2,4}[-)\s]\d{3,4}", name):
        return False
    # Reject room/building numbers (e.g., "사회대 260호", "경영대 2호관 106호")
    if re.search(r"\d+호", n):
        return False
    # Reject 군 designations ('가'군, '나'군, '다'군)
    if re.search(r"['\u2018\u2019]?[가나다]['\u2018\u2019]?군", n):
        return False
    # Reject subtotal patterns
    if n in {"수시계", "정시계", "수시", "정시"}:
        return False
    # Reject scholarship/money patterns (e.g., "100만원", "1학기 등록금 50%", "수시A급")
    if re.search(r"\d+만원", n) or "등록금" in n or "장학금" in n:
        return False
    if re.search(r"\d+%", n):
        return False
    if re.search(r"수시[A-Z가-힣]\d?급", n) or re.search(r"정시[A-Z가-힣]\d?급", n):
        return False
    # Reject date/schedule patterns
    if re.search(r"\d{4}\.\s*\d{1,2}\.\s*\d{1,2}", n):
        return False
    # Reject if it looks like a total/summary, score/grade, or subject column
    reject_patterns = ["총모집", "전체모집", "모집인원", "입학정원", "기준정원",
                       "고사시간", "고사장", "면접시간", "전화번호",
                       "백분위", "등급", "평균", "표준점수", "점수",
                       "환산점수", "반영점수", "원점수", "총점",
                       "과목수", "반영과목", "지원자정보", "지원자기재",
                       "반영교과", "이수단위", "실기내용", "실기종목",
                       "지원자격기준", "제출서류"]
    for pat in reject_patterns:
        if pat in n:
            return False
    # Reject subject combination strings (contain 2+ of: 국어/수학/영어/탐구/과학/사회)
    subjects = ["국어", "수학", "영어", "탐구", "과학", "사회", "한국사"]
    if sum(1 for s in subjects if s in n) >= 2:
        return False
    # Reject very short single-character names (likely row numbers)
    if len(n) <= 1:
        return False
    # Reject overly long names (form fields, instruction text, etc.)
    # Real 전형 names are rarely more than ~25 characters when normalized
    if len(n) > 30:
        return False
    return True


def is_total_row(row: list) -> bool:
    """Check if a row is a subtotal/total row."""
    for cell in row:
        if cell:
            n = norm(str(cell))
            if n in TOTAL_ROW_KEYWORDS:
                return True
    return False


def classify_process_type(name: str, overview_type: str = "") -> str:
    """Classify 전형명 into standard process_type.

    Uses 전형유형 from overview table if provided for more accurate classification.
    """
    # Prefer overview_type if available
    ot = norm(overview_type)
    if ot:
        if "교과" in ot and "종합" not in ot:
            return "학생부교과"
        if "종합" in ot:
            return "학생부종합"
        if "논술" in ot:
            return "논술위주"
        if "실기" in ot or "실적" in ot:
            return "실기/실적위주"
        if "수능" in ot:
            return "수능위주"

    n = norm(name)
    if any(k in n for k in ["교과", "내신"]):
        if "종합" not in n:
            return "학생부교과"
    if any(k in n for k in ["종합", "학종", "서류"]):
        return "학생부종합"
    if "논술" in n:
        return "논술위주"
    if any(k in n for k in ["실기", "실적", "특기", "체육", "예체능", "연기", "음악", "미술"]):
        return "실기/실적위주"
    if any(k in n for k in ["수능", "정시", "가군", "나군", "다군"]):
        return "수능위주"
    # 학생부종합 indicators
    if any(k in n for k in ["면접", "활동우수", "미래인재", "잠재력", "인재",
                              "다양성", "창의", "자기주도"]):
        return "학생부종합"
    # 학생부교과 indicators
    if any(k in n for k in ["균형", "지역", "추천", "학업우수", "일반전형",
                              "교과우수", "교과성적"]):
        return "학생부교과"
    # 기회균형/사회배려 — typically 기타
    if any(k in n for k in ["기회", "농어촌", "특성화", "재직자", "특수교육",
                              "사회배려", "사회적배려", "기초생활", "차상위",
                              "저소득", "수급자", "국가보훈", "만학도", "서해5도",
                              "북한이탈", "특수", "장애"]):
        return "기타"
    return "기타"


def find_dept_col(headers: list[str]) -> int | None:
    """Find the column index for department name (모집단위)."""
    for i, h in enumerate(headers):
        n = norm(h)
        if any(k in n for k in ["모집단위", "학과", "학부"]):
            return i
    return None


def find_track_col(headers: list[str]) -> int | None:
    """Find the column index for 계열."""
    for i, h in enumerate(headers):
        if norm(h) == "계열" or "계열" in norm(h):
            return i
    return None


def find_college_col(headers: list[str]) -> int | None:
    """Find the column index for 대학/단과대학."""
    for i, h in enumerate(headers):
        n = norm(h)
        if n in ("대학", "단과대학") or "단과대학" in n:
            return i
    return None


# ── Overview table detection ────────────────────────────────

def _build_col_labels(table: dict) -> list[str]:
    """Build a per-column label list using the best non-empty value from any header row."""
    headers_rows = table.get("headers", [])
    ncols = table.get("dimensions", {}).get("cols", 0)
    all_header_rows = []
    for hrow in headers_rows:
        padded = list(hrow) + [None] * max(0, ncols - len(hrow))
        all_header_rows.append([clean(c) if c else "" for c in padded])

    col_labels = []
    for i in range(ncols):
        label = ""
        for hrow in reversed(all_header_rows):
            if i < len(hrow) and hrow[i]:
                label = hrow[i]
                break
        col_labels.append(label)
    return col_labels


def detect_overview_table(table: dict) -> dict | None:
    """Try to extract 전형 overview info from a 전형별 모집인원 summary table.

    Looks for tables with 전형명 + 전형방법 columns.
    Returns {전형명: {전형방법, 비고, 전형유형, 총모집인원}} or None.
    """
    data = table.get("data", [])
    if not data:
        return None

    col_labels = _build_col_labels(table)
    if not col_labels:
        return None

    # Must have 전형명 AND 전형방법 columns
    has_proc = any("전형명" in norm(h) for h in col_labels)
    has_method = any("전형방법" in norm(h) for h in col_labels)
    if not (has_proc and has_method):
        return None

    proc_col = next((i for i, h in enumerate(col_labels) if "전형명" in norm(h)), None)
    method_col = next((i for i, h in enumerate(col_labels) if "전형방법" in norm(h)), None)
    note_col = next((i for i, h in enumerate(col_labels) if norm(h) == "비고" or
                     ("비고" in norm(h) and len(norm(h)) <= 6)), None)
    type_col = next((i for i, h in enumerate(col_labels) if "전형유형" in norm(h)), None)
    # Prefer 총모집인원 over 모집인원 to get the total
    total_col = next((i for i, h in enumerate(col_labels)
                      if "총모집인원" in norm(h) or
                      ("총" in norm(h) and "모집인원" in norm(h))), None)
    quota_col = next((i for i, h in enumerate(col_labels)
                      if "모집인원" in norm(h) and i != total_col), None) if total_col is None else None
    eff_quota_col = total_col if total_col is not None else quota_col

    if proc_col is None:
        return None

    result: dict[str, dict] = {}
    prev_proc = ""
    prev_type = ""

    for row in data:
        if len(row) <= proc_col:
            continue

        proc_name = clean(row[proc_col]) if row[proc_col] else ""
        if not proc_name:
            proc_name = prev_proc
        else:
            prev_proc = proc_name

        if not proc_name:
            continue

        entry: dict[str, str] = {}

        if type_col is not None and type_col < len(row) and row[type_col]:
            t = clean(row[type_col])
            if t:
                entry["전형유형"] = t
                prev_type = t
        if prev_type and "전형유형" not in entry:
            entry["전형유형"] = prev_type

        if method_col is not None and method_col < len(row) and row[method_col]:
            entry["전형방법"] = clean(row[method_col])

        if note_col is not None and note_col < len(row) and row[note_col]:
            entry["비고"] = clean(row[note_col])

        if eff_quota_col is not None and eff_quota_col < len(row) and row[eff_quota_col]:
            entry["총모집인원"] = clean(row[eff_quota_col])

        if proc_name in result:
            for k, v in entry.items():
                if v and (k not in result[proc_name] or not result[proc_name][k]):
                    result[proc_name][k] = v
        else:
            result[proc_name] = entry

    return result if result else None


# ── Matrix table detection ─────────────────────────────────

def analyze_table(table: dict) -> dict | None:
    """Analyze a table to see if it's a matrix table (depts × 전형s).

    Returns a dict with col mapping, or None if not a matrix table.
    """
    headers_rows = table.get("headers", [])
    data = table.get("data", [])
    if not headers_rows or not data:
        return None

    dims = table.get("dimensions", {})
    ncols = dims.get("cols", 0)
    if ncols < 3:
        return None

    # Use the last (most specific) header row for column names
    last_headers = headers_rows[-1] if headers_rows else []
    # Pad to ncols
    while len(last_headers) < ncols:
        last_headers.append(None)

    # Also gather headers from all rows for context
    all_headers = []
    for hrow in headers_rows:
        padded = list(hrow) + [None] * (ncols - len(hrow))
        all_headers.append(padded)

    # Find structural columns
    dept_col = None
    track_col = None
    college_col = None
    structural_cols = set()

    headers_clean = [clean(h) if h else "" for h in last_headers]

    for i, h in enumerate(headers_clean):
        n = norm(h)
        if not n:
            continue
        if is_structural_col(h):
            structural_cols.add(i)
            if dept_col is None and any(k in n for k in ["모집단위"]):
                dept_col = i
            elif track_col is None and "계열" in n:
                track_col = i
            elif college_col is None and n in ("대학", "단과대학") or "단과대학" in n:
                college_col = i

    # If no 모집단위 column found, try to detect it from data
    if dept_col is None:
        for i, h in enumerate(headers_clean):
            n = norm(h)
            if "학과" in n or "학부" in n:
                dept_col = i
                structural_cols.add(i)
                break

    if dept_col is None:
        return None

    # Find 전형 columns: non-structural, with at least some numeric values
    process_cols = {}
    for i in range(ncols):
        if i in structural_cols:
            continue
        if i == dept_col or i == track_col or i == college_col:
            continue

        header_name = headers_clean[i]
        if not header_name:
            # Check earlier header rows
            for hrow in reversed(all_headers):
                if i < len(hrow) and hrow[i]:
                    header_name = clean(hrow[i])
                    break

        if not header_name:
            continue

        # Skip if header looks structural
        if is_structural_col(header_name):
            continue

        # Skip if header doesn't look like a valid 전형 name
        if not is_valid_process_name(header_name):
            continue

        # Check if this column has numeric-like data
        numeric_count = 0
        for row in data[:min(20, len(data))]:
            if i < len(row):
                q = parse_quota(row[i])
                if q is not None:
                    numeric_count += 1

        # Need at least some numeric values to be a 전형 column
        if numeric_count >= 1:
            process_cols[i] = header_name

    if not process_cols:
        return None

    return {
        "dept_col": dept_col,
        "track_col": track_col,
        "college_col": college_col,
        "process_cols": process_cols,  # {col_idx: 전형명}
        "headers_clean": headers_clean,
        "data": data,
    }


# ── Page text collection ────────────────────────────────────

def collect_process_pages(pages: list[dict],
                          process_names: set[str]) -> dict[str, list[tuple[int, str]]]:
    """For each 전형명, collect (page_num, text) tuples where that 전형 is mentioned.

    Keys the result by normalized process name (no spaces) so that "가천 바람 개비"
    and "가천바람개비" are treated as the same 전형.
    Short pages (< 30 chars) are skipped as they are likely blank or cover-only.
    Returns {norm(process_name): [(page_num, text), ...]}.
    """
    # Deduplicate by normalized name; prefer the shortest (least-spaced) original name
    norm_to_names: dict[str, str] = {}
    for name in process_names:
        n = norm(name)
        if n not in norm_to_names or len(name) < len(norm_to_names[n]):
            norm_to_names[n] = name

    result: dict[str, list[tuple[int, str]]] = {n: [] for n in norm_to_names}

    for page in pages:
        page_num = page["page_number"]
        text = page.get("text", "")
        if not text or len(text.strip()) < 30:
            continue
        text_norm = norm(text)

        for norm_name, orig_name in norm_to_names.items():
            # Exact match with original name first
            if orig_name in text:
                result[norm_name].append((page_num, text))
                continue
            # Normalized match for ≥ 3-char names (handles PDF spacing artifacts)
            if len(norm_name) >= 3 and norm_name in text_norm:
                result[norm_name].append((page_num, text))

    return result


def serialize_table_as_text(table: dict) -> str:
    """Render a table's headers and data as plain text for content inclusion."""
    parts = []
    headers_rows = table.get("headers", [])
    data = table.get("data", [])

    if headers_rows:
        for hrow in headers_rows:
            parts.append(" | ".join(str(c) if c is not None else "" for c in hrow))
        parts.append("-" * 40)

    for row in data:
        parts.append(" | ".join(str(c) if c is not None else "" for c in row))

    return "\n".join(parts)


# ── Extraction ─────────────────────────────────────────────

def extract_from_file(json_path: Path, university: str, admission_type: str,
                      store: AdmissionStore) -> dict:
    """Extract admission data from one JSON file and store it.

    Strategy:
    1. Pre-scan all tables for the overview table (전형명, 전형방법, 비고, 전형유형)
    2. Collect all 전형 names from overview + matrix tables
    3. Find all pages that mention each 전형 (for rich content)
    4. Extract quotas from matrix tables
    5. For each (dept, 전형) pair, store: quota + overview info + all relevant page texts
    """
    with open(json_path, "r", encoding="utf-8") as f:
        doc = json.load(f)

    pages = doc.get("pages", [])
    stats = {"departments": 0, "processes": 0, "tables_found": 0}
    dept_cache: dict[tuple, int] = {}  # (university, dept_name) -> dept_id

    # ── Phase 1: Pre-scan for overview table ──────────────────
    # Keyed by norm(전형명) for consistent lookup despite spacing differences
    process_overview: dict[str, dict] = {}  # {norm(전형명): {전형방법, 비고, 전형유형, 총모집인원}}

    for page in pages:
        for table in page.get("tables", []):
            overview = detect_overview_table(table)
            if overview:
                for pname, pdata in overview.items():
                    nk = norm(pname)
                    if nk not in process_overview:
                        process_overview[nk] = pdata
                    else:
                        # Merge: fill in missing keys only
                        for k, v in pdata.items():
                            if v and (k not in process_overview[nk] or not process_overview[nk][k]):
                                process_overview[nk][k] = v

    # ── Phase 2: Build canonical name set ─────────────────────
    # The canonical form for all 전형 names is norm(name) — no spaces.
    # Korean 전형 names have no natural spaces; spaces in matrix/overview tables
    # are PDF extraction artifacts (e.g., "기 회 균 형" → "기회균형").
    # process_overview is already keyed by norm(name) from Phase 1.

    found_process_names: set[str] = set(process_overview.keys())

    for page in pages:
        for table in page.get("tables", []):
            info = analyze_table(table)
            if info:
                for raw_name in info["process_cols"].values():
                    found_process_names.add(norm(raw_name))

    # ── Phase 3: Collect ALL page texts per 전형 ─────────────
    # Returns {norm(전형명): [(page_num, text), ...]}
    process_page_texts = collect_process_pages(pages, found_process_names)

    # ── Phase 4: Extract matrix tables and store ──────────────
    for page in pages:
        page_num = page["page_number"]
        for table in page.get("tables", []):
            info = analyze_table(table)
            if info is None:
                continue

            stats["tables_found"] += 1
            dept_col = info["dept_col"]
            track_col = info["track_col"]
            college_col = info["college_col"]
            process_cols = info["process_cols"]
            data = info["data"]

            # Carry-forward state for merged cells
            prev_dept = ""
            prev_track = ""
            prev_college = ""

            for row in data:
                if is_total_row(row):
                    continue

                # Extract department name
                dept_name = collapse_spaced_text(clean(row[dept_col])) if dept_col < len(row) and row[dept_col] else ""
                if dept_name:
                    prev_dept = dept_name
                else:
                    dept_name = prev_dept
                if not dept_name:
                    continue

                # Skip if dept_name looks like a header or is too long (multi-dept merged cells)
                if norm(dept_name) in STRUCTURAL_KEYWORDS:
                    continue
                if norm(dept_name) in TOTAL_ROW_KEYWORDS:
                    continue
                if len(dept_name) > 50:
                    continue
                if re.match(r"^\d+점$", norm(dept_name)):
                    continue
                if dept_name[0] in "⑩⑪⑫⑬⑭⑮‣※◆◇►▶○●□■☐☑":
                    continue
                if re.match(r"^[\d\s.,]+$", dept_name):
                    continue

                # Extract track
                track = ""
                if track_col is not None and track_col < len(row) and row[track_col]:
                    track = clean(row[track_col])
                    prev_track = track
                else:
                    track = prev_track

                # Extract college
                college = ""
                if college_col is not None and college_col < len(row) and row[college_col]:
                    college = clean(row[college_col])
                    prev_college = college
                else:
                    college = prev_college

                # Upsert department
                dept_key = (university, dept_name)
                if dept_key not in dept_cache:
                    dept_id = store.upsert_department(
                        year=2026,
                        university=university,
                        campus=None,
                        track=track or None,
                        name=dept_name,
                    )
                    dept_cache[dept_key] = dept_id
                    stats["departments"] += 1
                else:
                    dept_id = dept_cache[dept_key]

                # Extract process records
                for col_idx, raw_process_name in process_cols.items():
                    if col_idx >= len(row):
                        continue
                    quota = parse_quota(row[col_idx])
                    if quota is None or quota > 500:
                        continue

                    # Use norm form as the canonical process name (no PDF spacing artifacts)
                    proc_norm = norm(raw_process_name)
                    process_name = proc_norm  # always store without spaces
                    overview_info = process_overview.get(proc_norm, {})
                    process_type = classify_process_type(
                        process_name,
                        overview_type=overview_info.get("전형유형", "")
                    )

                    # ── Build rich content (no truncation) ──────────────
                    # Header block: key structured facts
                    header_lines = [
                        f"대학: {university}",
                        f"모집단위: {dept_name}",
                    ]
                    if track:
                        header_lines.append(f"계열: {track}")
                    if college:
                        header_lines.append(f"단과대학: {college}")
                    header_lines += [
                        f"전형명: {process_name}",
                        f"전형유형: {overview_info.get('전형유형', process_type)}",
                        f"모집시기: {admission_type}",
                        f"모집인원: {quota}명",
                    ]
                    if overview_info.get("총모집인원"):
                        header_lines.append(f"전체모집인원(전형 전체): {overview_info['총모집인원']}명")
                    if overview_info.get("전형방법"):
                        header_lines.append(f"전형방법: {overview_info['전형방법']}")
                    if overview_info.get("비고"):
                        header_lines.append(f"비고: {overview_info['비고']}")

                    content_parts = ["\n".join(header_lines)]

                    # Raw page texts — every page that mentions this 전형
                    # (keyed by norm(process_name) in process_page_texts)
                    relevant_pages = process_page_texts.get(proc_norm, [])
                    if relevant_pages:
                        content_parts.append("\n" + "=" * 60)
                        content_parts.append(f"원문 발췌 ({len(relevant_pages)}페이지)")
                        content_parts.append("=" * 60)
                        for pg_num, pg_text in relevant_pages:
                            content_parts.append(f"\n--- p{pg_num} ---\n{pg_text}")

                    content = "\n".join(content_parts)

                    # ── Build attributes (structured, for quick access) ──
                    attributes: dict[str, Any] = {}
                    if track:
                        attributes["계열"] = track
                    if college:
                        attributes["단과대학"] = college
                    for attr_key in ("전형유형", "전형방법", "비고", "총모집인원"):
                        val = overview_info.get(attr_key)
                        if val:
                            attributes[attr_key] = val

                    # C1: parse 수능최저학력기준 from content
                    suneung_min = parse_수능최저(content)
                    if suneung_min is not None:
                        attributes["수능최저"] = suneung_min

                    store.upsert_process(
                        department_id=dept_id,
                        process_name=process_name,
                        process_type=process_type,
                        admission_type=admission_type,
                        quota=quota,
                        content=content,
                        attributes=attributes,
                    )
                    stats["processes"] += 1

    return stats


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Extract admission data from JSON files")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of files to process (0 = all)")
    parser.add_argument("--university", type=str, default="", help="Process only this university")
    parser.add_argument("--dry-run", action="store_true", help="Don't store, just analyze")
    args = parser.parse_args()

    extracted_dir = Path("data/extracted")
    if args.dry_run:
        store = None
    else:
        store = AdmissionStore()

    files = sorted(extracted_dir.glob("*_모집요강.json"))
    # Filter out non-standard files
    files = [f for f in files if "_수시_모집요강.json" in f.name or "_정시_모집요강.json" in f.name]

    if args.university:
        files = [f for f in files if f.name.startswith(args.university)]

    if args.limit:
        files = files[:args.limit]

    print(f"Processing {len(files)} files...\n")

    total_depts = 0
    total_procs = 0
    total_tables = 0
    results = []
    t0 = time.time()

    for i, fpath in enumerate(files, 1):
        # Parse university and admission_type from filename
        name = fpath.stem  # e.g. "가천대학교_수시_모집요강"
        parts = name.rsplit("_", 2)
        if len(parts) < 3:
            print(f"[{i}/{len(files)}] SKIP (bad name): {fpath.name}")
            continue
        university = parts[0]
        admission_type = parts[1]  # 수시 or 정시

        if args.dry_run:
            # Just analyze tables without storing
            with open(fpath, "r", encoding="utf-8") as f:
                doc = json.load(f)
            table_count = 0
            overview_count = 0
            for page in doc.get("pages", []):
                for table in page.get("tables", []):
                    if analyze_table(table):
                        table_count += 1
                    if detect_overview_table(table):
                        overview_count += 1
            print(f"[{i}/{len(files)}] {university} {admission_type}: "
                  f"{table_count} matrix tables, {overview_count} overview tables")
            total_tables += table_count
            continue

        stats = extract_from_file(fpath, university, admission_type, store)
        total_depts += stats["departments"]
        total_procs += stats["processes"]
        total_tables += stats["tables_found"]
        results.append((university, admission_type, stats))

        print(f"[{i}/{len(files)}] {university} {admission_type}: "
              f"{stats['departments']}d {stats['processes']}p ({stats['tables_found']}t)")

    elapsed = time.time() - t0

    print(f"\n{'='*60}")
    print(f"Total: {total_depts} departments, {total_procs} processes "
          f"from {total_tables} tables in {elapsed:.1f}s")

    if not args.dry_run and store:
        db_stats = store.stats()
        print(f"\nDB stats: {db_stats}")

    # Show universities with 0 processes (potential issues)
    if results:
        zero_procs = [(u, a) for u, a, s in results if s["processes"] == 0]
        if zero_procs:
            print(f"\n⚠ {len(zero_procs)} files with 0 processes extracted:")
            for u, a in zero_procs[:20]:
                print(f"  - {u} ({a})")


if __name__ == "__main__":
    main()
