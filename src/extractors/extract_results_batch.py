"""Parse extracted results JSON files and store structured data into AdmissionStore.

Handles various column layouts:
- Row-based (가천대형): each row is one (전형, 학과, scores) record
- Multi-level headers (건국대형, 경북대형): parent + child headers
- Section-header context (건국대형): 전형명 from page text above the table
- Multi-year columns (아주대형): takes only the first year's columns

Score types detected:
- 등급 (학생부 내신 1.0-9.0, lower=better) - mostly 수시
- 표준점수 - mostly 정시
- 백분위 - mostly 정시
- 환산점수 - computed score (university-specific)

Output columns in admission_result:
  average_score, cut_50, cut_60, cut_70, cut_80, cut_85, cut_90
  score_type (등급 / 표준점수 / 백분위 / 환산점수)
  competition_rate
"""

import io
import json
import re
import sys
from pathlib import Path
from typing import Any

if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, ".")
from src.storage.admission_store import AdmissionStore

RESULTS_EXTRACTED_DIR = Path("data/results_extracted")


# ── Utilities ─────────────────────────────────────────────────────────────────

def norm(s: str | None) -> str:
    """Remove all whitespace."""
    return re.sub(r"\s+", "", str(s)) if s else ""


def clean(s: str | None) -> str:
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", str(s)).strip() if s else ""


def is_numeric(s: str | None) -> bool:
    if not s:
        return False
    n = norm(s)
    return bool(re.match(r'^-?[\d,]+\.?\d*$', n))


def parse_float(s: str | None) -> float | None:
    if not s:
        return None
    n = norm(str(s)).replace(",", "")
    try:
        v = float(n)
        return None if v <= 0 or v > 1_000_000 else v
    except ValueError:
        return None


def parse_year_type_from_filename(filename: str) -> tuple[int | None, str | None]:
    """Extract (year, admission_type) from filename.

    Examples:
      가천대학교_2025학년도_수시입시결과.pdf → (2025, 수시)
      2024학년도 입시결과_240602.pdf → (2024, None)
      2023-2025+입학성적+결과집.pdf → (2025, None)  # take last year
    """
    year = None
    admission_type = None

    # Find year
    years = re.findall(r'(20\d\d)학년도', filename)
    if years:
        year = int(years[-1])  # take the last mentioned year
    else:
        years = re.findall(r'(20\d\d)', filename)
        if years:
            year = int(years[-1])

    # Find admission type
    if '수시' in filename:
        admission_type = '수시'
    elif '정시' in filename:
        admission_type = '정시'

    return year, admission_type


# ── Column Detection ──────────────────────────────────────────────────────────

# Patterns to detect column types
DEPT_PATTERNS = [
    r'모집단위', r'^학과$', r'^전공$', r'^학부$',
    r'학과명', r'전공명', r'학부명', r'모집단위명',
    r'계열및모집단위',
    r'^모집단위\s*\(',  # e.g., 모집단위(학부)
    r'^학부\(과\)$',   # e.g., 학부(과) — common in 항공대 style tables
    r'^학과\(부\)$',   # e.g., 학과(부)
    r'^학과.{0,3}전공$',  # e.g., 학과/전공
]
PROCESS_PATTERNS = [
    r'전형명', r'모집전형', r'세부전형', r'^전형$', r'전형구분', r'전형유형',
]
ADM_TYPE_PATTERNS = [
    r'^모집시기$', r'^모집구분$',
]
RATE_PATTERNS = [r'경쟁률', r'경쟁율']
QUOTA_PATTERNS = [r'모집인원', r'정원', r'모집.{0,3}인원']
APPLICANT_PATTERNS = [r'지원인원', r'지원자수', r'지원.{0,3}인원']
ENROLLED_PATTERNS = [r'등록인원', r'등록자수', r'최종등록인원', r'등록.{0,3}인원']
WAITLIST_PATTERNS = [r'충원합격.{0,10}인원', r'충원인원']
ADMIT_RANK_PATTERNS = [r'충원합격.{0,5}순위', r'충원.{0,3}순위', r'최종합격.{0,10}순위']

# Score type hints in column names
GRADE_HINTS = ['등급', '내신', '학생부']
STD_SCORE_HINTS = ['표준점수']
PERCENTILE_HINTS = ['백분위']
CONVERTED_HINTS = ['환산점수', '환산', '교과환산']

PCT_PATTERN = re.compile(r'(50|60|70|80|85|90)\s*%')
AVG_PATTERN = re.compile(r'평균')
YEAR_PATTERN = re.compile(r'20\d\d학년도|20\d\d년도')


def match_any(text: str, patterns: list[str]) -> bool:
    t = norm(text)
    return any(re.search(p, t) for p in patterns)


def detect_score_type_from_hints(col_label: str) -> str | None:
    """Detect score type from column label content."""
    l = norm(col_label)
    # Check 환산 before 학생부/등급 — '학생부 환산점수' must not be misclassified as '등급'
    if any(h in l for h in CONVERTED_HINTS):
        return '환산점수'
    if any(h in l for h in STD_SCORE_HINTS):
        return '표준점수'
    if any(h in l for h in PERCENTILE_HINTS):
        return '백분위'
    if any(h in l for h in GRADE_HINTS):
        return '등급'
    return None


def detect_pct_cut(col_label: str) -> int | str | None:
    """Extract percentage cutoff from column label. Returns int or 'avg' or None."""
    n = norm(col_label)
    m = PCT_PATTERN.search(n)
    if m:
        return int(m.group(1))
    # Also match plain '평균' without nearby percentage
    if AVG_PATTERN.search(n) and not PCT_PATTERN.search(n):
        return 'avg'
    return None


def flatten_headers(rows: list[list]) -> tuple[int, list[str]]:
    """Find header rows and flatten multi-level headers into column labels.

    Returns: (first_data_row_idx, column_labels)
    """
    n_rows = len(rows)
    if n_rows == 0:
        return 0, []

    max_cols = max(len(r) for r in rows)

    # Determine how many header rows there are (up to 5)
    # Header row: less than 50% numeric cells
    header_count = 0
    for i in range(min(5, n_rows)):
        row = rows[i]
        non_null = [c for c in row if c is not None]
        if not non_null:
            header_count = i + 1
            continue
        numeric = sum(1 for c in non_null if is_numeric(c))
        if numeric / len(non_null) >= 0.5:
            break
        header_count = i + 1

    if header_count == 0:
        header_count = 1  # at least 1 header row

    # Build flat column labels by concatenating values from all header rows
    # Forward-fill None values in each row separately
    header_rows = rows[:header_count]

    # First, forward-fill None cells in each row
    filled_rows = []
    for row in header_rows:
        filled = []
        last = None
        for cell in row:
            if cell is not None:
                last = cell
                filled.append(cell)
            else:
                filled.append(last)  # forward-fill merged cells to propagate parent header context
        # Pad to max_cols
        while len(filled) < max_cols:
            filled.append(None)
        filled_rows.append(filled)

    # For each column, combine non-None values from all header rows
    col_labels = []
    for ci in range(max_cols):
        parts = []
        for row in filled_rows:
            if ci < len(row) and row[ci] is not None:
                parts.append(clean(row[ci]))
        col_labels.append(' | '.join(parts) if parts else '')

    return header_count, col_labels


def build_col_map(col_labels: list[str], year_context: int | None = None) -> dict[str, int]:
    """Build a map from role → column index based on column labels.

    Roles: 'dept', 'process', 'adm_type', 'rate', 'quota',
           'avg', 'cut_50', 'cut_60', 'cut_70', 'cut_80', 'cut_85', 'cut_90',
    Also: 'year_start_{year}' for multi-year tables.

    For multi-year tables, returns only the first year's columns.
    """
    col_map: dict[str, int] = {}

    # Find year markers to handle multi-year columns
    year_cols = {}
    for i, label in enumerate(col_labels):
        m = YEAR_PATTERN.search(norm(label))
        if m:
            y = int(re.search(r'20\d\d', m.group()).group())
            if y not in year_cols:
                year_cols[y] = i

    # If multiple years found, restrict to the latest year's columns
    year_range = None
    if len(year_cols) >= 2:
        years_sorted = sorted(year_cols.keys(), reverse=True)
        latest_year = years_sorted[0]
        next_year_col = year_cols.get(years_sorted[1], len(col_labels))
        # Columns for latest year are between year_cols[latest] and year_cols[next]
        year_start = year_cols[latest_year]
        year_end = next_year_col if next_year_col > year_start else len(col_labels)
        year_range = (year_start, year_end)

    # Score type tracking: prefer 등급 over others
    score_cols: list[tuple[int, str, int | str]] = []  # (col_idx, score_type, pct)

    for i, label in enumerate(col_labels):
        # Skip if outside year range (for multi-year tables)
        if year_range and not (year_range[0] <= i < year_range[1]):
            # Allow dept/process/rate columns before the year range
            if i >= year_range[0]:
                pass  # skip this col

        ln = norm(label)
        if not ln:
            continue

        # Dept
        if 'dept' not in col_map and match_any(label, DEPT_PATTERNS):
            col_map['dept'] = i
            continue

        # Process name
        if 'process' not in col_map and match_any(label, PROCESS_PATTERNS):
            col_map['process'] = i
            continue

        # Admission type (수시/정시)
        if 'adm_type' not in col_map and match_any(label, ADM_TYPE_PATTERNS):
            col_map['adm_type'] = i
            continue

        # Competition rate
        if 'rate' not in col_map and match_any(label, RATE_PATTERNS):
            col_map['rate'] = i
            continue

        # Quota (모집인원)
        if 'quota' not in col_map and match_any(label, QUOTA_PATTERNS):
            col_map['quota'] = i
            continue

        # Applicants (지원자수)
        if 'applicants' not in col_map and match_any(label, APPLICANT_PATTERNS):
            col_map['applicants'] = i
            continue

        # Enrolled (등록인원)
        if 'enrolled' not in col_map and match_any(label, ENROLLED_PATTERNS):
            col_map['enrolled'] = i
            continue

        # Waitlist admissions (충원합격인원)
        if 'waitlist' not in col_map and match_any(label, WAITLIST_PATTERNS):
            col_map['waitlist'] = i
            continue

        # Waitlist admit rank (충원합격순위)
        if 'admit_rank' not in col_map and match_any(label, ADMIT_RANK_PATTERNS):
            col_map['admit_rank'] = i
            continue

        # Score columns
        score_type = detect_score_type_from_hints(label)
        pct = detect_pct_cut(label)

        if pct is not None:
            score_cols.append((i, score_type, pct))

    # Among score columns, prefer 등급 ones; if none, take any
    def role_for_pct(pct):
        if pct == 'avg':
            return 'avg'
        return f'cut_{pct}'

    # First pass: assign explicitly-typed 등급 score columns
    for (i, stype, pct) in score_cols:
        if stype == '등급':
            role = role_for_pct(pct)
            if role not in col_map:
                col_map[role] = i

    # Second pass: fill in remaining roles from unlabeled or non-등급 columns
    # (unlabeled percentage columns inherit from context)
    for (i, stype, pct) in score_cols:
        role = role_for_pct(pct)
        if role not in col_map:
            col_map[role] = i

    return col_map


