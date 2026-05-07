#!/usr/bin/env python3
"""
OCR-based 입시결과 extractor for image-based PDFs.
Handles universities whose results PDFs are scanned JPEG images.

Usage:
    python extract_ocr_results.py                    # process all 6 target universities
    python extract_ocr_results.py 서강대학교          # single university
    python extract_ocr_results.py --dry-run          # preview without DB write
"""

import os, re, sys, json, sqlite3, argparse, tempfile
from pathlib import Path

import fitz       # PyMuPDF
import easyocr

DB_PATH     = "data/admission.db"
RESULTS_DIR = "data/results"
SCALE       = 2.0          # render at 2x for better OCR
ROW_Y_GAP   = 15           # max Y gap (px, at 2x) to consider same row
DEPT_MAX_X  = 450          # x < this (at 2x) → department name cell
COL_TOL     = 100          # px tolerance when assigning values to columns
HEADER_ROWS = 18           # how many top rows to scan for column headers

TARGET_UNIS = [
    "서강대학교",
    "한양대학교",
    "경희대학교",
    "가톨릭대학교",
    "서울여자대학교",
    "성신여자대학교",
]

# ── OCR ─────────────────────────────────────────────────────────────────────

_reader = None

def get_reader():
    global _reader
    if _reader is None:
        print("Loading EasyOCR (ko,en)…", flush=True)
        _reader = easyocr.Reader(['ko', 'en'], gpu=False, verbose=False)
    return _reader


def render_page(page: fitz.Page, tmp_dir: str) -> str:
    mat  = fitz.Matrix(SCALE, SCALE)
    pix  = page.get_pixmap(matrix=mat)
    path = os.path.join(tmp_dir, f"page_{page.number}.png")
    pix.save(path)
    return path


# ── Row clustering ────────────────────────────────────────────────────────────

def y_center(r):  return (r[0][0][1] + r[0][2][1]) / 2
def x_left(r):   return r[0][0][0]
def x_center(r): return (r[0][0][0] + r[0][2][0]) / 2


def cluster_rows(regions):
    if not regions:
        return []
    sorted_r = sorted(regions, key=y_center)
    rows, cur = [], [sorted_r[0]]
    cy = y_center(sorted_r[0])
    for r in sorted_r[1:]:
        y = y_center(r)
        if abs(y - cy) <= ROW_Y_GAP:
            cur.append(r)
            cy = (cy * (len(cur) - 1) + y) / len(cur)
        else:
            rows.append(sorted(cur, key=x_left))
            cur, cy = [r], y
    rows.append(sorted(cur, key=x_left))
    return rows


# ── Column detection (regex-based) ───────────────────────────────────────────

def find_column_map(rows):
    """Scan header rows to locate X-centers of key columns via regex."""
    col = {}
    for row in rows[:HEADER_ROWS]:
        for region in row:
            raw  = region[1]
            text = raw.replace(' ', '')
            xc   = x_center(region)

            if not col.get('dept') and re.search(r'모집단위|모집단', text):
                col['dept'] = xc

            if not col.get('rate') and re.search(r'경쟁[률율속쟁]|경쟁[mMm]|경전[률율]', text):
                col['rate'] = xc

            # 50% cut — OCR often reads '%' as 'u','W','%' and '컷' as '것','컷'
            if not col.get('cut_50') and re.search(
                    r'50[%uWu％]?[컷것]|50[컷것]|50%cut|509[컷것]|50W컷|50u컷|50컷|50것', text, re.I):
                col['cut_50'] = xc
            # fall-through: "50" at start + cut keyword anywhere in same region
            if not col.get('cut_50') and re.match(r'^50', text) and re.search(r'[컷것]|cut', text, re.I):
                col['cut_50'] = xc

            # 70% cut
            if not col.get('cut_70') and re.search(
                    r'70[%46uWu％]?[컷것]|70[컷것]|70%cut|7046|7036|70W컷|70u컷|70컷|70것', text, re.I):
                col['cut_70'] = xc
            if not col.get('cut_70') and re.match(r'^70', text) and re.search(r'[컷것]|cut', text, re.I):
                col['cut_70'] = xc

    return col


# ── Process name detection ────────────────────────────────────────────────────

