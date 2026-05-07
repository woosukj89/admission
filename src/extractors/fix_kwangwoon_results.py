"""Fix 광운대학교 admission_result records.

The standard extract_results_batch.py fails to map 광운대's column header
'학생부\n등급\n(진로선택제외)' / '학생부\n등급' to a cut score, producing
records with score_type=None and cut_70=None.

This script reads the pre-extracted JSONs directly and upserts corrected records.

Format:
  Page text: "[수시] 학생부종합(광운참빛인재전형-면접형)"
  Table cols: 계열 | 모집단위 | 모집인원 | 지원인원 | 경쟁률 |
              학생부등급(cut_70) | 논술고사성적 | 충원예비번호 | 충원합격비율 | 비고

Usage:
    python fix_kwangwoon_results.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

DB_PATH = Path("data/admission.db")
RESULTS_DIR = Path("data/results_extracted/광운대학교")
UNIVERSITY = "광운대학교"

# Special 전형 substrings — these will be skipped (we focus on general 전형)
_SKIP_PATTERNS = [
    "특성화고", "재직자", "농어촌", "체육특기", "서해5도",
    "특별전형",
]

FILENAMES = [
    "광운대학교_2024학년도_수시입시결과.json",
    "광운대학교_2025학년도_수시입시결과.json",
]


def _parse_float(val: str | None) -> float | None:
    if not val:
        return None
    val = str(val).strip().replace(",", "")
    try:
        return float(val)
    except ValueError:
        return None


def _extract_process_name(text: str) -> str | None:
    """Extract process name from page text like '[수시] 학생부종합(광운참빛인재전형-면접형)'."""
    m = re.search(r"\[수시\]\s+(.+)", text)
    if m:
        return m.group(1).strip()
    return None


def _get_or_create_dept(conn: sqlite3.Connection, dept_name: str, track: str) -> int:
    """Return department id, creating if not exists."""
    row = conn.execute(
        "SELECT id FROM admission_department WHERE university=? AND name=? AND year=2025",
        (UNIVERSITY, dept_name),
    ).fetchone()
    if row:
        return row[0]
    # Create minimal dept record
    cur = conn.execute(
        """INSERT OR IGNORE INTO admission_department
           (year, university, campus, track, name)
           VALUES (2025, ?, '', ?, ?)""",
        (UNIVERSITY, track, dept_name),
    )
    if cur.lastrowid:
        return cur.lastrowid
    # Race condition: fetch again
    return conn.execute(
        "SELECT id FROM admission_department WHERE university=? AND name=? AND year=2025",
        (UNIVERSITY, dept_name),
    ).fetchone()[0]


def parse_file(filepath: Path) -> list[dict]:
    """Parse a 광운대 수시 results JSON and return list of records."""
    with open(filepath, encoding="utf-8") as f:
        d = json.load(f)

    # Determine result year from filename
    m = re.search(r"(\d{4})학년도", filepath.name)
    result_year = int(m.group(1)) if m else 2025

    records: list[dict] = []
    pages = d.get("pages", [])

    for page in pages:
        text = page.get("text", "")
        process_name = _extract_process_name(text)
        if not process_name:
            continue

        # Skip 특수전형 pages
        if any(pat in process_name for pat in _SKIP_PATTERNS):
            continue

        # Determine score type from process name
        if "학생부교과" in process_name or "학생부종합" in process_name:
            score_type = "등급"
            grade_type = "내신"
            admission_type = "수시"
        elif "논술" in process_name:
            score_type = "등급"  # 학생부 등급 column still exists
            grade_type = "내신"
            admission_type = "수시"
        else:
            score_type = "등급"
            grade_type = "내신"
            admission_type = "수시"

        tables = page.get("tables", [])
        if not tables:
            continue
        table = tables[0]
        data = table.get("data", [])
        if not data:
            continue

        # Parse header to find column indices
        header = [str(c or "").replace("\n", " ").strip() for c in data[0]]
        # Find columns
        dept_col = next((i for i, h in enumerate(header) if "모집" in h and "단위" in h), 1)
        rate_col = next((i for i, h in enumerate(header) if "경쟁률" in h), 4)
        cut_col = next((i for i, h in enumerate(header) if "학생부" in h and "등급" in h), 5)
        논술_col = next((i for i, h in enumerate(header) if "논술" in h), 6)
        track_col = 0  # 계열 is always column 0

        current_track = ""
        for row in data[1:]:
            if not row or all(c is None or str(c).strip() == "" for c in row):
                continue

            # Track 계열 carry-down
            track_val = str(row[track_col] or "").strip()
            if track_val in ("자연", "인문", "예체능", "의약학"):
                current_track = track_val
            elif track_val:
                current_track = track_val  # sometimes other values

            dept_name = str(row[dept_col] or "").replace("\n", " ").strip()
            if not dept_name:
                continue

            cut_70_raw = _parse_float(row[cut_col] if len(row) > cut_col else None)
            rate = _parse_float(row[rate_col] if len(row) > rate_col else None)

            # 논술 score
            논술_score = _parse_float(row[논술_col] if len(row) > 논술_col else None)

            # For 논술전형, use 논술 score if 학생부 등급 is None
            actual_cut = cut_70_raw
            actual_score_type = score_type
            if 논술_score is not None and cut_70_raw is None:
                actual_cut = 논술_score
                actual_score_type = "환산점수"

            records.append({
                "university": UNIVERSITY,
                "dept_name": dept_name,
                "track": current_track,
                "process_name": process_name,
                "result_year": result_year,
                "admission_type": admission_type,
                "score_type": actual_score_type,
                "grade_type": grade_type,
                "competition_rate": rate,
                "cut_70": actual_cut,
            })

    return records


def main(dry_run: bool = False) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    all_records: list[dict] = []
    for fname in FILENAMES:
        fpath = RESULTS_DIR / fname
        if not fpath.exists():
            print(f"File not found: {fpath}")
            continue
        recs = parse_file(fpath)
        print(f"{fname}: {len(recs)} records parsed")
        all_records.extend(recs)

    print(f"\nTotal records: {len(all_records)}")
    if dry_run:
        print("[dry-run] Sample (first 10):")
        for r in all_records[:10]:
            print(f"  {r['result_year']} | {r['process_name'][:30]} | {r['dept_name'][:20]} | cut_70={r['cut_70']} | rate={r['competition_rate']}")
        return

    conn.execute("BEGIN")
    inserted = 0
    for rec in all_records:
        # Get or create department
        dept_id = _get_or_create_dept(conn, rec["dept_name"], rec["track"])

        # Upsert admission_result
        conn.execute("""
            INSERT INTO admission_result
                (department_id, result_year, process_name, admission_type,
                 score_type, grade_type, competition_rate, cut_70)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
        """, (
            dept_id, rec["result_year"], rec["process_name"], rec["admission_type"],
            rec["score_type"], rec["grade_type"], rec["competition_rate"], rec["cut_70"],
        ))
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Done: {inserted} records upserted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