def dominant_score_type(col_labels: list[str], col_map: dict[str, int],
                         file_adm_type: str | None) -> str:
    """Infer the dominant score type for this table."""
    score_col_idxs = [v for k, v in col_map.items() if k.startswith('cut_') or k == 'avg']
    for ci in score_col_idxs:
        t = detect_score_type_from_hints(col_labels[ci])
        if t:
            return t
    # Fall back to admission type heuristic
    if file_adm_type == '수시':
        return '등급'
    elif file_adm_type == '정시':
        return '표준점수'
    return '등급'  # default


# ── Section Header Extraction ─────────────────────────────────────────────────

def find_process_context_from_text(page_text: str) -> str | None:
    """Extract 전형명 from page text section headers.

    Looks for patterns like:
      ▷ 학생부교과(KU지역균형)
      ■ 학생부종합전형
      [학생부교과] 일반전형
      Ⅰ2024학년도 학생부교과(교과성적우수자전형)   ← Roman numeral headers
      □ 학생부종합(계열모집)                       ← bullets without 전형 suffix
    """
    # 1. Bracket pattern: [전형유형] 전형명
    # Require '전형' in the text after the bracket to avoid matching dept track tags like [인문]\n3
    m = re.search(r'\[([^\]]+)\]\s*([가-힣\w\s·]*전형[가-힣\w()（）]*)', page_text)
    if m:
        return clean(m.group(2)) or clean(m.group(1))

    # 2. Roman numeral section header: Ⅰ/Ⅱ/Ⅲ... (optional 20XX학년도) 전형명
    # e.g. "Ⅰ2024학년도 학생부교과(교과성적우수자전형)"
    m = re.search(
        r'[Ⅰ-Ⅻ①-⑫]\s*(?:20\d\d학년도\s*)?([가-힣\w()（）·\s\-]+전형[가-힣\w()（）]*)',
        page_text[:400],
    )
    if m:
        candidate = clean(m.group(1).strip())
        if is_valid_process_name(candidate):
            return candidate

    # 3. Arrow/bullet pattern (requires 전형 in name — general case)
    m = re.search(r'[▷▶■□◆●◎]\s*([가-힣\w\s()（）·\-]+전형[가-힣\w()（）]*)', page_text)
    if m:
        return clean(m.group(1))

    # 4. Bullet pattern for 학생부/수능 without 전형 suffix
    # e.g. "□ 학생부종합(계열모집)", "▷ 수능(KU일반학생) 가군"
    m = re.search(
        r'[▷▶■□◆●◎]\s*((?:학생부(?:종합|교과)|수능|논술|실기)[가-힣\w\s()（）·\-]*)',
        page_text,
    )
    if m:
        raw = m.group(1).split('\n')[0].strip()
        # Strip trailing 군 division markers (가군/나군/다군)
        raw = re.sub(r'\s+[가나다]군$', '', raw).strip()
        candidate = clean(raw)
        if is_valid_process_name(candidate):
            return candidate

    # 5. "~전형" pattern near start of page
    m = re.search(r'([가-힣\w()（）]+전형)', page_text[:200])
    if m:
        return clean(m.group(1))

    return None


# ── Row Parsing ───────────────────────────────────────────────────────────────

SKIP_PROCESS_NAMES = {
    '', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9',
    '수시', '정시', '-', '·', '없음', '해당없음', '미해당',
    '최고', '최저', '평균', '합계', '소계',
    '모집단위', '학과', '전공', '학부', '대학',
    '모집인원', '지원인원', '경쟁률', '충원인원', '입학인원',
    '비고', '순위', '합격자', '전형별',
}


def is_valid_process_name(name: str | None) -> bool:
    """Validate that a process name is a real 전형 name, not a spurious value."""
    if not name:
        return False
    n = clean(name)
    if not n:
        return False
    nn = norm(n)
    # Purely numeric
    if re.match(r'^\d+$', nn):
        return False
    # Too short (likely a column value, not a 전형명)
    if len(n) <= 2:
        return False
    # Too long (likely a document title, not a 전형명)
    if len(n) > 30:
        return False
    # Starts with a year (document title like '2024학년도경북대학교...')
    if re.match(r'^20\d\d', n):
        return False
    # Contains year context like "2024학년도" → section header, not a 전형명
    # e.g. "Ⅰ 2024학년도 전형결과" embedded as a table cell value
    if re.search(r'20\d\d학년도', n):
        return False
    # '전형별' suffix indicates a section descriptor, not a specific 전형 name
    if nn.endswith('전형별'):
        return False
    # '전형결과' suffix (and similar section labels) are not 전형 names
    if nn.endswith('전형결과'):
        return False
    # Check against known-bad process names (apply to context lookups too)
    if nn in {norm(s) for s in SKIP_PROCESS_NAMES}:
        return False
    return True


SKIP_DEPT_NAMES = {
    '합계', '소계', '계', '합', 'total', '전체', '총계', '정원내', '정원외',
    '수시계', '정시계', '예체능계', '인문계', '자연계', '의약학계',
    '소계(인문계)', '소계(자연계)', '소계(의학계)', '소계(예체능계)',
}
# Large junk cells (headlines, merged info cells)
MAX_DEPT_LEN = 40


def collapse_spaced_text(text: str) -> str:
    """Collapse character-spaced Korean text from pdfplumber font artifacts.

    e.g. '정 보 통 신 공 학 과' → '정보통신공학과'
    Only collapses when every non-space segment is exactly 1 character.
    """
    stripped = text.strip()
    if re.fullmatch(r'(\S )+\S', stripped):
        return stripped.replace(' ', '')
    return text


def clean_dept_name(name: str | None) -> str | None:
    """Clean department name: strip leading parenthetical rename annotations,
    and collapse character-spaced text from PDF font artifacts.

    e.g., '(2025:경영학과)경영학부' → '경영학부'
         '(→경영학과)경영학부' → '경영학부'
         '정 보 통 신 공 학 과' → '정보통신공학과'
    """
    if not name:
        return name
    # Strip leading (annotation)text → take the text after the parenthetical
    m = re.match(r'^\([^)]+\)\s*(.+)', name)
    result = clean(m.group(1)) if m else clean(name)
    return collapse_spaced_text(result)


def is_valid_dept_name(name: str | None) -> bool:
    if not name:
        return False
    n = clean(name)
    if not n or len(n) > MAX_DEPT_LEN:
        return False
    if norm(n).lower() in SKIP_DEPT_NAMES:
        return False
    # Must contain at least one Korean character
    if not re.search(r'[가-힣]', n):
        return False
    # Skip merged multi-dept cells (e.g., "간호학과*** 바이오의약학과 생명공학과")
    # Detect by: multiple 학과/학부/전공 occurrences OR comma-separated names
    dept_terms = re.findall(r'학과|학부|전공', n)
    if len(dept_terms) >= 2:
        return False
    # Comma-separated multi-dept
    if ',' in n and re.search(r'[가-힣]', n):
        return False
    # Parenthetical dept with annotations that slipped through
    if re.search(r'\([^)]*대학[^)]*\)', n):
        return False
    return True


def extract_table_records(
    table_data: list[list],
    col_map: dict[str, int],
    file_adm_type: str | None,
    score_type: str,
    process_context: str | None,
) -> list[dict]:
    """Extract records from a table's data rows using the column map."""
    records = []

    last_dept = None
    last_process = None
    last_adm_type = file_adm_type

    for row in table_data:
        # Get dept name (forward-fill from previous row)
        dept_raw = row[col_map['dept']] if 'dept' in col_map and col_map['dept'] < len(row) else None
        dept = clean_dept_name(dept_raw) if dept_raw else None
        if dept:
            last_dept = dept
        else:
            dept = last_dept

        if not is_valid_dept_name(dept):
            continue

        # Get process name
        proc_raw = row[col_map['process']] if 'process' in col_map and col_map['process'] < len(row) else None
        proc = clean(proc_raw) if proc_raw else None
        proc_skip = norm(proc) in {norm(s) for s in SKIP_PROCESS_NAMES} if proc else True
        if proc and not proc_skip and is_valid_process_name(proc):
            last_process = proc
        else:
            proc = last_process or process_context or '(전형미상)'

        # Final validation of process name
        if not is_valid_process_name(proc):
            # process_context may also be invalid (e.g., '3' from dept track tags)
            ctx = process_context if is_valid_process_name(process_context) else None
            proc = ctx or '(전형미상)'

        # Get admission type
        adm_raw = row[col_map['adm_type']] if 'adm_type' in col_map and col_map['adm_type'] < len(row) else None
        adm = clean(adm_raw) if adm_raw else None
        if adm and adm in ('수시', '정시'):
            last_adm_type = adm
        adm = last_adm_type or file_adm_type

        def get_score(role: str) -> float | None:
            ci = col_map.get(role)
            if ci is None or ci >= len(row):
                return None
            return parse_float(row[ci])

        def get_int(role: str) -> int | None:
            v = get_score(role)
            if v is None:
                return None
            iv = int(round(v))
            return iv if 0 < iv < 100_000 else None

        # Extract competition rate
        rate = get_score('rate')

        # Extract supplementary integer fields
        quota = get_int('quota')
        applicants = get_int('applicants')
        enrolled = get_int('enrolled')
        waitlist = get_int('waitlist')
        admit_rank = get_int('admit_rank')

        # Extract score fields
        average = get_score('avg')
        cut_50 = get_score('cut_50')
        cut_60 = get_score('cut_60')
        cut_70 = get_score('cut_70')
        cut_80 = get_score('cut_80')
        cut_85 = get_score('cut_85')
        cut_90 = get_score('cut_90')

        # For 등급 scores: valid range is 1.0-9.0 (allow up to 10 for safety)
        # For 표준점수: valid range ~200-900
        # For 환산점수: varies but usually 500-1000
        # Basic sanity filter
        def sane(v: float | None) -> float | None:
            if v is None:
                return None
            if score_type == '등급' and not (0.5 <= v <= 10.0):
                return None
            if score_type in ('표준점수', '백분위') and v < 10:
                return None
            return v

        average = sane(average)
        cut_50 = sane(cut_50)
        cut_60 = sane(cut_60)
        cut_70 = sane(cut_70)
        cut_80 = sane(cut_80)
        cut_85 = sane(cut_85)
        cut_90 = sane(cut_90)

        # Only emit if we have at least one useful score, rate, or count field
        if not any([average, cut_50, cut_60, cut_70, cut_80, cut_85, cut_90,
                    rate, quota, applicants, enrolled, waitlist, admit_rank]):
            continue

        # If no score values at all, don't store score_type to avoid overwriting
        # correct score_type in duplicate UPSERT scenarios (e.g. multi-sheet XLSX)
        has_scores = any([average, cut_50, cut_60, cut_70, cut_80, cut_85, cut_90])
        effective_score_type = score_type if has_scores else None

        # Post-hoc: if classified as 표준점수 but all score values < 200,
        # it's almost certainly 백분위 (표준점수 ranges ~200-900)
        if effective_score_type == '표준점수' and has_scores:
            score_vals = [v for v in [average, cut_50, cut_60, cut_70,
                                       cut_80, cut_85, cut_90] if v is not None]
            if score_vals and max(score_vals) < 200:
                effective_score_type = '백분위'

        records.append({
            'dept': dept,
            'process': proc,
            'adm_type': adm,
            'score_type': effective_score_type,
            'rate': rate,
            'average': average,
            'cut_50': cut_50,
            'cut_60': cut_60,
            'cut_70': cut_70,
            'cut_80': cut_80,
            'cut_85': cut_85,
            'cut_90': cut_90,
            'quota': quota,
            'applicants': applicants,
            'enrolled': enrolled,
            'waitlist': waitlist,
            'admit_rank': admit_rank,
        })

    return records