# keywords that should NOT appear in the section header (they'd make it a data header)
_HDR_SCORE_KWORDS = re.compile(r'컷|경쟁률|모집인|지원인|합격인|50%|70%|50컷|70컷')
_PROC_MAP = [
    # patterns for the combined text of a section header row
    # order: most-specific first
    # 교과 + 지역균형 (OCR variants: 교과→고개, 균형→균켓/균험)
    (r'교과.{0,10}지역균형|지역균형.{0,10}교과|교과.{0,10}균[형켓험]|지.{0,8}균[형켓험]|지먹균',
     '학생부교과(지역균형)'),
    # 교과 + 기회균형
    (r'교과.{0,10}기회균형|기회균형.{0,10}교과|교과.{0,10}기회|기외근경',
     '학생부교과(기회균형)'),
    # 교과 + 일반
    (r'교과.{0,10}[일인]반|[일인]반.{0,10}교과',
     '학생부교과(일반)'),
    # 교과 (any)
    (r'학[성생]부교과|고개교과|교과전형|학성부교|학생부교',
     '학생부교과'),
    # 종합 + 일반  (종합 OCR'd as 중대, 중합, 종렉, 종임, 중렉, 하성부중, 학생부중 etc.)
    (r'종[합렉임]?.{0,10}[일인]반|[일인]반.{0,10}종[합렉임]?'
     r'|중[대합렉임해].{0,8}[일인]반|[일인]반.{0,8}중[대합렉임해]'
     r'|하성부중.{0,8}[일인]반|학성부중.{0,8}[일인]반'
     r'|학[성생]부중.{0,10}[일인]반',
     '학생부종합(일반)'),
    # 종합 + 기회균형
    (r'종[합렉임]?.{0,10}기회|기회.{0,10}종[합렉임]?|중대.{0,8}기회|기회.{0,8}중대|기외근경.*중|중.*기외근경',
     '학생부종합(기회균형)'),
    # 종합 + 지역균형
    (r'종[합렉임]?.{0,10}균[형켓험]|균[형켓험].{0,10}종[합렉임]?',
     '학생부종합(지역균형)'),
    # 종합 (any): "학생부종합", "하성부중", "학성부종", "학.+부중대" etc.
    (r'학[성생]부종[합렉임]?|학[성생]부중[대합렉임]|종합전형|학성부종|학생부종|하성부중|학.{0,3}부중',
     '학생부종합'),
    (r'논술',
     '논술전형'),
    (r'실기|특기자',
     '실기/특기자전형'),
]


def _match_proc(text: str) -> str | None:
    c = text.replace(' ', '')
    for pat, name in _PROC_MAP:
        if re.search(pat, c):
            return name
    return None


def detect_section_header(row) -> str | None:
    """
    If `row` looks like a 전형 section header, return the process name.
    Returns None if it's a data row or column-header row.
    """
    # Section headers typically have few regions and sit at the left margin
    if len(row) > 8:
        return None
    lx = row[0][0][0][0]  # leftmost x
    if lx > 450:
        return None

    combined = " ".join(r[1] for r in row)
    # Skip if this is a score/column header
    if _HDR_SCORE_KWORDS.search(combined):
        return None

    proc = _match_proc(combined)
    if proc:
        return proc

    # Also detect by digit-section prefix ("2-1", "2-2", "3-") plus 일반/기회 keywords
    if re.search(r'^\d[,\-\.]\d|^[Ⅰ-Ⅹ]', combined.strip()):
        if re.search(r'[일인]반', combined.replace(' ', '')):
            return '학생부종합(일반)'
        if re.search(r'기회', combined.replace(' ', '')):
            return '학생부종합(기회균형)'
        if re.search(r'균[형켓험]', combined.replace(' ', '')):
            return '학생부종합(지역균형)'

    return None


def extract_process_name(top_rows) -> str:
    """Extract process name from first few rows of a page."""
    combined = " ".join(r[1] for row in top_rows[:6] for r in row)
    name = _match_proc(combined)
    return name or '(전형미상)'


# ── Score helpers ─────────────────────────────────────────────────────────────

def parse_num(s: str | None) -> float | None:
    if not s:
        return None
    # strip ratio suffix like "13.5 : 1" → keep first number
    s = re.sub(r'\s*[：:]\s*\d+.*', '', s).strip()
    s = s.replace(',', '').replace('，', '').replace(' ', '').replace('；', '')
    m = re.match(r'^(\d+\.?\d*)$', s)
    return float(m.group(1)) if m else None


def valid_score(v: float | None, score_type: str) -> float | None:
    if v is None:
        return None
    if score_type == '등급':
        return v if 1.0 <= v <= 9.99 else None
    if score_type == '표준점수':
        return v if 50 <= v <= 200 else None
    if score_type == '백분위':
        return v if 1 <= v <= 99.9 else None
    return v


def infer_score_type(admission_type: str, sample_cuts: list[float]) -> str:
    """Infer score type from admission type and a sample of cut values."""
    if admission_type == '수시':
        return '등급'   # 수시는 항상 내신 등급
    # 정시: guess from value range
    valid = [v for v in sample_cuts if v is not None]
    if not valid:
        return '표준점수'
    avg = sum(valid) / len(valid)
    if avg < 10:
        return '등급'       # 정시 등급제 (드물지만 존재)
    if avg < 100:
        return '백분위'
    return '표준점수'


