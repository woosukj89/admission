"""
Fix 부산대학교 admission_result labeling error.

Records where process_name contains "학생부" (수시 전형 names) are incorrectly
labeled as admission_type='정시'. These are 수시 results and should be '수시'.

The grade_type='수능등급' is retained — 부산대 reports admitted students'
수능등급 averages even for 수시 학생부종합/교과전형 (수능최저 context).
"""
import sqlite3

DB_PATH = "data/admission.db"


def run(dry_run: bool = False):
    conn = sqlite3.connect(DB_PATH)

    # Find records to fix
    rows = conn.execute("""
        SELECT r.id, r.process_name, r.admission_type, r.grade_type, d.university
        FROM admission_result r
        JOIN admission_department d ON d.id = r.department_id
        WHERE d.university = '부산대학교'
          AND r.admission_type = '정시'
          AND (r.process_name LIKE '%학생부종합%' OR r.process_name LIKE '%학생부교과%')
    """).fetchall()

    print(f"Found {len(rows)} 부산대 records to fix (admission_type '정시' → '수시')")
    if dry_run:
        print("DRY RUN — showing first 10:")
        for r in rows[:10]:
            print(f"  id={r[0]} process={r[1][:35]} adm={r[2]} grade={r[3]}")
        return

    ids = [r[0] for r in rows]
    conn.execute(
        f"UPDATE admission_result SET admission_type='수시' WHERE id IN ({','.join('?'*len(ids))})",
        ids,
    )
    conn.commit()
    print(f"Updated {len(ids)} records: admission_type '정시' → '수시'")

    # Verify
    remaining = conn.execute("""
        SELECT COUNT(*) FROM admission_result r
        JOIN admission_department d ON d.id = r.department_id
        WHERE d.university = '부산대학교'
          AND r.admission_type = '정시'
          AND (r.process_name LIKE '%학생부종합%' OR r.process_name LIKE '%학생부교과%')
    """).fetchone()[0]
    print(f"Remaining incorrectly labeled: {remaining}")

    conn.close()


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