# ── Main Table Processor ──────────────────────────────────────────────────────

def process_table(table: dict, page_text: str, file_adm_type: str | None,
                  year_context: int | None,
                  fallback_process_ctx: str | None = None) -> list[dict]:
    """Process one table from a PDF page. Returns extracted records.

    fallback_process_ctx: 전형명 carried over from a previous page when this
    page has no section header (e.g. 경북대학교 정시 multi-page tables).
    """
    data = table.get('data', [])
    if len(data) < 2:
        return []

    header_end, col_labels = flatten_headers(data)
    if not col_labels:
        return []

    col_map = build_col_map(col_labels, year_context)

    # Must have at least a dept column and some score/rate column
    has_score = any(k.startswith('cut_') or k == 'avg' for k in col_map)
    has_rate = 'rate' in col_map
    if 'dept' not in col_map or (not has_score and not has_rate):
        return []

    score_type = dominant_score_type(col_labels, col_map, file_adm_type)

    # Extract process context: prefer col_labels (table-specific) over page text
    process_ctx = None
    if 'process' not in col_map:
        # Check if any column label embeds a 전형명 (e.g., '국민프런티어전형 | 모집인원')
        for label in col_labels:
            m = re.search(r'([가-힣\w()（）]+전형[가-힣\w()（）]*)', norm(label))
            if m:
                candidate = clean(m.group(1))
                if is_valid_process_name(candidate):
                    process_ctx = candidate
                    break
    if not process_ctx:
        process_ctx = find_process_context_from_text(page_text)
    # Cross-page fallback: use the last known context from a previous page
    if not process_ctx and fallback_process_ctx:
        process_ctx = fallback_process_ctx

    data_rows = data[header_end:]
    return extract_table_records(data_rows, col_map, file_adm_type, score_type, process_ctx)


def process_xlsx_sheet(sheet: dict, file_adm_type: str | None,
                       year_context: int | None) -> list[dict]:
    """Process one XLSX sheet. Same logic as PDF table."""
    return process_table(sheet, '', file_adm_type, year_context)


# ── Special Format: 제주대학교 Text-Columnar ──────────────────────────────────

def is_jeju_format(doc: dict) -> bool:
    """Detect 제주대학교 text-only columnar format (no tables, starts with 17-line header)."""
    pages = doc.get('pages', [])
    if not pages:
        return False
    first_page = pages[0]
    if first_page.get('tables'):
        return False
    text = first_page.get('text', '')
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return (len(lines) >= 3
            and lines[0] == '단과대학'
            and lines[1] == '모집단위'
            and lines[2] == '모집시기')


