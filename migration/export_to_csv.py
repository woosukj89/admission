"""Export SQLite admission data to CSV files for Supabase dashboard import.

Usage:
    python migration/export_to_csv.py

Outputs CSV files to migration/csv/ directory.
Import order in Supabase dashboard:
  1. admission_department.csv
  2. admission_process.csv  (search_vector column will be NULL — run SQL after)
  3. admission_result.csv
"""
import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
ADMISSION_DB = ROOT / "data" / "admission.db"
OUT_DIR = Path(__file__).parent / "csv"
OUT_DIR.mkdir(exist_ok=True)


def export_table(src, table: str, out_path: Path, col_map: dict | None = None):
    """Export a table to CSV. col_map renames columns if needed."""
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"  {table}: 0 rows, skipping")
        return
    cols = [d[0] for d in src.execute(f"SELECT * FROM {table} LIMIT 0").description]
    out_cols = [col_map.get(c, c) for c in cols] if col_map else cols
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(out_cols)
        for row in rows:
            w.writerow(row)
    print(f"  {table}: {len(rows):,} rows → {out_path.name}")


def main():
    print(f"Exporting from {ADMISSION_DB}...")
    src = sqlite3.connect(str(ADMISSION_DB))

    # admission_department — column names match PostgreSQL schema
    export_table(src, "admission_department",
                 OUT_DIR / "admission_department.csv")

    # admission_process — exclude FTS5 virtual table cols, add empty search_vector
    procs = src.execute("SELECT * FROM admission_process").fetchall()
    cols = [d[0] for d in src.execute("SELECT * FROM admission_process LIMIT 0").description]
    out_path = OUT_DIR / "admission_process.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)  # search_vector will be NULL on import; populated via SQL after
        for row in procs:
            w.writerow(row)
    print(f"  admission_process: {len(procs):,} rows → {out_path.name}")

    # admission_result
    export_table(src, "admission_result",
                 OUT_DIR / "admission_result.csv")

    src.close()
    print(f"\nCSV files saved to {OUT_DIR}/")
    print("\nNext steps in Supabase Dashboard → Table Editor:")
    print("  1. Select 'admission_department' → Import data → upload admission_department.csv")
    print("  2. Select 'admission_process'   → Import data → upload admission_process.csv")
    print("  3. Select 'admission_result'    → Import data → upload admission_result.csv")
    print("\nAfter import, run this in Supabase SQL Editor to build full-text search index:")
    print("  UPDATE admission_process SET search_vector =")
    print("    to_tsvector('simple', COALESCE(process_name,'') || ' ' || COALESCE(content,''));")


if __name__ == "__main__":
    main()
