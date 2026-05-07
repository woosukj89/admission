"""Storage for extracted admission data: departments, processes (전형), and results (입시결과).

Uses SQLite with JSON columns for freeform attributes and FTS5 for full-text search.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from ..config import settings
from .db_factory import is_postgres, DATABASE_URL


class AdmissionStore:
    """Stores and queries extracted admission data.

    Three record types:
    - department: structured anchor (year, university, campus, track, name)
    - process: semi-structured 전형 records linked to a department
    - result: semi-structured 입시결과 records linked to a department
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self) -> Generator:
        if is_postgres:
            import psycopg2
            from .db_factory import DBConn
            raw = psycopg2.connect(DATABASE_URL)
            conn = DBConn(raw, is_pg=True)
            try:
                yield conn
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()
        else:
            raw = sqlite3.connect(str(self.db_path))
            raw.row_factory = sqlite3.Row
            raw.execute("PRAGMA journal_mode=WAL")
            raw.execute("PRAGMA foreign_keys=ON")
            from .db_factory import DBConn
            conn = DBConn(raw, is_pg=False)
            try:
                yield conn
                raw.commit()
            except Exception:
                raw.rollback()
                raise
            finally:
                raw.close()

    def _init_db(self) -> None:
        if is_postgres:
            return  # Tables created by migration/migrate_to_postgres.py
        with self._conn() as conn:
            # Migrate admission_result if it has the old schema (missing result_year)
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(admission_result)"
            ).fetchall()]
            if cols and 'result_year' not in cols:
                # Old schema detected — drop and recreate (safe only when empty)
                count = conn.execute("SELECT COUNT(*) FROM admission_result").fetchone()[0]
                if count == 0:
                    conn.execute("DROP TABLE IF EXISTS admission_result")
                    conn.execute("DROP TABLE IF EXISTS admission_result_fts")
                else:
                    raise RuntimeError(
                        "admission_result has old schema but is not empty. "
                        "Please migrate manually."
                    )

            # E1: Add grade_type column if missing (분리 내신 vs 수능등급)
            if cols and 'grade_type' not in cols:
                conn.execute(
                    "ALTER TABLE admission_result ADD COLUMN grade_type TEXT"
                )
                conn.execute("""
                    UPDATE admission_result
                    SET grade_type = CASE
                        WHEN score_type = '등급' AND admission_type = '수시' THEN '내신'
                        WHEN score_type = '등급' AND admission_type = '정시' THEN '수능등급'
                        WHEN score_type IS NOT NULL THEN score_type
                        ELSE NULL
                    END
                    WHERE grade_type IS NULL
                """)

            # E2: Add process_id FK column if missing
            if cols and 'process_id' not in cols:
                conn.execute(
                    "ALTER TABLE admission_result ADD COLUMN process_id INTEGER"
                )
                # Populate via exact match on (department_id, process_name)
                conn.execute("""
                    UPDATE admission_result
                    SET process_id = (
                        SELECT p.id FROM admission_process p
                        WHERE p.department_id = admission_result.department_id
                          AND p.process_name = admission_result.process_name
                        LIMIT 1
                    )
                    WHERE process_id IS NULL
                """)

            conn.executescript("""
                CREATE TABLE IF NOT EXISTS admission_department (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    year INTEGER NOT NULL,
                    university TEXT NOT NULL,
                    campus TEXT,
                    track TEXT,
                    name TEXT NOT NULL,
                    UNIQUE(year, university, campus, name)
                );

                CREATE TABLE IF NOT EXISTS admission_process (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL REFERENCES admission_department(id),
                    process_name TEXT NOT NULL,
                    process_type TEXT,
                    admission_type TEXT,
                    quota INTEGER,
                    content TEXT,
                    attributes TEXT DEFAULT '{}',
                    UNIQUE(department_id, process_name)
                );

                CREATE TABLE IF NOT EXISTS admission_result (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    department_id INTEGER NOT NULL REFERENCES admission_department(id),
                    result_year INTEGER NOT NULL,
                    process_name TEXT NOT NULL,
                    process_id INTEGER REFERENCES admission_process(id),
                    admission_type TEXT,
                    score_type TEXT,
                    grade_type TEXT,
                    competition_rate REAL,
                    average_score REAL,
                    cut_50 REAL,
                    cut_60 REAL,
                    cut_70 REAL,
                    cut_80 REAL,
                    cut_85 REAL,
                    cut_90 REAL,
                    content TEXT,
                    attributes TEXT DEFAULT '{}',
                    UNIQUE(department_id, result_year, process_name)
                );

                CREATE INDEX IF NOT EXISTS idx_dept_univ ON admission_department(university);
                CREATE INDEX IF NOT EXISTS idx_dept_year ON admission_department(year);
                CREATE INDEX IF NOT EXISTS idx_proc_dept ON admission_process(department_id);
                CREATE INDEX IF NOT EXISTS idx_proc_name ON admission_process(process_name);
                CREATE INDEX IF NOT EXISTS idx_proc_type ON admission_process(process_type);
                CREATE INDEX IF NOT EXISTS idx_proc_adm ON admission_process(admission_type);
                CREATE INDEX IF NOT EXISTS idx_res_dept ON admission_result(department_id);
                CREATE INDEX IF NOT EXISTS idx_res_name ON admission_result(process_name);
                CREATE INDEX IF NOT EXISTS idx_res_year ON admission_result(result_year);
                CREATE INDEX IF NOT EXISTS idx_res_adm ON admission_result(admission_type);
                CREATE INDEX IF NOT EXISTS idx_res_score_type ON admission_result(score_type);
                CREATE INDEX IF NOT EXISTS idx_res_grade_type ON admission_result(grade_type);
                CREATE INDEX IF NOT EXISTS idx_res_process_id ON admission_result(process_id);
                CREATE INDEX IF NOT EXISTS idx_res_cut70 ON admission_result(cut_70);
                CREATE INDEX IF NOT EXISTS idx_res_avg ON admission_result(average_score);
            """)

            # FTS5 virtual tables for full-text search (standalone, synced via triggers)
            conn.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS admission_process_fts USING fts5(
                    process_name, content_text, university, department_name,
                    tokenize='unicode61'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS admission_result_fts USING fts5(
                    process_name, content_text, university, department_name,
                    tokenize='unicode61'
                );
            """)

            # Triggers to keep FTS in sync with main tables
            for table, fts in [("admission_process", "admission_process_fts"),
                               ("admission_result", "admission_result_fts")]:
                conn.executescript(f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_ai AFTER INSERT ON {table} BEGIN
                        INSERT INTO {fts}(rowid, process_name, content_text, university, department_name)
                        SELECT NEW.id, NEW.process_name, NEW.content,
                               d.university, d.name
                        FROM admission_department d WHERE d.id = NEW.department_id;
                    END;

                    CREATE TRIGGER IF NOT EXISTS {table}_ad AFTER DELETE ON {table} BEGIN
                        DELETE FROM {fts} WHERE rowid = OLD.id;
                    END;

                    CREATE TRIGGER IF NOT EXISTS {table}_au AFTER UPDATE ON {table} BEGIN
                        DELETE FROM {fts} WHERE rowid = OLD.id;
                        INSERT INTO {fts}(rowid, process_name, content_text, university, department_name)
                        SELECT NEW.id, NEW.process_name, NEW.content,
                               d.university, d.name
                        FROM admission_department d WHERE d.id = NEW.department_id;
                    END;
                """)

    # ── Department ─────────────────────────────────────────────

    def upsert_department(self, *, year: int, university: str,
                          campus: str | None = None, track: str | None = None,
                          name: str) -> int:
        """Insert or update a department. Returns the department id."""
        # Normalize NULL campus to '' so UNIQUE constraint works (NULL != NULL in SQL)
        campus = campus or ""
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO admission_department (year, university, campus, track, name)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(year, university, campus, name) DO UPDATE SET
                    track = COALESCE(excluded.track, track)
                RETURNING id
            """, (year, university, campus, track, name))
            return cursor.fetchone()[0]

    def get_department(self, department_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM admission_department WHERE id = ?", (department_id,)
            ).fetchone()
            return dict(row) if row else None

    def find_departments(self, *, university: str | None = None,
                         year: int | None = None, track: str | None = None,
                         name: str | None = None) -> list[dict]:
        clauses, params = [], []
        if university:
            clauses.append("university = ?")
            params.append(university)
        if year:
            clauses.append("year = ?")
            params.append(year)
        if track:
            clauses.append("track = ?")
            params.append(track)
        if name:
            clauses.append("name LIKE ?")
            params.append(f"%{name}%")

        where = " AND ".join(clauses) if clauses else "1=1"
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM admission_department WHERE {where} ORDER BY university, name",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def list_distinct_departments(self, *, year: int = 2025,
                                   keyword: str | None = None,
                                   limit: int = 200) -> list[str]:
        """Return distinct department names for a year, optionally filtered by keyword."""
        params: list = [year]
        kw_clause = ""
        if keyword:
            kw_clause = "AND name LIKE ?"
            params.append(f"%{keyword}%")
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT name FROM admission_department "
                f"WHERE year = ? {kw_clause} ORDER BY name LIMIT ?",
                params,
            ).fetchall()
        return [r[0] for r in rows]

    # ── Process ────────────────────────────────────────────────

    def upsert_process(self, *, department_id: int, process_name: str,
                       process_type: str | None = None,
                       admission_type: str | None = None,
                       quota: int | None = None,
                       content: str | None = None,
                       attributes: dict | None = None) -> int:
        """Insert or update a process record. Returns the process id."""
        attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO admission_process
                    (department_id, process_name, process_type, admission_type, quota, content, attributes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(department_id, process_name) DO UPDATE SET
                    process_type = COALESCE(excluded.process_type, process_type),
                    admission_type = COALESCE(excluded.admission_type, admission_type),
                    quota = COALESCE(excluded.quota, quota),
                    content = COALESCE(excluded.content, content),
                    attributes = CASE
                        WHEN excluded.attributes != '{}' THEN
                            json_patch(COALESCE(attributes, '{}'), excluded.attributes)
                        ELSE attributes
                    END
                RETURNING id
            """, (department_id, process_name, process_type, admission_type,
                  quota, content, attrs_json))
            return cursor.fetchone()[0]

    def get_process(self, process_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT p.*, d.university, d.campus, d.year, d.track, d.name as department_name
                FROM admission_process p
                JOIN admission_department d ON d.id = p.department_id
                WHERE p.id = ?
            """, (process_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["attributes"] = json.loads(result.get("attributes") or "{}")
            return result

    def find_processes(self, *, department_id: int | None = None,
                       university: str | None = None,
                       process_name: str | None = None,
                       process_type: str | None = None,
                       admission_type: str | None = None,
                       year: int | None = None,
                       limit: int = 500) -> list[dict]:
        clauses, params = [], []
        if department_id:
            clauses.append("p.department_id = ?")
            params.append(department_id)
        if university:
            clauses.append("d.university = ?")
            params.append(university)
        if process_name:
            clauses.append("p.process_name LIKE ?")
            params.append(f"%{process_name}%")
        if process_type:
            clauses.append("p.process_type = ?")
            params.append(process_type)
        if admission_type:
            clauses.append("p.admission_type = ?")
            params.append(admission_type)
        if year:
            clauses.append("d.year = ?")
            params.append(year)

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT p.*, d.university, d.campus, d.year, d.track, d.name as department_name
                FROM admission_process p
                JOIN admission_department d ON d.id = p.department_id
                WHERE {where}
                ORDER BY d.university, d.name, p.process_name
                LIMIT ?
            """, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["attributes"] = json.loads(d.get("attributes") or "{}")
                results.append(d)
            return results

    def update_process_attr(self, process_id: int, key: str, value: Any) -> None:
        """Set a single attribute key on a process. Merges into existing attributes."""
        patch = json.dumps({key: value}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute("""
                UPDATE admission_process
                SET attributes = json_patch(COALESCE(attributes, '{}'), ?)
                WHERE id = ?
            """, (patch, process_id))

    def get_process_attr(self, process_id: int, key: str) -> Any:
        """Get a single attribute value from a process."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT json_extract(attributes, ?) FROM admission_process WHERE id = ?",
                (f"$.{key}", process_id),
            ).fetchone()
            if row and row[0] is not None:
                try:
                    return json.loads(row[0]) if isinstance(row[0], str) else row[0]
                except (json.JSONDecodeError, TypeError):
                    return row[0]
            return None

    # ── Result ─────────────────────────────────────────────────

    @staticmethod
    def _compute_grade_type(score_type: str | None, admission_type: str | None) -> str | None:
        """E1: Derive grade_type from score_type + admission_type."""
        if score_type == "등급":
            if admission_type == "수시":
                return "내신"
            if admission_type == "정시":
                return "수능등급"
            return "등급"  # unknown admission type — keep generic
        return score_type  # 표준점수 / 백분위 / 환산점수 / None

    def upsert_result(self, *, department_id: int, result_year: int,
                      process_name: str,
                      admission_type: str | None = None,
                      score_type: str | None = None,
                      competition_rate: float | None = None,
                      average_score: float | None = None,
                      cut_50: float | None = None,
                      cut_60: float | None = None,
                      cut_70: float | None = None,
                      cut_80: float | None = None,
                      cut_85: float | None = None,
                      cut_90: float | None = None,
                      content: str | None = None,
                      attributes: dict | None = None) -> int:
        """Insert or update a result record. Returns the result id."""
        attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
        grade_type = self._compute_grade_type(score_type, admission_type)
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO admission_result (
                    department_id, result_year, process_name, admission_type,
                    score_type, grade_type, competition_rate, average_score,
                    cut_50, cut_60, cut_70, cut_80, cut_85, cut_90,
                    content, attributes
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(department_id, result_year, process_name) DO UPDATE SET
                    admission_type = COALESCE(excluded.admission_type, admission_type),
                    score_type = COALESCE(excluded.score_type, score_type),
                    grade_type = COALESCE(excluded.grade_type, grade_type),
                    competition_rate = COALESCE(excluded.competition_rate, competition_rate),
                    average_score = COALESCE(excluded.average_score, average_score),
                    cut_50 = COALESCE(excluded.cut_50, cut_50),
                    cut_60 = COALESCE(excluded.cut_60, cut_60),
                    cut_70 = COALESCE(excluded.cut_70, cut_70),
                    cut_80 = COALESCE(excluded.cut_80, cut_80),
                    cut_85 = COALESCE(excluded.cut_85, cut_85),
                    cut_90 = COALESCE(excluded.cut_90, cut_90),
                    content = COALESCE(excluded.content, content),
                    attributes = CASE
                        WHEN excluded.attributes != '{}' THEN
                            json_patch(COALESCE(attributes, '{}'), excluded.attributes)
                        ELSE attributes
                    END
                RETURNING id
            """, (department_id, result_year, process_name, admission_type,
                  score_type, grade_type, competition_rate, average_score,
                  cut_50, cut_60, cut_70, cut_80, cut_85, cut_90,
                  content, attrs_json))
            return cursor.fetchone()[0]

    def get_result(self, result_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("""
                SELECT r.*, d.university, d.campus, d.year, d.track, d.name as department_name
                FROM admission_result r
                JOIN admission_department d ON d.id = r.department_id
                WHERE r.id = ?
            """, (result_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["attributes"] = json.loads(result.get("attributes") or "{}")
            return result

    def find_results(self, *, department_id: int | None = None,
                     university: str | None = None,
                     process_name: str | None = None,
                     year: int | None = None,
                     result_year: int | None = None,
                     admission_type: str | None = None,
                     limit: int = 500) -> list[dict]:
        clauses, params = [], []
        if department_id:
            clauses.append("r.department_id = ?")
            params.append(department_id)
        if university:
            clauses.append("d.university = ?")
            params.append(university)
        if process_name:
            clauses.append("r.process_name LIKE ?")
            params.append(f"%{process_name}%")
        if year:
            clauses.append("d.year = ?")
            params.append(year)
        if result_year:
            clauses.append("r.result_year = ?")
            params.append(result_year)
        if admission_type:
            clauses.append("r.admission_type = ?")
            params.append(admission_type)

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT r.*, d.university, d.campus, d.year, d.track, d.name as department_name
                FROM admission_result r
                JOIN admission_department d ON d.id = r.department_id
                WHERE {where}
                ORDER BY d.university, d.name, r.process_name
                LIMIT ?
            """, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["attributes"] = json.loads(d.get("attributes") or "{}")
                results.append(d)
            return results

    def find_results_by_score(self, *, student_grade: float,
                               score_type: str = "등급",
                               admission_type: str | None = None,
                               grade_type: str | None = None,
                               result_year: int | None = None,
                               use_cut: str = "cut_70",
                               limit: int = 200) -> list[dict]:
        """Find departments where the student is likely to be admitted.

        For 등급 scoring: student_grade >= cut_70 means student's grade is
        equal or worse than the 70% cutoff, i.e., ~30% chance.
        Typically: student_grade <= cut_70 means good match.

        Args:
            student_grade: student's score (등급: 1.0-9.0, lower=better)
            score_type: '등급', '표준점수', '백분위'
            admission_type: '수시' or '정시' or None for all (ignored when grade_type is set)
            grade_type: E1 — '내신', '수능등급', '표준점수', '백분위', '환산점수'.
                        When set, filters by grade_type column directly (more precise
                        than admission_type since it was derived at insert time).
            result_year: filter by specific result year
            use_cut: which cut to compare against ('cut_60', 'cut_70', 'cut_80', 'average_score')
            limit: max results
        """
        # E1: use grade_type column when available; fall back to score_type + admission_type
        if grade_type:
            clauses = [f"r.grade_type = ?", f"r.{use_cut} IS NOT NULL"]
            params: list[Any] = [grade_type]
            # Determine comparison direction from score_type hint
            is_grade = grade_type in ("내신", "수능등급", "등급")
        else:
            clauses = [f"r.score_type = ?", f"r.{use_cut} IS NOT NULL"]
            params: list[Any] = [score_type]
            is_grade = score_type == "등급"

        # For 등급: lower is better, so student can get in if student_grade <= cut_N
        # (their grade is better than N% of admitted students)
        if is_grade:
            clauses.append(f"r.{use_cut} >= ?")
        else:
            # For 표준점수/백분위: higher is better
            clauses.append(f"r.{use_cut} <= ?")
        params.append(student_grade)

        if grade_type is None and admission_type:
            clauses.append("r.admission_type = ?")
            params.append(admission_type)
        if result_year:
            clauses.append("r.result_year = ?")
            params.append(result_year)

        # Order: for 등급, closest match first (smallest cut_N that is >= student_grade)
        order = f"r.{use_cut} ASC" if score_type == "등급" else f"r.{use_cut} DESC"
        params.append(limit)

        where = " AND ".join(clauses)
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT r.*, d.university, d.campus, d.year, d.track, d.name as department_name
                FROM admission_result r
                JOIN admission_department d ON d.id = r.department_id
                WHERE {where}
                ORDER BY {order}
                LIMIT ?
            """, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["attributes"] = json.loads(d.get("attributes") or "{}")
                results.append(d)
            return results

    def update_result_attr(self, result_id: int, key: str, value: Any) -> None:
        patch = json.dumps({key: value}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute("""
                UPDATE admission_result
                SET attributes = json_patch(COALESCE(attributes, '{}'), ?)
                WHERE id = ?
            """, (patch, result_id))

    # ── Search ─────────────────────────────────────────────────

    def search(self, query: str, *, table: str = "process",
               university: str | None = None, limit: int = 50) -> list[dict]:
        """Full-text search across process or result content."""
        if table not in ("process", "result"):
            raise ValueError(f"table must be 'process' or 'result', got '{table}'")

        if is_postgres:
            # PostgreSQL: tsvector for process, ILIKE for result
            extra_clause = ""
            if table == "process":
                pg_params: list[Any] = [query]
                if university:
                    extra_clause = "AND d.university = ?"
                    pg_params.append(university)
                pg_params.extend([query, limit])
                pg_sql = f"""
                    SELECT p.*, d.university, d.campus, d.year, d.track, d.name as department_name
                    FROM admission_process p
                    JOIN admission_department d ON d.id = p.department_id
                    WHERE p.search_vector @@ plainto_tsquery('simple', ?)
                    {extra_clause}
                    ORDER BY ts_rank(p.search_vector, plainto_tsquery('simple', ?)) DESC
                    LIMIT ?
                """
            else:
                pct = f"%{query}%"
                pg_params = [pct, pct]
                if university:
                    extra_clause = "AND d.university = ?"
                    pg_params.append(university)
                pg_params.append(limit)
                pg_sql = f"""
                    SELECT r.*, d.university, d.campus, d.year, d.track, d.name as department_name
                    FROM admission_result r
                    JOIN admission_department d ON d.id = r.department_id
                    WHERE (r.process_name ILIKE ? OR r.content ILIKE ?)
                    {extra_clause}
                    ORDER BY r.result_year DESC
                    LIMIT ?
                """
            with self._conn() as conn:
                rows = conn.execute(pg_sql, pg_params).fetchall()
                results = []
                for r in rows:
                    d = dict(r)
                    d["attributes"] = json.loads(d.get("attributes") or "{}")
                    results.append(d)
                return results

        # SQLite: FTS5 virtual tables
        fts_table = "admission_process_fts" if table == "process" else "admission_result_fts"
        data_table = "admission_process" if table == "process" else "admission_result"

        extra_where = ""
        params: list[Any] = [query]
        if university:
            extra_where = "AND d.university = ?"
            params.append(university)
        params.append(limit)

        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT t.*, d.university, d.campus, d.year, d.track, d.name as department_name,
                       fts.rank
                FROM {fts_table}(?) fts
                JOIN {data_table} t ON t.id = fts.rowid
                JOIN admission_department d ON d.id = t.department_id
                WHERE 1=1 {extra_where}
                ORDER BY fts.rank
                LIMIT ?
            """, params).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                d["attributes"] = json.loads(d.get("attributes") or "{}")
                results.append(d)
            return results

    # ── Bulk ───────────────────────────────────────────────────

    def bulk_import(self, records: list[dict]) -> dict:
        """Import a list of records. Each record should have:
        - department: {year, university, campus?, track?, name}
        - process_name, process_type?, admission_type?, quota?, content?, attributes?

        Returns: {"departments": count, "processes": count}
        """
        dept_count, proc_count = 0, 0
        for rec in records:
            dept = rec.get("department", {})
            dept_id = self.upsert_department(
                year=dept["year"],
                university=dept["university"],
                campus=dept.get("campus"),
                track=dept.get("track"),
                name=dept["name"],
            )
            dept_count += 1

            if "process_name" in rec:
                self.upsert_process(
                    department_id=dept_id,
                    process_name=rec["process_name"],
                    process_type=rec.get("process_type"),
                    admission_type=rec.get("admission_type"),
                    quota=rec.get("quota"),
                    content=rec.get("content"),
                    attributes=rec.get("attributes"),
                )
                proc_count += 1

        return {"departments": dept_count, "processes": proc_count}

    # ── Stats ──────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._conn() as conn:
            s = {}
            s["departments"] = conn.execute(
                "SELECT COUNT(*) FROM admission_department"
            ).fetchone()[0]
            s["processes"] = conn.execute(
                "SELECT COUNT(*) FROM admission_process"
            ).fetchone()[0]
            s["results"] = conn.execute(
                "SELECT COUNT(*) FROM admission_result"
            ).fetchone()[0]
            s["result_years"] = [r[0] for r in conn.execute(
                "SELECT DISTINCT result_year FROM admission_result ORDER BY result_year"
            ).fetchall()]
            s["result_score_types"] = {r[0]: r[1] for r in conn.execute(
                "SELECT score_type, COUNT(*) FROM admission_result GROUP BY score_type ORDER BY COUNT(*) DESC"
            ).fetchall()}
            s["universities"] = conn.execute(
                "SELECT COUNT(DISTINCT university) FROM admission_department"
            ).fetchone()[0]
            s["years"] = [r[0] for r in conn.execute(
                "SELECT DISTINCT year FROM admission_department ORDER BY year"
            ).fetchall()]

            # Process types breakdown
            s["process_types"] = {r[0]: r[1] for r in conn.execute(
                "SELECT process_type, COUNT(*) FROM admission_process GROUP BY process_type ORDER BY COUNT(*) DESC"
            ).fetchall()}

            return s
