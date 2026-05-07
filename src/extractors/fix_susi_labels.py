"""
Fix admission_result labeling error: 수시 학생부 전형 incorrectly tagged as 정시.

Records where process_name contains '학생부종합' or '학생부교과' (수시 전형 names)
but admission_type='정시'. These are 수시 results and should be labeled '수시'.

Affected universities found during 2026-03-29 verification:
  조선대학교: 405건
  경북대학교:  90건
  건국대학교:  47건
  성균관대학교: 43건
  고려대학교(세종): 24건
  전남대학교:  16건
  차의과학대학교: 6건
  영남대학교:   3건
  경기대학교:   2건
  (부산대학교:  already fixed by fix_busan_labels.py)

Note: grade_type is left unchanged — some universities report 수능등급 averages
for 수시 합격생 (수능최저 context).
"""
import sqlite3

DB_PATH = "data/admission.db"


def run(dry_run: bool = False):
    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute("""
        SELECT r.id, r.process_name, r.admission_type, r.grade_type, d.university
        FROM admission_result r
        JOIN admission_department d ON d.id = r.department_id
        WHERE r.admission_type = '정시'
          AND (r.process_name LIKE '%학생부종합%' OR r.process_name LIKE '%학생부교과%')
    """).fetchall()

    # Group by university for reporting
    from collections import Counter
    by_uni = Counter(r[4] for r in rows)
    print(f"Found {len(rows)} records to fix across {len(by_uni)} universities:")
    for uni, cnt in by_uni.most_common():
        print(f"  {uni}: {cnt}건")

    if dry_run:
        print("\nDRY RUN — no changes made.")
        print("First 10 records:")
        for r in rows[:10]:
            print(f"  id={r[0]} uni={r[4]} process={r[1][:35]} adm={r[2]} grade={r[3]}")
        return

    ids = [r[0] for r in rows]
    conn.execute(
        f"UPDATE admission_result SET admission_type='수시' WHERE id IN ({','.join('?'*len(ids))})",
        ids,
    )
    conn.commit()
    print(f"\nUpdated {len(ids)} records: admission_type '정시' → '수시'")

    remaining = conn.execute("""
        SELECT COUNT(*) FROM admission_result r
        JOIN admission_department d ON d.id = r.department_id
        WHERE r.admission_type = '정시'
          AND (r.process_name LIKE '%학생부종합%' OR r.process_name LIKE '%학생부교과%')
    """).fetchone()[0]
    print(f"Remaining incorrectly labeled: {remaining}")
    conn.close()


if __name__ == "__main__":
    import sys
    dry_run = "--dry-run" in sys.argv
    run(dry_run=dry_run)