# ── Data-row filtering ─────────────────────────────────────────────────────────

SKIP_DEPTS = {'합계', '총계', '소계', '합 계', '총 계', '소 계', '전체', '계'}

def is_data_row(dept_text: str) -> bool:
    if not dept_text or len(dept_text.replace(' ', '')) < 2:
        return False
    plain = dept_text.replace(' ', '')
    if plain in SKIP_DEPTS:
        return False
    if any(k in plain for k in ['모집단위', '전형명', '대학명', '단과대학']):
        return False
    if re.match(r'^[\d\.\:\%／\s]+$', plain):
        return False
    return True


# ── Nearest-text helper ───────────────────────────────────────────────────────

def nearest_text(row, target_x: float, tol: float = COL_TOL) -> str | None:
    best, best_d = None, float('inf')
    for r in row:
        d = abs(x_center(r) - target_x)
        if d < best_d and d < tol:
            best_d, best = d, r[1]
    return best


# ── Fuzzy dept-name matching ──────────────────────────────────────────────────

def _clean_name(s: str) -> str:
    return re.sub(r'[\d\*\(\)\s]+$', '', s).strip()


def fuzzy_score(a: str, b: str) -> float:
    a, b = _clean_name(a), _clean_name(b)
    if not a or not b:
        return 0.0
    common = sum(1 for c in a if c in b)
    return common / max(len(a), len(b))


def best_dept_match(ocr_name: str, candidates: list[str], threshold=0.55) -> str:
    best, best_sc = ocr_name, 0.0
    for cand in candidates:
        sc = fuzzy_score(ocr_name, cand)
        if sc > best_sc:
            best_sc, best = sc, cand
    return best if best_sc >= threshold else ocr_name


# ── Per-page extraction ───────────────────────────────────────────────────────

def extract_page(regions, admission_type: str):
    """
    Returns list of dicts: {process_name, dept, cut_50, cut_70, competition_rate}.
    Handles multiple 전형 sections on a single page.
    """
    rows = cluster_rows(regions)
    if not rows:
        return []

    col = find_column_map(rows)
    if not col.get('cut_70') and not col.get('cut_50'):
        return []   # no score columns detected — skip

    # Determine initial process name from page header
    current_proc = extract_process_name(rows)

    # Detect score type BEFORE validating (use all visible values)
    raw_cuts = []
    for row in rows:
        if 'cut_70' in col:
            v = parse_num(nearest_text(row, col['cut_70']))
            if v is not None:
                raw_cuts.append(v)
    score_type = infer_score_type(admission_type, raw_cuts)

    results = []
    for row in rows:
        # Check if this row is a new-section header
        new_proc = detect_section_header(row)
        if new_proc:
            current_proc = new_proc
            continue

        # Department name: leftmost region(s) within ~120px of the leftmost x
        # This avoids pulling in adjacent columns (모집인원, 지원인원) that may be within DEPT_MAX_X
        leftmost_x = row[0][0][0][0]
        dept_thresh = min(leftmost_x + 120, DEPT_MAX_X)
        dept_regions = [r for r in row if r[0][0][0] < dept_thresh]
        if not dept_regions:
            continue
        dept_text = " ".join(r[1] for r in dept_regions).strip()
        # Clean trailing numbers / punctuation / category labels
        dept_text = re.sub(r'\s*[\d\*\"\'\`\(\)]+\s*$', '', dept_text).strip()
        dept_text = re.sub(r'^(?:인문|자연|지연|인문사연)\s+', '', dept_text).strip()
        if not is_data_row(dept_text):
            continue

        # Score values
        c50_raw = nearest_text(row, col['cut_50']) if 'cut_50' in col else None
        c70_raw = nearest_text(row, col['cut_70']) if 'cut_70' in col else None
        rate_raw = nearest_text(row, col['rate'])   if 'rate'   in col else None

        c50 = valid_score(parse_num(c50_raw), score_type)
        c70 = valid_score(parse_num(c70_raw), score_type)

        if c50 is None and c70 is None:
            continue

        rate = parse_num(rate_raw)
        # Validate competition rate (should be < 100:1 for most programs)
        if rate is not None and rate > 99:
            rate = None

        results.append({
            'process_name':     current_proc,
            'dept':             dept_text,
            'cut_50':           c50,
            'cut_70':           c70,
            'competition_rate': rate,
            'score_type':       score_type,
        })

    return results


# ── Per-PDF driver ────────────────────────────────────────────────────────────

