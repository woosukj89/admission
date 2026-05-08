"""Phase 2 export: smart content extraction for all admission_process rows.

Extracts content NOT already captured in structured columns
(process_name, process_type, admission_type, quota, attributes).

Outputs migration/csv/supabase/process_content.csv  (process_id, extracted_content)
Then upload that CSV to Supabase as a temp table and run the UPDATE SQL.

Usage:
    python migration/extract_content.py

After generating the CSV, in Supabase:
  1. Create temp table:
     CREATE TEMP TABLE process_content_temp (process_id INT, content TEXT);
  2. Import process_content.csv into process_content_temp via Table Editor
  3. Run update:
     UPDATE admission_process p
     SET content = t.content,
         search_vector = to_tsvector('simple',
             COALESCE(p.process_name,'') || ' ' || COALESCE(t.content,''))
     FROM process_content_temp t
     WHERE p.id = t.process_id AND t.content != '';
"""
import csv
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
ADMISSION_DB = ROOT / "data" / "admission.db"
OUT_DIR = Path(__file__).parent / "csv" / "supabase"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_CHARS = 1500

# Section headers that duplicate structured columns — skip these sections
SKIP_SECTION_RE = re.compile(
    r"^\s*(?:[0-9]+[\.．]?\s*)?"
    r"(모집\s*인원|수능\s*최저\s*학력\s*기준|전형\s*요소|전형\s*명"
    r"|모집\s*단위\s*현황|지원\s*현황|합격자\s*현황|전형\s*구분"
    r"|모집\s*정원|전형\s*방법\s*요약|대학\s*수학\s*능력\s*시험)",
    re.IGNORECASE,
)

# Numbered section header pattern (e.g. "1.", "2.", "가.", "나.")
ANY_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:[0-9]+[\.．]|[가나다라마바사아자차카타파하][\.．])\s*\S",
    re.MULTILINE,
)

# Collapse excessive whitespace / repeated newlines from PDF extraction
WHITESPACE_RE = re.compile(r"[ \t]{3,}")
NEWLINE_RE = re.compile(r"\n{4,}")


def clean(text: str) -> str:
    text = WHITESPACE_RE.sub(" ", text)
    text = NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def extract_key_content(content: str) -> str:
    """Keep all content NOT already in structured columns, cap at MAX_CHARS."""
    if not content:
        return ""

    content = clean(content)
    splits = list(ANY_SECTION_RE.finditer(content))

    if not splits:
        # No numbered section headers — just return cleaned first N chars
        return content[:MAX_CHARS]

    kept = []
    for i, m in enumerate(splits):
        end = splits[i + 1].start() if i + 1 < len(splits) else len(content)
        section = content[m.start():end].strip()
        header_line = section.split("\n")[0]
        if SKIP_SECTION_RE.match(header_line):
            continue
        kept.append(section)

    result = "\n".join(kept)
    return result[:MAX_CHARS]


SPECIAL_KEYWORDS = [
    "지역인재", "농어촌", "기회균형", "기초생활", "특성화고",
    "장애인", "재직자", "사회배려", "저소득",
]

DETAIL_IDS_SQL = """
SELECT DISTINCT p.id
FROM admission_process p
JOIN admission_department d ON d.id = p.department_id
WHERE p.id IN (
    SELECT id FROM (
        SELECT p2.id,
               ROW_NUMBER() OVER (
                   PARTITION BY d2.university
                   ORDER BY COALESCE(p2.quota, 0) DESC
               ) AS rn
        FROM admission_process p2
        JOIN admission_department d2 ON d2.id = p2.department_id
    ) WHERE rn <= 2
    UNION
    SELECT id FROM (
        SELECT p3.id,
               ROW_NUMBER() OVER (
                   PARTITION BY d3.university, kw
                   ORDER BY COALESCE(p3.quota, 0) DESC
               ) AS rn
        FROM admission_process p3
        JOIN admission_department d3 ON d3.id = p3.department_id
        JOIN ({keyword_union}) ON p3.process_name LIKE '%' || kw || '%'
    ) WHERE rn <= 1
)
"""


def main():
    print(f"Loading from {ADMISSION_DB}...")
    conn = sqlite3.connect(str(ADMISSION_DB))

    keyword_union = " UNION ".join(f"SELECT '{kw}' AS kw" for kw in SPECIAL_KEYWORDS)
    detail_ids = {r[0] for r in conn.execute(DETAIL_IDS_SQL.format(keyword_union=keyword_union)).fetchall()}
    print(f"  Detail rows (full content already exported): {len(detail_ids):,}")

    procs = conn.execute("SELECT id, content FROM admission_process").fetchall()
    print(f"  Processing {len(procs):,} rows...")

    out = OUT_DIR / "process_content.csv"
    short_count = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["process_id", "extracted_content"])
        for proc_id, content in procs:
            if proc_id in detail_ids:
                # Detail table has full content — leave main table content empty
                w.writerow([proc_id, ""])
                continue
            extracted = extract_key_content(content or "")
            if len(extracted) < 100:
                short_count += 1
            w.writerow([proc_id, extracted])

    size_mb = out.stat().st_size / 1e6
    print(f"  Wrote {len(procs):,} rows → {out.name} ({size_mb:.1f}MB)")
    print(f"  Rows with <100 chars extracted (candidates for AI Phase 3): {short_count:,}")

    conn.close()
    print(f"\nCSV saved to {out}")
    print("\n--- Supabase: upload process_content.csv as temp table, then run ---")
    print("CREATE TEMP TABLE process_content_temp (process_id INT, content TEXT);")
    print("-- (import CSV into process_content_temp via Table Editor)")
    print()
    print("UPDATE admission_process p")
    print("SET content = t.content,")
    print("    search_vector = to_tsvector('simple',")
    print("        COALESCE(p.process_name,'') || ' ' || COALESCE(t.content,''))")
    print("FROM process_content_temp t")
    print("WHERE p.id = t.process_id AND t.content != '';")
    print()
    print("DROP TABLE process_content_temp;")


if __name__ == "__main__":
    main()