def parse_jeju_text(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 제주대학교 text-only columnar format.

    Each page repeats a 17-line header. Data records are either:
    - 17 lines: 10 base fields + score-type label + 6 score fields
    - 10 lines:  base fields only (no score row)
    Each dept+전형 appears in 3 rows: 1.평균, 2.50컷, 3.70컷.
    """
    SCORE_LABELS = {'1. 평균', '2. 50컷', '3. 70컷'}
    HEADER_FIRST = '단과대학'
    HEADER_LEN = 17

    raw_records: list[tuple] = []

    for page in doc.get('pages', []):
        text = page.get('text', '')
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        i = 0
        while i < len(lines):
            # Skip repeating page header
            if lines[i] == HEADER_FIRST and i + HEADER_LEN <= len(lines):
                i += HEADER_LEN
                continue
            # Need at least 10 base fields
            if i + 10 > len(lines):
                break
            base = lines[i:i + 10]
            i += 10
            # Check if next line is a score label
            if i < len(lines) and lines[i] in SCORE_LABELS:
                score_label = lines[i]
                i += 1
                scores = lines[i:i + 6] if i + 6 <= len(lines) else lines[i:]
                i += min(6, len(lines) - (i - 1))
                raw_records.append((base, score_label, scores))
            else:
                raw_records.append((base, None, None))

    # Group records by (dept, process_name), collecting 평균/50컷/70컷
    groups: dict[tuple, dict] = {}

    for (base, score_label, scores) in raw_records:
        if len(base) < 10:
            continue
        dept = base[1]
        time_type = base[2]   # e.g. "수시(학생부교과)" or "정시(다군)"
        track = base[3]        # e.g. "일반학생" or "농어촌학생"
        rate_str = base[7]
        if not dept or not track:
            continue

        # Build process_name: sub_type + track to avoid UNIQUE collisions
        m = re.match(r'(?:수시|정시)\((.+)\)', time_type)
        sub_type = m.group(1) if m else ''
        process_name = f"{sub_type} {track}".strip() if sub_type else track

        rec_adm_type = adm_type
        if not rec_adm_type:
            if time_type.startswith('수시'):
                rec_adm_type = '수시'
            elif time_type.startswith('정시'):
                rec_adm_type = '정시'

        key = (dept, process_name)
        if key not in groups:
            groups[key] = {
                'dept': dept,
                'process': process_name,
                'adm_type': rec_adm_type,
                'rate': parse_float(rate_str),
                'quota': parse_float(base[5]),
                'average': None,
                'cut_50': None,
                'cut_70': None,
                'score_type': None,
            }

        if score_label and scores:
            score_val = scores[0] if scores else None
            parsed = parse_float(score_val)
            # Determine score_type from time_type
            if time_type.startswith('수시'):
                groups[key]['score_type'] = '등급'
            elif time_type.startswith('정시'):
                groups[key]['score_type'] = '백분위'
            if score_label == '1. 평균':
                groups[key]['average'] = parsed
            elif score_label == '2. 50컷':
                groups[key]['cut_50'] = parsed
            elif score_label == '3. 70컷':
                groups[key]['cut_70'] = parsed

    # Only return groups with at least one score
    return [v for v in groups.values() if v['score_type'] is not None]


# ── Special Format: 3-Table Aligned (충북대학교 style) ────────────────────────

def is_three_table_format(doc: dict) -> bool:
    """Detect 3-aligned-tables-per-page format (충북대학교 style).

    Signature: Table 1 has 7-col header [단과대학, 모집단위, 계열, ...],
    Table 3 header row[0] == '최종등록자'.
    """
    pages = doc.get('pages', [])
    for pg in pages[:5]:
        tables = pg.get('tables', [])
        if len(tables) >= 3:
            hdr0 = tables[0].get('data', [[None]])[0]
            hdr2 = tables[2].get('data', [[None]])[0]
            if (len(hdr0) >= 2
                    and hdr0[0] == '단과대학' and hdr0[1] == '모집단위'
                    and hdr2 and hdr2[0] in ('최종등록자', '죄종등록자')):
                return True
    return False


def parse_three_table_page(tables: list, page_text: str,
                            adm_type: str | None) -> list[dict]:
    """Parse one page of 3-aligned-tables format.

    Table 1: dept info [단과대학, 모집단위, 계열, ..., 모집인원, 지원인원, ...]
    Table 3: scores [최종등록자 / 인원 / ... / avg / std / cut_50 / cut_70 / ...]
    Rows are aligned 1-to-1 after headers are skipped.
    """
    if len(tables) < 3:
        return []

    t1 = tables[0]['data']
    t3 = tables[2]['data']
    t3_cols = tables[2]['cols']

    # Validate table 1 header
    hdr = t1[0] if t1 else []
    if not (len(hdr) >= 2 and hdr[0] == '단과대학' and hdr[1] == '모집단위'):
        return []

    # Validate table 3 and determine header rows to skip
    t3_hdr0 = t3[0][0] if t3 else None
    if t3_hdr0 not in ('최종등록자', '죄종등록자'):
        return []

    if t3_cols == 6:
        score_start = 2    # 학생부교과: [인원, avg, std, cut_50, cut_70, 총점]
        score_type = '등급'
    elif t3_cols == 7:
        score_start = 3    # 학생부종합: extra sub-header row
        score_type = '등급'
    elif t3_cols >= 14:
        score_start = 4    # 정시 수능: [인원, avg, std, cut_50, cut_70, subjects...]
        score_type = '환산점수'
    else:
        return []

    dept_rows = t1[1:]            # skip 1 table-1 header
    score_rows = t3[score_start:]  # skip N table-3 headers

    if not dept_rows or not score_rows:
        return []
    # Skip if row counts mismatch (alignment broken by merged cells etc.)
    if len(dept_rows) != len(score_rows):
        return []

    # Get process name from page text: "[학생부교과(학생부교과전형)]" style
    m = re.search(r'\[([^\]]*전형[^\]]*)\]', page_text)
    process_name = m.group(1) if m else None
    if not process_name:
        m2 = re.search(r'([가-힣\w()（）]+전형)', page_text[:300])
        process_name = m2.group(1) if m2 else None
    if not process_name:
        return []

    # Find quota and applicants columns by header name
    quota_col = next((i for i, h in enumerate(hdr) if h == '모집인원'), 3)
    appl_col = next((i for i, h in enumerate(hdr) if h == '지원인원'), 4)

    records = []
    last_college = None

    for dept_row, score_row in zip(dept_rows, score_rows):
        col0 = dept_row[0] if dept_row else None
        col1 = dept_row[1] if len(dept_row) > 1 else None
        dept_name = col1

        if col0:
            if '\n' in col0:
                # Merged cell: "대학명 학과명\n다음학과\n..." — extract college + first dept
                first_line = col0.split('\n')[0]
                parts = first_line.split(' ', 1)
                last_college = parts[0].strip()
                if not dept_name and len(parts) > 1:
                    dept_name = parts[1].strip()
            else:
                last_college = col0.strip()

        if not dept_name:
            continue

        quota = parse_float(dept_row[quota_col] if len(dept_row) > quota_col else None)
        applicants = parse_float(dept_row[appl_col] if len(dept_row) > appl_col else None)
        rate = round(applicants / quota, 2) if quota and applicants else None

        # Score data: col[1]=avg, col[3]=cut_50, col[4]=cut_70 for all subtypes
        avg = parse_float(score_row[1] if len(score_row) > 1 else None)
        cut_50 = parse_float(score_row[3] if len(score_row) > 3 else None)
        cut_70 = parse_float(score_row[4] if len(score_row) > 4 else None)

        if avg is None and cut_50 is None and cut_70 is None:
            continue

        records.append({
            'dept': dept_name,
            'process': process_name,
            'adm_type': adm_type,
            'rate': rate,
            'quota': quota,
            'average': avg,
            'cut_50': cut_50,
            'cut_70': cut_70,
            'score_type': score_type,
        })

    return records


def parse_three_table_doc(doc: dict, year: int | None,
                           adm_type: str | None) -> list[dict]:
    """Parse all pages of a 3-aligned-tables document."""
    records = []
    for page in doc.get('pages', []):
        tables = page.get('tables', [])
        page_text = page.get('text', '')
        page_records = parse_three_table_page(tables, page_text, adm_type)
        records.extend(page_records)
    return records


# ── Special Format: 한양대학교(ERICA) Multi-Year Packed ────────────────────────

def is_erica_format(doc: dict) -> bool:
    """Detect 한양대학교(ERICA) multi-year packed table format.

    Tables have year labels (2022/2023/2024 or 2023/2024/2025) in row 1,
    with older years merged into packed cells and most-recent year per-row.
    """
    pages = doc.get('pages', [])
    for pg in pages[:5]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if len(data) < 2:
                continue
            row0, row1 = data[0], data[1]
            # Row 0 starts with '대학', row 1 has year labels
            if not (row0 and str(row0[0]).strip() == '대학'):
                continue
            year_vals = {str(v) for v in row1 if v and re.match(r'20\d\d', str(v))}
            if len(year_vals) >= 2:
                return True
    return False


def parse_erica_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 한양대학교(ERICA) multi-year packed table format.

    For each page/table, use the latest year column for scores (quota, rate,
    최종등록 평균등급, 최종등록 70%cut). Older-year columns are packed into
    merged cells in the first data row — skip them.
    """
    records = []

    for page in doc.get('pages', []):
        page_text = page.get('text', '')
        # Get process name from page text
        process_name = find_process_context_from_text(page_text)
        if not process_name:
            m = re.search(r'([가-힣\w()（）]+전형)', page_text[:500])
            process_name = m.group(1) if m else None

        for table in page.get('tables', []):
            data = table.get('data', [])
            if len(data) < 3:
                continue
            row0, row1 = data[0], data[1]

            # Confirm format
            if not (row0 and str(row0[0]).strip() == '대학'):
                continue
            year_vals = sorted({str(v) for v in row1 if v and re.match(r'20\d\d', str(v))})
            if len(year_vals) < 2:
                continue

            target_year_str = year_vals[-1]  # Most recent year (e.g., '2024')

            # Find all column indices for the target year
            year_cols = [j for j, v in enumerate(row1) if str(v) == target_year_str]

            # Require ≥6 year columns = at least 6 metrics (quota,rate,[실질],충원,avg,cut)
            # Tables with only 4 columns (no score columns) are skipped
            if len(year_cols) < 6:
                continue

            quota_col = year_cols[0]
            rate_col = year_cols[1]
            avg_col = year_cols[-2]   # 최종등록 평균 (second-to-last metric)
            cut_col = year_cols[-1]   # 최종등록 70%cut (last metric)

            # Determine score_type from header
            hdr_text = ' '.join(str(c) for c in row0 if c).replace('\x01', ' ')
            if '백분위' in hdr_text:
                score_type = '백분위'
            elif '등급' in hdr_text:
                score_type = '등급'
            else:
                score_type = '등급'

            # Which process? From page text or fallback
            proc = process_name or '(전형미상)'

            # Determine adm_type from 전형명 (overrides file-level adm_type)
            if proc and '학생부' in proc:
                table_adm_type = '수시'
            elif proc and '수능' in proc:
                table_adm_type = '정시'
            else:
                table_adm_type = adm_type

            last_college = None
            for row in data[2:]:
                # College/dept name
                col0 = row[0] if row else None
                col1 = row[1] if len(row) > 1 else None
                col2 = row[2] if len(row) > 2 else None

                if col0:
                    last_college = col0.split('\n')[0].strip()

                # Dept name: prefer col2 (전공) if not None, else col1 (학부)
                dept_name = None
                if col2 and str(col2).strip():
                    dept_name = str(col2).strip().split('\n')[0]
                elif col1 and str(col1).strip():
                    dept_name = str(col1).strip().split('\n')[0]

                if not dept_name:
                    continue

                # Get target-year values
                quota = parse_float(row[quota_col] if len(row) > quota_col else None)
                rate = parse_float(row[rate_col] if len(row) > rate_col else None)
                avg = parse_float(row[avg_col] if len(row) > avg_col else None)
                cut_70 = parse_float(row[cut_col] if len(row) > cut_col else None)

                # If quota is None or contains '\n', it's a packed (old-year) merged cell
                if quota is None and avg is None:
                    continue

                records.append({
                    'dept': dept_name,
                    'process': proc,
                    'adm_type': table_adm_type,
                    'rate': rate,
                    'quota': quota,
                    'average': avg,
                    'cut_50': None,
                    'cut_70': cut_70,
                    'score_type': score_type,
                })

    return records


# ── Special Format: 안양대학교 캠퍼스-Column Table ─────────────────────────────

def is_anyang_format(doc: dict) -> bool:
    """Detect 안양대학교 table format: header row 0 col[0] packs 'campus...dept...rate...'."""
    pages = doc.get('pages', [])
    for pg in pages[:8]:
        text = pg.get('text', '')
        if '◉' not in text:
            continue
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            hdr = str(data[0][0] or '') if data[0] else ''
            if '캠퍼스' in hdr and '모집단위' in hdr and '경쟁률' in hdr:
                return True
    return False


def parse_anyang_tables(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 안양대학교 캠퍼스-column table format.

    수시: 10-col [캠퍼스, 모집단위, quota, rate, avg, min, 국영수사_avg, 국영수사_min, ...]
    정시:  8-col [캠퍼스, 모집단위, quota, rate, avg, min, ...]
    Process name from ◉ pattern in page text.
    """
    records = []

    for page in doc.get('pages', []):
        page_text = page.get('text', '')
        # Extract ◉-style process names
        processes = re.findall(r'◉\s+([^\n◉]+)', page_text)
        if not processes:
            continue

        for table in page.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            hdr = str(data[0][0] or '') if data[0] else ''
            if '캠퍼스' not in hdr or '모집단위' not in hdr:
                continue

            n_cols = table['cols']
            if n_cols not in (8, 10):
                continue

            # Use first process name with 전형 keyword if available
            proc = None
            for p in processes:
                if '전형' in p or '수시' in p or '정시' in p or '학생부' in p:
                    proc = p.strip()
                    break
            if not proc:
                proc = processes[0].strip()

            # Score type and column positions
            if n_cols == 10:
                # 수시: col[4] = 입시결과 평균 (등급)
                score_type = '등급'
                avg_col = 4
                quota_col = 2
                rate_col = 3
            else:
                # 정시: col[4] = 평균 (환산점수, ~79-80 range)
                score_type = '환산점수'
                avg_col = 4
                quota_col = 2
                rate_col = 3

            for row in data[1:]:
                if not row or len(row) <= avg_col:
                    continue
                dept = row[1] if len(row) > 1 else None
                if not dept or str(dept).strip() in ('-', ''):
                    continue
                dept = str(dept).strip()

                quota = parse_float(row[quota_col] if len(row) > quota_col else None)
                # Rate: clean "9.30 : 1" format
                rate_str = str(row[rate_col] or '') if len(row) > rate_col else ''
                rate_m = re.search(r'(\d+\.?\d*)\s*:', rate_str)
                rate = float(rate_m.group(1)) if rate_m else parse_float(rate_str)

                avg = parse_float(row[avg_col] if len(row) > avg_col else None)
                if avg is None or str(avg) in ('-', ''):
                    continue

                records.append({
                    'dept': dept,
                    'process': proc,
                    'adm_type': adm_type,
                    'rate': rate,
                    'quota': quota,
                    'average': avg,
                    'cut_50': None,
                    'cut_70': None,
                    'score_type': score_type,
                })

    return records


# ── Special Format: 청운대학교 Dual-전형 Wide Table ────────────────────────────

def is_chungun_format(doc: dict) -> bool:
    """Detect 청운대학교 dual-전형 wide table format (15 or 18 cols, row 3 has '70%컷')."""
    pages = doc.get('pages', [])
    for pg in pages[:3]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            cols = table.get('cols', 0)
            if cols not in (15, 18):
                continue
            if len(data) < 4:
                continue
            # Row 1 has '일반전형' somewhere (col[0] packed or individual cells)
            r1 = ' '.join(str(v or '') for v in data[1])
            if '일반전형' not in r1 and '일반, 청운인재' not in r1:
                continue
            # Row 3 has '70%컷'
            r3 = ' '.join(str(v or '') for v in data[3])
            if '70%컷' in r3:
                return True
    return False


def parse_chungun_tables(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 청운대학교 dual-전형 wide table format.

    수시 (18 cols): 일반전형 최종합격 at col[9]/[10], 청운인재전형 at col[16]/[17]
    정시 (15 cols): 일반전형 최종합격 at col[9]/[10], score_type='환산점수'
    """
    records = []

    for page in doc.get('pages', []):
        for table in page.get('tables', []):
            data = table.get('data', [])
            cols = table.get('cols', 0)
            if cols not in (15, 18):
                continue
            if len(data) < 5:
                continue
            r1 = ' '.join(str(v or '') for v in data[1])
            r3 = ' '.join(str(v or '') for v in data[3])
            if ('일반전형' not in r1 and '일반, 청운인재' not in r1) or '70%컷' not in r3:
                continue

            if cols == 18:
                # 수시: 일반전형 최종합격 at col[9],[10]; 청운인재 at col[16],[17]
                processes = [
                    ('일반전형', 4, 5, 6, 9, 10, '등급'),
                    ('청운인재전형', 11, 12, 13, 16, 17, '등급'),
                ]
            else:
                # 정시 (15 cols): 일반/청운인재 수능 백분위 합산
                processes = [
                    ('일반전형', 4, 5, 6, 9, 10, '환산점수'),
                ]

            for (proc_name, quota_c, appl_c, rate_c, avg_c, cut_c, st) in processes:
                for row in data[4:]:  # Data starts at row 4
                    dept = row[2] if len(row) > 2 else None
                    if not dept or str(dept).strip() in ('', '-'):
                        continue
                    dept = str(dept).strip()

                    quota = parse_float(row[quota_c] if len(row) > quota_c else None)
                    rate = parse_float(row[rate_c] if len(row) > rate_c else None)
                    avg = parse_float(row[avg_c] if len(row) > avg_c else None)
                    cut_70 = parse_float(row[cut_c] if len(row) > cut_c else None)

                    if avg is None and cut_70 is None:
                        continue

                    records.append({
                        'dept': dept,
                        'process': proc_name,
                        'adm_type': adm_type,
                        'rate': rate,
                        'quota': quota,
                        'average': avg,
                        'cut_50': None,
                        'cut_70': cut_70,
                        'score_type': st,
                    })

    return records


# ── 수원대학교 format ──────────────────────────────────────────────────────────

def is_suwon_format(doc: dict) -> bool:
    """수원대학교: '대학' | '학부/학과' header."""
    for pg in doc.get('pages', [])[:5]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            r0 = data[0]
            if (r0 and len(r0) >= 2
                    and str(r0[0] or '').strip() == '대학'
                    and str(r0[1] or '').strip() == '학부/학과'):
                return True
    return False


def parse_suwon_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 수원대학교: average only, dynamic column detection."""
    records = []
    for pg in doc.get('pages', []):
        page_text = pg.get('text', '')
        m = re.search(r'■\s+(.+?)(?:\n|$)', page_text)
        process_name = m.group(1).strip() if m else None
        if '수시' in page_text[:200]:
            page_adm = '수시'
        elif '정시' in page_text[:200]:
            page_adm = '정시'
        else:
            page_adm = adm_type

        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            r0 = data[0]
            if not (r0 and len(r0) >= 2
                    and str(r0[0] or '').strip() == '대학'
                    and str(r0[1] or '').strip() == '학부/학과'):
                continue

            ncols = table.get('cols', 0)
            # Find data start: first row where ≥2 of cols 2-6 are numeric
            data_start = len(data)
            for i in range(1, len(data)):
                row = data[i]
                num = sum(1 for v in row[2:7]
                          if v and re.match(r'^\d+\.?\d*$', str(v).strip().replace(',', '')))
                if num >= 2:
                    data_start = i
                    break
            if data_start >= len(data):
                continue

            # Build col labels from header rows
            col_labels = [''] * ncols
            for row in data[:data_start]:
                for j, v in enumerate(row):
                    if j < ncols and v:
                        col_labels[j] += str(v).replace('\n', '').strip()

            quota_col = next((j for j, l in enumerate(col_labels) if '모집인원' in l), None)
            rate_col = next((j for j, l in enumerate(col_labels) if '경쟁률' in l), None)
            # Grade col: last col with '등급' but not '영어'
            grade_col = next((j for j in range(ncols - 1, -1, -1)
                              if '등급' in col_labels[j] and '영어' not in col_labels[j]), None)
            # Score col: last col with '성적' + '평균' (학생부성적, 환산점수)
            score_col = next((j for j in range(ncols - 1, -1, -1)
                              if '성적' in col_labels[j] and '평균' in col_labels[j]), None)

            if quota_col is None or rate_col is None:
                continue
            if grade_col is None and score_col is None:
                continue

            college = None
            for row in data[data_start:]:
                if not row:
                    continue
                v0 = str(row[0] or '').replace('\n', ' ').strip()
                if v0:
                    college = v0
                dept = str(row[1] or '').replace('\n', ' ').strip()
                if not dept:
                    continue

                quota = parse_float(str(row[quota_col] or '')) if quota_col < len(row) else None
                rate_raw = str(row[rate_col] or '') if rate_col < len(row) else ''
                rate_m2 = re.match(r'(\d+\.?\d*)\s*(?::|：)', rate_raw)
                rate = parse_float(rate_m2.group(1)) if rate_m2 else parse_float(rate_raw)

                grade_val = (parse_float(str(row[grade_col] or ''))
                             if grade_col is not None and grade_col < len(row) else None)
                score_val = (parse_float(str(row[score_col] or ''))
                             if score_col is not None and score_col < len(row) else None)

                if grade_val is not None and 1.0 <= grade_val <= 9.0:
                    avg, score_type = grade_val, '등급'
                elif score_val is not None:
                    avg = score_val
                    score_type = '환산점수' if score_val > 9 else '등급'
                else:
                    continue

                records.append({
                    'dept': dept,
                    'process': process_name,
                    'adm_type': page_adm,
                    'rate': rate,
                    'quota': quota,
                    'average': avg,
                    'cut_50': None,
                    'cut_70': None,
                    'score_type': score_type,
                })
    return records


# ── 한국공학대학교 format ──────────────────────────────────────────────────────

def is_hknu_format(doc: dict) -> bool:
    """한국공학대학교: '구분' header row with '지원현황' + '최종등록자'."""
    for pg in doc.get('pages', [])[:5]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            r0 = data[0]
            if not r0 or str(r0[0] or '').strip() != '구분':
                continue
            r0_str = ' '.join(str(v or '') for v in r0)
            if '지원현황' in r0_str and '최종등록자' in r0_str:
                return True
    return False


def parse_hknu_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 한국공학대학교: dynamic column detection, grade average."""
    records = []
    for pg in doc.get('pages', []):
        page_text = pg.get('text', '')
        m = re.search(r'(?:수시|정시)모집\s+([^\n]+)', page_text)
        process_name = m.group(1).strip() if m else None
        if not process_name:
            continue

        if '수시모집' in page_text[:200]:
            page_adm = '수시'
        elif '정시모집' in page_text[:200]:
            page_adm = '정시'
        else:
            page_adm = adm_type

        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data or str(data[0][0] or '').strip() != '구분':
                continue
            r0_str = ' '.join(str(v or '') for v in data[0])
            if '지원현황' not in r0_str or '최종등록자' not in r0_str:
                continue

            ncols = table.get('cols', 0)
            # Find data start: first row > 0 with ≥3 numeric values in cols 2+
            data_start = len(data)
            for i in range(1, len(data)):
                row = data[i]
                num = sum(1 for v in row[2:]
                          if v and re.match(r'^\d+\.?\d*$', str(v).strip().replace(',', '')))
                if num >= 3:
                    data_start = i
                    break
            if data_start >= len(data):
                continue

            # Build col labels from header rows
            col_labels = [''] * ncols
            for row in data[:data_start]:
                for j, v in enumerate(row):
                    if j < ncols and v:
                        col_labels[j] += str(v).replace('\n', '').strip()

            quota_col = next((j for j, l in enumerate(col_labels) if '모집' in l and '인원' in l), None)
            rate_col = next((j for j, l in enumerate(col_labels) if '경쟁률' in l), None)
            # Grade col: first col with '등급' but not '영어'
            grade_col = next((j for j, l in enumerate(col_labels)
                              if '등급' in l and '영어' not in l), None)
            # Score col for 정시: col with '환산점수'
            score_col = next((j for j, l in enumerate(col_labels) if '환산점수' in l), None)

            if quota_col is None or rate_col is None:
                continue

            if page_adm == '수시':
                val_col, score_type = grade_col, '등급'
            else:
                val_col, score_type = score_col, '환산점수'
            if val_col is None:
                continue

            college = None
            for row in data[data_start:]:
                if len(row) <= val_col:
                    continue
                v0 = str(row[0] or '').replace('\n', ' ').strip()
                if v0:
                    college = v0
                dept = str(row[1] or '').replace('\n', ' ').strip()
                if not dept:
                    continue
                # Handle merged dept cells (take first)
                if '\n' in dept:
                    dept = dept.split('\n')[0].strip()
                if not dept:
                    continue

                quota = parse_float(str(row[quota_col] or '')) if quota_col < len(row) else None
                rate = parse_float(str(row[rate_col] or '')) if rate_col < len(row) else None
                avg = parse_float(str(row[val_col] or ''))
                if avg is None:
                    continue
                if score_type == '등급' and not (1.0 <= avg <= 9.0):
                    continue

                records.append({
                    'dept': dept,
                    'process': process_name,
                    'adm_type': page_adm,
                    'rate': rate,
                    'quota': quota,
                    'average': avg,
                    'cut_50': None,
                    'cut_70': None,
                    'score_type': score_type,
                })
    return records


# ── 한성대학교 정시 format ────────────────────────────────────────────────────

def is_hansung_format(doc: dict) -> bool:
    """한성대학교 정시: 'No|모집군|모집전형|모집단위|수능등급|총점|경쟁률' header."""
    for pg in doc.get('pages', [])[:3]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            r0 = [str(v or '').strip() for v in data[0]]
            if (len(r0) >= 5 and r0[0] == 'No'
                    and r0[3] == '모집단위' and r0[4] == '수능등급'):
                return True
    return False


def parse_hansung_tables(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 한성대학교 정시: col[2]=전형, col[3]=dept, col[4]=grade, col[6]=rate."""
    records = []
    for pg in doc.get('pages', []):
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            r0 = [str(v or '').strip() for v in data[0]]
            if not (len(r0) >= 5 and r0[0] == 'No' and r0[3] == '모집단위'):
                continue
            for row in data[1:]:
                if len(row) < 5:
                    continue
                dept = str(row[3] or '').replace('\n', ' ').strip().rstrip('_').strip()
                if not dept:
                    continue
                process_name = str(row[2] or '').strip()
                avg = parse_float(str(row[4] or ''))
                if avg is None or not (1.0 <= avg <= 9.0):
                    continue
                rate_raw = str(row[6] or '') if len(row) > 6 else ''
                rate_m = re.match(r'(\d+\.?\d*)\s*(?::|：)', rate_raw)
                rate = parse_float(rate_m.group(1)) if rate_m else parse_float(rate_raw)
                records.append({
                    'dept': dept,
                    'process': process_name,
                    'adm_type': '정시',
                    'rate': rate,
                    'quota': None,
                    'average': avg,
                    'cut_50': None,
                    'cut_70': None,
                    'score_type': '등급',
                })
    return records


# ── 예수대학교 format ─────────────────────────────────────────────────────────

def is_yesu_format(doc: dict) -> bool:
    """예수대학교: '학부|전형명|...|학생부 커트라인' header with '50%','70%' row patterns."""
    for pg in doc.get('pages', [])[:3]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if len(data) < 3:
                continue
            r0_str = ' '.join(str(v or '') for v in data[0])
            if '학부' in r0_str and '커트라인' in r0_str:
                # Check for 50%/70% pattern in data rows
                body = ' '.join(str(v or '') for row in data[1:5] for v in row)
                if '50%' in body and '70%' in body:
                    return True
    return False


def parse_yesu_tables(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 예수대학교: grouped 3-row blocks (50%, 70%, 경쟁률) per 전형.

    Table structure: multi-year columns; current-year data in right half.
    Rows cycle: '50%' row → '70%' row → '경쟁률' row, repeat per 전형.
    Marker is found dynamically (not at fixed col).
    """
    records = []
    for pg in doc.get('pages', []):
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            r0_str = ' '.join(str(v or '') for v in data[0])
            if '학부' not in r0_str or '커트라인' not in r0_str:
                continue

            # Find first row with '50%' or '70%' marker — that's data_start
            data_start = 0
            for i, row in enumerate(data):
                if i == 0:
                    continue
                if any(str(v or '').strip() in ('50%', '70%', '경쟁률') for v in row):
                    data_start = i
                    break
            if data_start == 0:
                continue

            # Find the LAST marker col in first data row (rightmost = current year)
            first_row = data[data_start]
            marker_col = max((j for j, v in enumerate(first_row)
                              if str(v or '').strip() == '50%'), default=None)
            if marker_col is None:
                continue

            dept = None
            current_process = None
            cut_50 = None
            cut_70 = None

            for row in data[data_start:]:
                # Find LAST (rightmost) marker in this row — current year's section
                marker = None
                actual_marker_col = marker_col
                for j, v in enumerate(row):
                    if str(v or '').strip() in ('50%', '70%', '경쟁률'):
                        marker = str(v).strip()
                        actual_marker_col = j  # keep updating → gets rightmost
                if marker is None:
                    continue

                # Forward-fill dept from col[0]
                if row[0] and str(row[0]).strip():
                    dept = str(row[0]).replace('\n', '').strip()

                val_col = actual_marker_col + 1
                raw_val = str(row[val_col] or '').strip() if val_col < len(row) else ''
                # Scores: "4.08\n(981.13)" → grade is first number
                grade_m = re.match(r'(\d+\.\d+)', raw_val)
                grade_val = parse_float(grade_m.group(1)) if grade_m else None
                # Rate: "3.61:1"
                rate_m2 = re.match(r'(\d+\.?\d*)\s*:', raw_val)
                rate_val = parse_float(rate_m2.group(1)) if rate_m2 else None

                if marker == '50%':
                    # Start of new 전형 block
                    cut_50 = grade_val
                    cut_70 = None
                    # Get process name from col left of marker
                    pc = actual_marker_col - 1
                    if pc >= 0 and pc < len(row) and row[pc]:
                        proc_raw = str(row[pc]).replace('\n', ' ').strip()
                        current_process = re.sub(r'\s*\(\d+명\)', '', proc_raw).strip()
                    else:
                        current_process = None
                elif marker == '70%':
                    cut_70 = grade_val
                elif marker == '경쟁률':
                    rate = rate_val
                    if dept and current_process and (cut_50 is not None or cut_70 is not None):
                        records.append({
                            'dept': dept,
                            'process': current_process,
                            'adm_type': adm_type or '수시',
                            'rate': rate,
                            'quota': None,
                            'average': None,
                            'cut_50': cut_50,
                            'cut_70': cut_70,
                            'score_type': '등급',
                        })
                    cut_50 = cut_70 = None
                    current_process = None
    return records


# ── 서경대학교 format ──────────────────────────────────────────────────────────

def is_seokyeong_format(doc: dict) -> bool:
    """서경대학교: header area has both '70% Cut' and '모집단위' in first 4 rows."""
    for pg in doc.get('pages', [])[:2]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            combined = ' '.join(str(v or '') for row in data[:5] for v in row)
            if '70% Cut' in combined and '모집단위' in combined and '전형' in combined:
                return True
    return False


def parse_seokyeong_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 서경대학교: packed dept list in col[1] of process header row;
    col[5]=70%점수(환산), col[6]=70%등급. One score row per dept."""
    _SKIP_HEADERS = {'전형', '모집단위', '50% Cut', '70% Cut', '점수', '등급',
                     '최종', '예비', '번호'}
    records = []

    for pg in doc.get('pages', []):
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue

            current_process = None
            dept_list: list[str] = []
            group_idx = 0

            for row in data:
                if len(row) < 7:
                    continue

                col0 = str(row[0] or '').replace('\n', ' ').strip()
                col1 = str(row[1] or '').strip()

                # Skip header rows
                if col0 in _SKIP_HEADERS or (col0 and col0 in ('전형', '모집단위')):
                    current_process = None
                    continue

                # Detect process header row: col[0] non-empty + col[1] non-empty
                if col0 and col1:
                    # New process group
                    current_process = col0
                    dept_list = [d.strip() for d in col1.split('\n') if d.strip()]
                    group_idx = 0

                if not current_process or not dept_list:
                    continue

                if group_idx >= len(dept_list):
                    continue

                dept = dept_list[group_idx]
                group_idx += 1

                if not is_valid_dept_name(dept):
                    continue

                # col[6] = 70%컷 등급 (1-9), col[5] = 70%컷 score (0-1000)
                grade_70 = parse_float(str(row[6] or ''))
                score_70 = parse_float(str(row[5] or ''))
                rate = None
                # competition rate not directly available per-dept in this format

                if grade_70 is not None and 1.0 <= grade_70 <= 9.0:
                    records.append({
                        'dept': dept,
                        'process': current_process,
                        'adm_type': adm_type,
                        'rate': rate,
                        'quota': None,
                        'average': None,
                        'cut_50': None,
                        'cut_70': grade_70,
                        'score_type': '등급',
                    })
                elif score_70 is not None and score_70 > 9.0:
                    records.append({
                        'dept': dept,
                        'process': current_process,
                        'adm_type': adm_type,
                        'rate': rate,
                        'quota': None,
                        'average': None,
                        'cut_50': None,
                        'cut_70': score_70,
                        'score_type': '환산점수',
                    })

    return records


# ── 서원대학교 format ──────────────────────────────────────────────────────────

def is_seowon_format(doc: dict) -> bool:
    """서원대학교: header row[3] has '70%CUT' in col[13] and '모집학과' in col[3]."""
    for pg in doc.get('pages', [])[:2]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            for row in data[:5]:
                if (len(row) > 13
                        and '70%' in str(row[13] or '')
                        and '모집학과' in str(row[3] or '')
                        and '전형구분' in str(row[2] or '')):
                    return True
    return False


def parse_seowon_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 서원대학교: 4-row header; col[2]=전형구분, col[3]=dept, col[6]=rate,
    col[12]=평균, col[13]=70%CUT."""
    records = []
    for pg in doc.get('pages', []):
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue

            # Find header row with '전형구분' / '70%CUT'
            data_start = 0
            for i, row in enumerate(data[:6]):
                if len(row) > 13 and '70%' in str(row[13] or '') and '모집학과' in str(row[3] or ''):
                    data_start = i + 1
                    break
            if data_start == 0:
                continue

            for row in data[data_start:]:
                if len(row) < 14:
                    continue

                process = str(row[2] or '').strip()
                if not process or process in ('전형구분', '시기'):
                    continue

                dept = str(row[3] or '').strip()
                if not dept or not is_valid_dept_name(dept):
                    continue

                cut_70_raw = parse_float(str(row[13] or ''))
                avg_raw = parse_float(str(row[12] or ''))
                rate_raw = parse_float(str(row[6] or ''))

                if cut_70_raw is None and avg_raw is None:
                    continue

                # Determine score type from value range
                ref = cut_70_raw if cut_70_raw is not None else avg_raw
                if ref is not None and ref > 9.0:
                    score_type = '백분위'
                else:
                    score_type = '등급'

                if score_type == '등급':
                    if cut_70_raw is not None and not (1.0 <= cut_70_raw <= 9.0):
                        cut_70_raw = None
                    if avg_raw is not None and not (1.0 <= avg_raw <= 9.0):
                        avg_raw = None
                else:
                    if cut_70_raw is not None and not (0 <= cut_70_raw <= 100):
                        cut_70_raw = None
                    if avg_raw is not None and not (0 <= avg_raw <= 100):
                        avg_raw = None

                if cut_70_raw is None and avg_raw is None:
                    continue

                records.append({
                    'dept': dept,
                    'process': process,
                    'adm_type': adm_type,
                    'rate': rate_raw,
                    'quota': None,
                    'average': avg_raw,
                    'cut_50': None,
                    'cut_70': cut_70_raw,
                    'score_type': score_type,
                })

    return records


# ── 아주대학교 format ──────────────────────────────────────────────────────────

def is_ajou_format(doc: dict) -> bool:
    """아주대학교: first table row[0][0]='구분' AND header area has '70%cut' in col[7]."""
    for pg in doc.get('pages', [])[:2]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue
            # 아주대 specific: row[0][0]='구분' (other universities use '모집단위' etc.)
            if str(data[0][0] or '').strip() != '구분':
                continue
            for row in data[:5]:
                if len(row) > 7 and str(row[7] or '').strip() == '70%cut':
                    return True
    return False


def parse_ajou_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 아주대학교: col[0]=전형(forward-fill), col[1]=dept, col[7]=70%cut."""
    records = []
    current_process = None

    for pg in doc.get('pages', []):
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if not data:
                continue

            # Find data_start: skip header rows (rows containing '70%cut' or '구분')
            data_start = 0
            for i, row in enumerate(data[:5]):
                if any(str(v or '').strip() in ('구분', '70%cut', '총계', '인원') for v in row):
                    data_start = i + 1

            for row in data[data_start:]:
                if len(row) < 8:
                    continue

                # col[0]: 전형명 (may have \n, may be empty → forward-fill)
                col0 = str(row[0] or '').replace('\n', ' ').strip()
                if col0:
                    current_process = col0
                if not current_process:
                    continue

                # col[1]: dept name
                dept = str(row[1] or '').replace('\n', ' ').strip()
                if not dept or not is_valid_dept_name(dept):
                    continue

                # col[7]: 70%컷 (학생부등급, 1.0-9.0)
                cut_70 = parse_float(str(row[7] or ''))
                if cut_70 is not None and not (1.0 <= cut_70 <= 9.0):
                    cut_70 = None

                # col[10]: average grade (if present)
                avg = None
                if len(row) > 10:
                    avg = parse_float(str(row[10] or ''))
                    if avg is not None and not (1.0 <= avg <= 9.0):
                        avg = None

                # col[4]: competition rate "X.X : 1"
                rate = None
                if len(row) > 4:
                    rate_m = re.search(r'(\d+\.?\d*)\s*:\s*1', str(row[4] or ''))
                    if rate_m:
                        rate = parse_float(rate_m.group(1))

                if cut_70 is None and avg is None:
                    continue

                records.append({
                    'dept': dept,
                    'process': current_process,
                    'adm_type': adm_type,
                    'rate': rate,
                    'quota': None,
                    'average': avg,
                    'cut_50': None,
                    'cut_70': cut_70,
                    'score_type': '등급',
                })

    return records


# ── 충남대학교 format ──────────────────────────────────────────────────────────

_CNU_GYEOL = {'인문계', '자연계', '예체능계', '기타계', '전체계', '사범계'}
_CNU_GUNS = {'가군', '나군', '다군'}


def is_cnu_format(doc: dict) -> bool:
    """충남대학교: 3-col table with '충원합격' header + 4-col score table on same page."""
    for pg in doc.get('pages', [])[:8]:
        tables = pg.get('tables', [])
        if len(tables) < 2:
            continue
        d0, d1 = tables[0].get('data', []), tables[1].get('data', [])
        if (d0 and len(d0[0]) >= 3 and str(d0[0][2] or '') == '충원합격'
                and d1 and any('합격자' in str(v or '') for v in d1[0])):
            return True
    return False


def parse_cnu_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 충남대학교: join Table1 (process+dept) with Table2 (scores) by row index."""
    records = []
    for pg in doc.get('pages', []):
        tables = pg.get('tables', [])
        if len(tables) < 2:
            continue
        t1_data, t2_data = tables[0].get('data', []), tables[1].get('data', [])
        # Validate: Table1 row0[2]='충원합격', Table2 row0 has '합격자'
        if not t1_data or str(t1_data[0][2] if len(t1_data[0]) > 2 else '') != '충원합격':
            continue
        if not t2_data or not any('합격자' in str(v or '') for v in t2_data[0]):
            continue

        # Determine score format from Table2 row1 sub-header
        t2_sub = ' '.join(str(v or '') for v in t2_data[1]) if len(t2_data) > 1 else ''
        # 수시: col[3] packed as 7 values (교과Avg,교과70%,교과Std,등급Avg,등급70%,등급Low,등급Std)
        # or 4 values (등급Avg,등급70%,등급Low,등급Std)
        # 정시: col[3] packed as 4 values (수능Avg,수능70%,수능Low,수능Std)
        has_gyogwa = '교과' in t2_sub
        is_jeongsi = '수능' in t2_sub or '백분위' in t2_sub

        # Score col index for Table2 (always last non-empty col)
        score_col = None
        for j in range(len(t2_data[0]) - 1, -1, -1):
            if t2_data[0][j] or (len(t2_data) > 1 and t2_data[1][j]):
                score_col = j
                break
        if score_col is None or score_col < 2:
            continue

        # Align: t1 starts at row 2, t2 starts at row 2
        t1_offset, t2_offset = 2, 2
        if len(t1_data) - t1_offset != len(t2_data) - t2_offset:
            # Try offset 1 for t2 if counts mismatch
            if len(t1_data) - t1_offset == len(t2_data) - 1:
                t2_offset = 1

        current_process = None

        for i, row1 in enumerate(t1_data[t1_offset:]):
            row2_idx = i + t2_offset
            if row2_idx >= len(t2_data):
                break
            row2 = t2_data[row2_idx]

            # Table1 col[0]: 전형명? 계열 단과대학 모집단위
            col0 = str(row1[0] or '').strip()
            if col0:
                parts = col0.split()
                first = parts[0] if parts else ''
                if first in _CNU_GYEOL:
                    # No 전형명 — keep current_process (or set to 학생부종합)
                    if current_process is None:
                        current_process = '학생부종합'
                elif first in _CNU_GUNS:
                    current_process = first  # 정시 군
                elif first in {'일반', '지역인재', '교과', '논술'}:
                    current_process = first
                elif re.match(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]', first):
                    # Roman numeral prefix: 'Ⅰ일반', 'ⅡSW' — use first token only
                    current_process = first
                elif '지역인재' in first or '인재' in first or '전형' in first or '기회' in first:
                    current_process = first
                else:
                    # Skip if col0 looks like a dept/college continuation row
                    _dept_ends = ('학과', '교육과', '학부', '학전공', '전공', '학군', '학과군')
                    _coll_ends = ('대학', '대학교', '대학원')
                    if ('전공' not in first
                            and not any(first.endswith(e) for e in _dept_ends)
                            and not any(first.endswith(e) for e in _coll_ends)):
                        current_process = first

            if not current_process:
                continue

            # Table2 col[0]: dept name
            dept = str(row2[0] or '').replace('\n', ' ').strip() if row2 else None
            if not dept:
                continue

            # Table2 score_col: packed scores
            score_raw = str(row2[score_col] or '').strip() if score_col < len(row2) else ''
            if not score_raw:
                continue
            vals = [v for v in score_raw.split() if re.match(r'-?\d+\.?\d*', v)]
            if len(vals) < 2:
                continue

            try:
                if has_gyogwa and len(vals) >= 7:
                    # 수시 교과 형: [교과Avg, 교과70%, 교과Std, 등급Avg, 등급70%, 등급Low, 등급Std]
                    avg = float(vals[3])
                    cut_70 = float(vals[4])
                    score_type = '등급'
                elif len(vals) >= 4:
                    # 4-value: [Avg, 70%, Low, Std] — 등급 for 수시, 수능 for 정시
                    avg = float(vals[0])
                    cut_70 = float(vals[1])
                    score_type = '백분위' if is_jeongsi else '등급'
                else:
                    continue
            except (ValueError, IndexError):
                continue

            if score_type == '등급' and not (1.0 <= avg <= 9.0):
                continue
            if score_type == '백분위' and not (0 <= avg <= 100):
                continue

            # Rate from Table1 col[1]: "모집인원 지원인원 경쟁률:1 ..."
            rate = None
            col1 = str(row1[1] or '').strip() if len(row1) > 1 else ''
            rate_m = re.search(r'(\d+\.?\d*):1', col1)
            if rate_m:
                rate = parse_float(rate_m.group(1))

            records.append({
                'dept': dept,
                'process': current_process,
                'adm_type': adm_type,
                'rate': rate,
                'quota': None,
                'average': avg,
                'cut_50': None,
                'cut_70': cut_70,
                'score_type': score_type,
            })
    return records


# ── Special Format: 선문대학교 Text-Columnar (50컷/70컷 교과등급) ──────────────

_SUNMOON_SKIP_NORMS = {norm(x) for x in {
    '구분', '입학전형', '모집단위', '2025학년도모집단위', '2024학년도모집단위',
    '2026학년도모집단위', '모집인원', '경쟁률', '컷순위', '50컷', '70컷',
    '50%CUT', '70%CUT', '50%cut', '70%cut', '비고', '추가합격자',
    '최종순위', '추가합격자최종순위', '학생부등급', '최종등록자교과성적',
    '교과', '종합', '실기/', '실적', '실기/실적',
    '나군', '가군', '다군', '통합모집',
    '모집', '최종등록자', '입학전형*2026학년도모집단위',
}}


def is_sunmoon_format(doc: dict) -> bool:
    """선문대학교: text-based with '선문대학교' + '50컷'/'50% CUT' in page text."""
    for pg in doc.get('pages', [])[:3]:
        text = pg.get('text', '')
        if '선문대학교' in text and ('50컷' in text or '50% CUT' in text):
            return True
    return False


def parse_sunmoon_text(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 선문대학교 text-based columnar 수시 format.

    Two sub-formats:
      2024: per dept → 5 values: quota, rate, waitlist_rank, cut50, cut70
      2025: per dept → 5 values (full) OR 2 values (통합모집 sub-entry: cut50, cut70)
    All scores are 교과 등급 (1-9 scale).
    Skips 정시 pages (환산점수 values > 100).
    """
    records = []

    for pg in doc.get('pages', []):
        text = pg.get('text', '')
        lines = [l.strip() for l in text.split('\n') if l.strip()]

        current_process = None
        i = 0

        while i < len(lines):
            line = lines[i]

            # Skip header/note/page markers
            if (len(line) < 2
                    or re.match(r'^-\s*\d+\s*-$', line)
                    or re.match(r'^20\d\d', line)
                    or line.startswith(('※', '(', '*', '（', '+', '-'))
                    or norm(line) in _SUNMOON_SKIP_NORMS
                    or '통합모집' in norm(line)
                    or '통합 모집' in line
                    or '학년도' in line
                    or '성적산출' in norm(line)
                    or '성적 기준' in line
                    or '산출 기준' in line):
                i += 1
                continue

            # Process name: ends with '전형'
            if line.endswith('전형') and len(line) >= 4:
                current_process = line
                i += 1
                continue

            # Pure numeric stray value
            if parse_float(line.replace(',', '')) is not None:
                i += 1
                continue

            # Korean text → try as dept name
            if re.search(r'[가-힣]', line) and len(line) <= 35:
                dept = line

                # Collect following numeric values (up to 6), skipping note lines
                j = i + 1
                nums: list[float] = []
                while j < len(lines) and len(nums) < 6:
                    nxt = lines[j]
                    # Skip note/header lines transparently
                    if (not nxt
                            or re.match(r'^20\d\d', nxt)
                            or nxt.startswith(('※', '(', '*', '（'))
                            or norm(nxt) in _SUNMOON_SKIP_NORMS
                            or '통합모집' in norm(nxt)
                            or '통합 모집' in nxt
                            or '학년도' in nxt):
                        j += 1
                        continue
                    v = parse_float(nxt.replace(',', ''))
                    if v is not None:
                        nums.append(v)
                        j += 1
                    else:
                        break  # non-numeric → next dept/process

                proc = current_process or '일반전형'

                if (len(nums) >= 5
                        and 1.0 <= nums[3] <= 9.0
                        and 1.0 <= nums[4] <= 9.0):
                    # Full record: quota=nums[0], rate=nums[1], waitlist=nums[2],
                    #              cut50=nums[3], cut70=nums[4]
                    if is_valid_dept_name(dept):
                        records.append({
                            'dept': dept, 'process': proc,
                            'adm_type': adm_type,
                            'rate': nums[1],
                            'quota': int(nums[0]) if nums[0] < 10000 else None,
                            'average': None,
                            'cut_50': nums[3], 'cut_70': nums[4],
                            'score_type': '등급',
                        })
                    i = j

                elif (len(nums) == 2
                        and 1.0 <= nums[0] <= 9.0
                        and 1.0 <= nums[1] <= 9.0):
                    # Sub-record: cut50=nums[0], cut70=nums[1]
                    if is_valid_dept_name(dept):
                        records.append({
                            'dept': dept, 'process': proc,
                            'adm_type': adm_type,
                            'rate': None, 'quota': None,
                            'average': None,
                            'cut_50': nums[0], 'cut_70': nums[1],
                            'score_type': '등급',
                        })
                    i = j

                else:
                    i += 1
            else:
                i += 1

    return records


# ── Special Format: 성공회대학교 Text-Columnar (학생부 평균등급) ────────────────

def is_sungkonghoe_format(doc: dict) -> bool:
    """성공회대학교 수시: text with '최종최저등급' and 학부/학과 dept names."""
    for pg in doc.get('pages', [])[:2]:
        text = pg.get('text', '')
        if '최종최저등급' in text and ('학부' in text or '학과' in text):
            return True
    return False


def parse_sungkonghoe_text(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 성공회대학교 수시 text.

    Per-dept block: dept_line → [process_line + 6 values]...
    Values: quota, 지원인원, 충원인원, 경쟁률, 학생부평균등급, 최종최저등급
    Skips rows with '-' for grade (not enough numeric values).
    """
    _SKIP_LINES = {
        '수시', '정시', '모집학과', '전형구분', '모집인원', '지원인원', '충원인원',
        '경쟁률', '학생부', '최종최저등급', '평균등급',
        '국어(백분위)', '수학(백분위)', '영어(등급)', '탐구(백분위)', '평균점수', '컷트라인',
        '-', '—', '',
    }

    records = []

    for pg in doc.get('pages', []):
        text = pg.get('text', '')
        # Only 수시 pages (have 최종최저등급 column)
        if '최종최저등급' not in text:
            continue
        # Detect page-level admission type from section header
        page_adm = adm_type
        for _ln in text.split('\n'):
            _s = _ln.strip()
            if _s in ('수시', '정시'):
                page_adm = _s
                break

        lines = [l.strip() for l in text.split('\n')]
        current_dept: str | None = None
        current_process: str | None = None
        pending: list[float] = []

        def flush_record():
            if (current_dept and current_process
                    and len(pending) >= 5
                    and 1.0 <= pending[4] <= 9.0
                    and is_valid_dept_name(current_dept)):
                records.append({
                    'dept': current_dept, 'process': current_process,
                    'adm_type': page_adm,
                    'rate': pending[3] if pending[3] < 100 else None,
                    'quota': int(pending[0]) if pending[0] < 1000 else None,
                    'average': pending[4],
                    'cut_50': None, 'cut_70': None,
                    'score_type': '등급',
                })

        for line in lines:
            if line in _SKIP_LINES:
                continue

            # Numeric value
            v = parse_float(line)
            if v is not None:
                pending.append(v)
                continue

            # Non-Korean non-numeric (e.g., '-', page markers) → skip
            if not re.search(r'[가-힣]', line):
                continue

            # Korean text: dept or process name
            if line.endswith(('학부', '학과')):
                # New dept → flush then reset
                flush_record()
                pending = []
                current_dept = line
                current_process = None
            else:
                # Process name → flush then update
                flush_record()
                pending = []
                current_process = line

        flush_record()  # final

    return records


# ── Special Format: 한국성서대학교 Dual-Column (학생부등급 + 수능백분위) ──────────

def is_sungseo_format(doc: dict) -> bool:
    """한국성서대학교 정시: single table with 모집군/일반학생 header AND 대학수학능력시험/백분위 sub-header."""
    for pg in doc.get('pages', [])[:2]:
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if len(data) < 5 or len(data[0]) < 7:
                continue
            combined = ' '.join(str(v or '') for row in data[:4] for v in row)
            if ('모집군' in combined and '일반학생' in combined
                    and '대학수학능력시험' in combined and '백분위' in combined):
                return True
    return False


def parse_sungseo_doc(doc: dict, year: int | None, adm_type: str | None) -> list[dict]:
    """Parse 한국성서대학교 정시 dual-column table.

    Layout (9 cols):
      col[0]=모집군  col[1]=모집단위  col[2]=경쟁률
      col[3]=학생부최고  col[4]=학생부평균  col[5]=학생부최저  (등급 1-9)
      col[6]=수능최고   col[7]=수능평균   col[8]=수능최저    (백분위 0-100)
    Process name embedded in header row[0][2] (e.g., '일반학생(정시)').
    """
    records = []
    for pg in doc.get('pages', []):
        for table in pg.get('tables', []):
            data = table.get('data', [])
            if len(data) < 5 or len(data[0]) < 7:
                continue
            combined = ' '.join(str(v or '') for row in data[:4] for v in row)
            if '대학수학능력시험' not in combined or '모집단위' not in combined:
                continue

            # Extract process name from first header row col[2]
            process_name = clean(str(data[0][2] or ''))
            if not process_name or not is_valid_process_name(process_name):
                process_name = '일반학생'

            # Find first data row: first row where col[1] looks like a dept
            data_start = 0
            for i, row in enumerate(data):
                if len(row) < 7:
                    continue
                dept_candidate = clean(str(row[1] or ''))
                if is_valid_dept_name(dept_candidate):
                    data_start = i
                    break

            for row in data[data_start:]:
                if len(row) < 7:
                    continue
                dept = clean(str(row[1] or '').replace('\n', ' '))
                if not is_valid_dept_name(dept):
                    continue

                # 학생부 등급: col[3]=최고, col[4]=평균, col[5]=최저
                grade_avg = parse_float(str(row[4] if len(row) > 4 else None or ''))
                if grade_avg is not None and 1.0 <= grade_avg <= 9.0:
                    records.append({
                        'dept': dept, 'process': process_name,
                        'adm_type': adm_type, 'rate': None, 'quota': None,
                        'average': grade_avg, 'cut_50': None, 'cut_70': None,
                        'score_type': '등급',
                    })

                # 수능 백분위: col[6]=최고, col[7]=평균, col[8]=최저
                pct_avg = parse_float(str(row[7] if len(row) > 7 else None or ''))
                if pct_avg is not None and 10.0 <= pct_avg <= 100.0:
                    records.append({
                        'dept': dept, 'process': process_name,
                        'adm_type': adm_type, 'rate': None, 'quota': None,
                        'average': pct_avg, 'cut_50': None, 'cut_70': None,
                        'score_type': '백분위',
                    })

    return records


# ── File Processor ────────────────────────────────────────────────────────────

def process_json_file(json_path: Path) -> tuple[list[dict], int | None, str | None]:
    """Process one extracted JSON file. Returns (records, result_year, admission_type)."""
    with open(json_path, encoding='utf-8') as f:
        doc = json.load(f)

    source = doc.get('source_file', json_path.name)
    year, adm_type = parse_year_type_from_filename(source)

    # Fallback: scan first 5 pages of content for year if not found in filename
    if year is None:
        pages = doc.get('pages', [])
        for page in pages[:5]:
            page_text = page.get('text', '')
            years_found = re.findall(r'(20\d\d)학년도', page_text)
            if years_found:
                year = int(years_found[0])
                break

    all_records = []

    if doc.get('format') == 'xlsx':
        for sheet in doc.get('sheets', []):
            records = process_xlsx_sheet(sheet, adm_type, year)
            all_records.extend(records)
    else:
        if is_jeju_format(doc):
            all_records = parse_jeju_text(doc, year, adm_type)
        elif is_three_table_format(doc):
            all_records = parse_three_table_doc(doc, year, adm_type)
        elif is_erica_format(doc):
            all_records = parse_erica_doc(doc, year, adm_type)
        elif is_anyang_format(doc):
            all_records = parse_anyang_tables(doc, year, adm_type)
        elif is_chungun_format(doc):
            all_records = parse_chungun_tables(doc, year, adm_type)
        elif is_suwon_format(doc):
            all_records = parse_suwon_doc(doc, year, adm_type)
        elif is_hknu_format(doc):
            all_records = parse_hknu_doc(doc, year, adm_type)
        elif is_hansung_format(doc):
            all_records = parse_hansung_tables(doc, year, adm_type)
        elif is_yesu_format(doc):
            all_records = parse_yesu_tables(doc, year, adm_type)
        elif is_seokyeong_format(doc):
            all_records = parse_seokyeong_doc(doc, year, adm_type)
        elif is_seowon_format(doc):
            all_records = parse_seowon_doc(doc, year, adm_type)
        elif is_ajou_format(doc):
            all_records = parse_ajou_doc(doc, year, adm_type)
        elif is_cnu_format(doc):
            all_records = parse_cnu_doc(doc, year, adm_type)
        elif is_sunmoon_format(doc):
            all_records = parse_sunmoon_text(doc, year, adm_type)
        elif is_sungkonghoe_format(doc):
            all_records = parse_sungkonghoe_text(doc, year, adm_type)
        elif is_sungseo_format(doc):
            all_records = parse_sungseo_doc(doc, year, adm_type)
        else:
            last_process_ctx: str | None = None  # Cross-page 전형명 carry (B2)
            for page in doc.get('pages', []):
                page_text = page.get('text', '')
                # Detect page-level 수시/정시 override from section headers.
                # Some PDFs (e.g. 건국대) bundle 수시 + 정시 sections in one file.
                page_adm = adm_type
                m = re.search(r'전형결과\s*[―\-–]\s*(수시|정시)', page_text[:500])
                if m:
                    page_adm = m.group(1)
                else:
                    m2 = re.search(r'^(수시|정시)모집', page_text[:200], re.MULTILINE)
                    if m2:
                        page_adm = m2.group(1)
                    else:
                        # "정시 │ 24" style page numbers (e.g. 부산대 combined document)
                        m3 = re.search(r'^(수시|정시)\s*[│|]\s*\d', page_text[:300], re.MULTILINE)
                        if m3:
                            page_adm = m3.group(1)
                # Track last known 전형명 for pages without a section header (e.g. 경북대 정시)
                page_ctx = find_process_context_from_text(page_text)
                if page_ctx:
                    last_process_ctx = page_ctx
                for table in page.get('tables', []):
                    records = process_table(table, page_text, page_adm, year,
                                            fallback_process_ctx=last_process_ctx)
                    all_records.extend(records)

    return all_records, year, adm_type


# ── DB Storage ────────────────────────────────────────────────────────────────

def store_records(store: AdmissionStore, university: str, records: list[dict],
                  result_year: int, adm_type: str | None) -> tuple[int, int]:
    """Store extracted records into DB. Returns (dept_count, result_count)."""
    dept_count = 0
    result_count = 0

    for rec in records:
        dept_name = rec['dept']
        process_name = rec['process']

        # Try exact match in year=2026 records (from 모집요강)
        # Use a direct SQL query for exact match
        with store._conn() as conn:
            row = conn.execute(
                """SELECT id FROM admission_department
                   WHERE year = 2026 AND university = ? AND name = ?""",
                (university, dept_name)
            ).fetchone()
        if row:
            dept_id = row[0]
        else:
            # Create a new department record linked to result_year
            dept_id = store.upsert_department(
                year=result_year,
                university=university,
                name=dept_name,
            )
            dept_count += 1

        result_adm_type = rec.get('adm_type') or adm_type
        score_type = rec.get('score_type')

        # Build supplementary attributes (counts, ranks)
        extra_attrs = {k: v for k, v in {
            '모집인원': rec.get('quota'),
            '지원인원': rec.get('applicants'),
            '등록인원': rec.get('enrolled'),
            '충원합격인원': rec.get('waitlist'),
            '충원합격순위': rec.get('admit_rank'),
        }.items() if v is not None}

        if score_type == '환산점수':
            # Don't mix 환산점수 with 등급 scores
            # Still store as attributes but skip primary score fields
            attrs = {k: v for k, v in {
                '환산_평균': rec.get('average'),
                '환산_70': rec.get('cut_70'),
                '환산_80': rec.get('cut_80'),
            }.items() if v is not None}
            attrs.update(extra_attrs)
            store.upsert_result(
                department_id=dept_id,
                result_year=result_year,
                process_name=process_name,
                admission_type=result_adm_type,
                score_type=score_type,
                competition_rate=rec.get('rate'),
                content=None,
                attributes=attrs or None,
            )
        else:
            store.upsert_result(
                department_id=dept_id,
                result_year=result_year,
                process_name=process_name,
                admission_type=result_adm_type,
                score_type=score_type,
                competition_rate=rec.get('rate'),
                average_score=rec.get('average'),
                cut_50=rec.get('cut_50'),
                cut_60=rec.get('cut_60'),
                cut_70=rec.get('cut_70'),
                cut_80=rec.get('cut_80'),
                cut_85=rec.get('cut_85'),
                cut_90=rec.get('cut_90'),
                attributes=extra_attrs or None,
            )
        result_count += 1

    return dept_count, result_count


# ── Entry Point ───────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Parse results JSON → AdmissionStore")
    parser.add_argument("--univ", help="Process only this university (substring match)")
    parser.add_argument("--file", help="Process one specific JSON file path")
    parser.add_argument("--dry-run", action="store_true", help="Parse but don't write to DB")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    store = AdmissionStore()

    total_depts = 0
    total_results = 0
    total_files = 0
    skipped = 0

    if args.file:
        paths = [Path(args.file)]
    else:
        paths = sorted(RESULTS_EXTRACTED_DIR.rglob("*.json"))
        if args.univ:
            paths = [p for p in paths if args.univ in str(p.parent.name)]

    # Group by university
    univ_files: dict[str, list[Path]] = {}
    for p in paths:
        univ = p.parent.name
        univ_files.setdefault(univ, []).append(p)

    for univ, files in sorted(univ_files.items()):
        # Skip 정시 files that are byte-identical to a 수시 file in the same folder
        # (e.g. 부산대 CDN serves the same combined PDF under both URLs)
        _sizes = {p: p.stat().st_size for p in files}
        _susi = {p for p in files if '수시' in p.name}
        _jeongsi = {p for p in files if '정시' in p.name}
        _dup_jeongsi = {jf for jf in _jeongsi for sf in _susi if _sizes[jf] == _sizes[sf]}
        if _dup_jeongsi:
            for dup in _dup_jeongsi:
                print(f"  SKIP {dup.name}: byte-identical to 수시 file")
            skipped += len(_dup_jeongsi)

        univ_results = 0
        for json_path in files:
            if json_path in _dup_jeongsi:
                continue
            try:
                records, result_year, adm_type = process_json_file(json_path)
                if not records or not result_year:
                    skipped += 1
                    if args.verbose:
                        print(f"  SKIP {json_path.name}: {len(records)} records, year={result_year}")
                    continue

                if args.dry_run:
                    if args.verbose:
                        print(f"  DRY {json_path.name}: {len(records)} records, year={result_year}, type={adm_type}")
                        for r in records[:3]:
                            print(f"    {r['dept']} | {r['process']} | {r['score_type']} | 70={r.get('cut_70')}")
                    total_results += len(records)
                    total_files += 1
                    continue

                d, r = store_records(store, univ, records, result_year, adm_type)
                total_depts += d
                total_results += r
                univ_results += r
                total_files += 1

                if args.verbose:
                    print(f"  {json_path.name}: {r} results ({adm_type} {result_year})")

            except Exception as e:
                print(f"  ERROR {json_path}: {e}")
                import traceback
                if args.verbose:
                    traceback.print_exc()

        if univ_results > 0:
            print(f"{univ}: {univ_results} results")

    print(f"\n{'DRY RUN - ' if args.dry_run else ''}Done:")
    print(f"  Files: {total_files} processed, {skipped} skipped")
    print(f"  New departments: {total_depts}")
    print(f"  Results stored: {total_results}")

    stats = store.stats()
    print(f"\nDB stats: {stats['universities']} unis, "
          f"{stats['departments']} depts, "
          f"{stats['processes']} processes, "
          f"{stats['results']} results")
    if stats.get('result_score_types'):
        print(f"  Score types: {stats['result_score_types']}")


if __name__ == "__main__":
    main()
