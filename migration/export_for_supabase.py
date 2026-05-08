"""Phase 1 export: generates 4 small CSVs for Supabase dashboard import.

Outputs to migration/csv/supabase/:
  admission_department.csv        (~2.5MB)
  admission_process_nocontent.csv (~8MB)   content column is empty
  admission_process_detail.csv    (~25MB)  ~790 rows with full content
  admission_result.csv            (~8MB)

Usage:
    python migration/export_for_supabase.py

Supabase SQL Editor — run BEFORE importing:
  TRUNCATE admission_result, admission_process, admission_department RESTART IDENTITY CASCADE;
  CREATE TABLE IF NOT EXISTS admission_process_detail (
      process_id   INTEGER PRIMARY KEY REFERENCES admission_process(id),
      full_content TEXT NOT NULL
  );

Dashboard import order:
  1. admission_department.csv
  2. admission_process_nocontent.csv   (content will be empty — OK for Phase 1)
  3. admission_process_detail.csv      (into admission_process_detail table)
  4. admission_result.csv

After import, run in SQL Editor:
  UPDATE admission_process
  SET search_vector = to_tsvector('simple', COALESCE(process_name, ''));
"""
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
ADMISSION_DB = ROOT / "data" / "admission.db"
OUT_DIR = Path(__file__).parent / "csv" / "supabase"
OUT_DIR.mkdir(parents=True, exist_ok=True)

SPECIAL_KEYWORDS = [
    "지역인재", "농어촌", "기회균형", "기초생활", "특성화고",
    "장애인", "재직자", "사회배려", "저소득",
]

DETAIL_SELECTION_SQL = """
SELECT DISTINCT p.id
FROM admission_process p
JOIN admission_department d ON d.id = p.department_id
WHERE p.id IN (
    -- Top-2 by quota per university
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
    -- Top-1 per university per special 전형 keyword
    SELECT id FROM (
        SELECT p3.id,
               ROW_NUMBER() OVER (
                   PARTITION BY d3.university, kw
                   ORDER BY COALESCE(p3.quota, 0) DESC
               ) AS rn
        FROM admission_process p3
        JOIN admission_department d3 ON d3.id = p3.department_id
        JOIN (
            {keyword_union}
        ) ON p3.process_name LIKE '%' || kw || '%'
    ) WHERE rn <= 1
)
AND LENGTH(p.content) > 0
"""


def build_detail_ids(conn) -> set:
    keyword_union = " UNION ".join(
        f"SELECT '{kw}' AS kw" for kw in SPECIAL_KEYWORDS
    )
    sql = DETAIL_SELECTION_SQL.format(keyword_union=keyword_union)
    rows = conn.execute(sql).fetchall()
    return {r[0] for r in rows}


def main():
    print(f"Exporting from {ADMISSION_DB}...")
    conn = sqlite3.connect(str(ADMISSION_DB))

    # --- admission_department (unchanged) ---
    rows = conn.execute("SELECT * FROM admission_department").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM admission_department LIMIT 0").description]
    out = OUT_DIR / "admission_department.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    print(f"  admission_department: {len(rows):,} rows → {out.name} ({out.stat().st_size/1e6:.1f}MB)")

    # --- Find detail rows ---
    print("  Selecting detail rows (top-quota + special 전형s)...")
    detail_ids = build_detail_ids(conn)
    print(f"  Detail rows selected: {len(detail_ids):,}")

    # --- admission_process (no content) ---
    procs = conn.execute("SELECT * FROM admission_process").fetchall()
    proc_cols = [d[0] for d in conn.execute("SELECT * FROM admission_process LIMIT 0").description]
    content_idx = proc_cols.index("content")
    search_vector_idx = proc_cols.index("search_vector") if "search_vector" in proc_cols else -1

    out = OUT_DIR / "admission_process_nocontent.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        # Write header — content column present but will be empty; drop search_vector
        out_cols = [c for c in proc_cols if c != "search_vector"]
        w.writerow(out_cols)
        for row in procs:
            row = list(row)
            row[content_idx] = ""  # empty content for Phase 1
            if search_vector_idx >= 0:
                row.pop(search_vector_idx)
            w.writerow(row)
    print(f"  admission_process (no content): {len(procs):,} rows → {out.name} ({out.stat().st_size/1e6:.1f}MB)")

    # --- admission_process_detail (full content for ~790 rows) ---
    out = OUT_DIR / "admission_process_detail.csv"
    detail_rows = [(row[0], row[content_idx]) for row in procs if row[0] in detail_ids and row[content_idx]]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["process_id", "full_content"])
        w.writerows(detail_rows)
    print(f"  admission_process_detail: {len(detail_rows):,} rows → {out.name} ({out.stat().st_size/1e6:.1f}MB)")

    # --- admission_result (unchanged) ---
    rows = conn.execute("SELECT * FROM admission_result").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM admission_result LIMIT 0").description]
    out = OUT_DIR / "admission_result.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    print(f"  admission_result: {len(rows):,} rows → {out.name} ({out.stat().st_size/1e6:.1f}MB)")

    conn.close()
    total = sum(p.stat().st_size for p in OUT_DIR.glob("*.csv")) / 1e6
    print(f"\nAll CSVs saved to {OUT_DIR}/  (total: {total:.1f}MB)")
    print("\n--- Supabase SQL Editor (run BEFORE importing) ---")
    print("TRUNCATE admission_result, admission_process, admission_department RESTART IDENTITY CASCADE;")
    print()
    print("CREATE TABLE IF NOT EXISTS admission_process_detail (")
    print("    process_id   INTEGER PRIMARY KEY REFERENCES admission_process(id),")
    print("    full_content TEXT NOT NULL")
    print(");")
    print()
    print("--- Dashboard import order ---")
    print("1. admission_department.csv")
    print("2. admission_process_nocontent.csv  → into 'admission_process' table")
    print("3. admission_process_detail.csv     → into 'admission_process_detail' table")
    print("4. admission_result.csv")
    print()
    print("--- SQL Editor (after all imports) ---")
    print("UPDATE admission_process SET search_vector = to_tsvector('simple', COALESCE(process_name, ''));")


if __name__ == "__main__":
    main()