def process_pdf(pdf_path: str, university: str, year: int, admission_type: str,
                conn: sqlite3.Connection, dry_run: bool = False) -> int:
    reader = get_reader()
    doc    = fitz.open(pdf_path)
    total  = 0

    existing_depts = [r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM admission_department WHERE university=?", (university,)
    ).fetchall()]

    with tempfile.TemporaryDirectory() as tmp:
        for pg in doc:
            img_path = render_page(pg, tmp)
            regions  = reader.readtext(img_path, detail=1, paragraph=False)
            page_rows = extract_page(regions, admission_type)

            if not page_rows:
                continue

            # Group by process name for reporting
            procs = {}
            for r in page_rows:
                procs.setdefault(r['process_name'], []).append(r)

            for proc_name, rows in procs.items():
                score_type = rows[0]['score_type']
                print(f"  pg{pg.number+1}: {proc_name} [{score_type}] → {len(rows)} rows")

                for row in rows:
                    matched = best_dept_match(row['dept'], existing_depts)
                    if dry_run:
                        print(f"    {matched!r:40s}  50%={row['cut_50']}  70%={row['cut_70']}  "
                              f"rate={row['competition_rate']}")
                        continue

                    # Upsert department
                    dept_id = conn.execute("""
                        INSERT INTO admission_department (year, university, campus, track, name)
                        VALUES (?, ?, '', NULL, ?)
                        ON CONFLICT(year, university, campus, name) DO UPDATE SET name=name
                        RETURNING id
                    """, (year, university, matched)).fetchone()[0]

                    grade_type = '내신' if (score_type == '등급' and admission_type == '수시') \
                                 else '수능등급' if (score_type == '등급' and admission_type == '정시') \
                                 else score_type

                    conn.execute("""
                        INSERT INTO admission_result (
                            department_id, result_year, process_name,
                            admission_type, score_type, grade_type,
                            competition_rate, cut_50, cut_70
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(department_id, result_year, process_name) DO UPDATE SET
                            admission_type   = COALESCE(excluded.admission_type,   admission_type),
                            score_type       = COALESCE(excluded.score_type,       score_type),
                            grade_type       = COALESCE(excluded.grade_type,       grade_type),
                            competition_rate = COALESCE(excluded.competition_rate, competition_rate),
                            cut_50           = COALESCE(excluded.cut_50,           cut_50),
                            cut_70           = COALESCE(excluded.cut_70,           cut_70)
                    """, (dept_id, year, proc_name, admission_type, score_type, grade_type,
                          row['competition_rate'], row['cut_50'], row['cut_70']))
                    total += 1

            if not dry_run:
                conn.commit()

    return total


# ── File discovery ────────────────────────────────────────────────────────────

def find_pdfs(results_dir: str, universities: list[str]):
    """Yield (path, university, year, admission_type) for image-based PDFs."""
    found = []
    for uni in universities:
        uni_dir = os.path.join(results_dir, uni)
        if not os.path.isdir(uni_dir):
            continue
        for fname in sorted(os.listdir(uni_dir)):
            if not fname.endswith('.pdf'):
                continue
            pdf_path = os.path.join(uni_dir, fname)
            doc = fitz.open(pdf_path)
            # Check if image-based (very little extractable text)
            chars = sum(len(doc[i].get_text()) for i in range(min(3, len(doc))))
            if chars > 150:
                continue   # has real text — handled by extract_results_pdfs.py

            m = re.search(r'(\d{4})학년도_(수시|정시)', fname)
            if not m:
                continue
            year, adm = int(m.group(1)), m.group(2)
            found.append((pdf_path, uni, year, adm))
    return found


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="OCR-based 입시결과 extractor")
    parser.add_argument('universities', nargs='*', default=TARGET_UNIS,
                        help="Universities to process (default: all 6)")
    parser.add_argument('--dry-run', action='store_true',
                        help="Preview without writing to DB")
    args = parser.parse_args()

    pdfs = find_pdfs(RESULTS_DIR, args.universities)
    if not pdfs:
        print("No image-based PDFs found.")
        return

    conn = sqlite3.connect(DB_PATH)
    grand_total = 0

    for pdf_path, uni, year, adm in pdfs:
        print(f"\n{'='*60}")
        print(f"{uni}  {year}학년도 {adm}")
        count = process_pdf(pdf_path, uni, year, adm, conn, dry_run=args.dry_run)
        print(f"  → {count} records {'(dry-run)' if args.dry_run else 'upserted'}")
        grand_total += count

    conn.close()
    print(f"\n{'='*60}")
    print(f"Grand total: {grand_total}")


if __name__ == '__main__':
    main()
