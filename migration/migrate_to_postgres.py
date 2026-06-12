"""One-time migration: SQLite admission.db + users.db → Supabase PostgreSQL.

Usage:
    DATABASE_URL="postgresql://user:pass@host:5432/db" python migration/migrate_to_postgres.py

The script:
1. Creates all tables in PostgreSQL (admission + users + analytics)
2. Bulk-inserts data from local SQLite files
3. Creates GIN index for full-text search on admission_process
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL environment variable is required.")
    print("  export DATABASE_URL='postgresql://user:pass@host:5432/dbname'")
    sys.exit(1)

import psycopg2
import psycopg2.extras

ADMISSION_DB = ROOT / "data" / "admission.db"
USERS_DB = ROOT / "data" / "users.db"


def pg_conn():
    from urllib.parse import urlparse, unquote
    u = urlparse(DATABASE_URL)
    return psycopg2.connect(
        host=u.hostname,
        port=u.port or 5432,
        user=unquote(u.username),
        password=unquote(u.password),
        dbname=u.path.lstrip("/"),
        sslmode="require",
        options="-c default_transaction_read_only=off",
    )


def create_tables(cur):
    print("Creating tables...")
    # Column names match admission_store.py SQLite schema for query compatibility
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admission_department (
            id        SERIAL PRIMARY KEY,
            year      INTEGER NOT NULL,
            university TEXT NOT NULL,
            campus    TEXT DEFAULT '',
            track     TEXT DEFAULT '',
            name      TEXT NOT NULL,
            attributes TEXT DEFAULT '{}'
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_dept
        ON admission_department (year, university, campus, name)
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admission_process (
            id            SERIAL PRIMARY KEY,
            department_id INTEGER NOT NULL REFERENCES admission_department(id),
            process_name  TEXT NOT NULL,
            process_type  TEXT,
            admission_type TEXT,
            quota         INTEGER,
            content       TEXT,
            attributes    TEXT DEFAULT '{}',
            search_vector TSVECTOR
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_proc
        ON admission_process (department_id, process_name)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_proc_fts ON admission_process USING GIN(search_vector)
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admission_result (
            id               SERIAL PRIMARY KEY,
            department_id    INTEGER NOT NULL REFERENCES admission_department(id),
            result_year      INTEGER NOT NULL,
            process_name     TEXT NOT NULL,
            process_id       INTEGER,
            admission_type   TEXT,
            score_type       TEXT,
            grade_type       TEXT,
            competition_rate REAL,
            average_score    REAL,
            cut_50 REAL, cut_60 REAL, cut_70 REAL,
            cut_80 REAL, cut_85 REAL, cut_90 REAL,
            content TEXT, attributes TEXT DEFAULT '{}'
        )
    """)
    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_result
        ON admission_result (department_id, result_year, process_name)
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email            TEXT PRIMARY KEY,
            google_sub       TEXT,
            name             TEXT,
            picture          TEXT,
            tier             TEXT NOT NULL DEFAULT 'free',
            billing_key      TEXT,
            customer_key     TEXT,
            subscription_end TEXT,
            revenuecat_id    TEXT,
            password_hash    TEXT,
            email_verified   INTEGER DEFAULT 0,
            verification_code TEXT,
            verification_expires TEXT,
            auth_method      TEXT DEFAULT 'google',
            created_at       TEXT NOT NULL,
            updated_at       TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            email           TEXT PRIMARY KEY REFERENCES users(email),
            gender          TEXT,
            school_name     TEXT,
            school_region   TEXT,
            school_type     TEXT,
            graduation_year INTEGER,
            track           TEXT,
            interests       TEXT,
            updated_at      TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    cur.execute("INSERT INTO app_config (key, value) VALUES ('daily_free_limit', '5') ON CONFLICT DO NOTHING")
    cur.execute("INSERT INTO app_config (key, value) VALUES ('daily_paid_limit', '5') ON CONFLICT DO NOTHING")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id            TEXT PRIMARY KEY,
            user_email    TEXT,
            anon_id       TEXT,
            started_at    TEXT NOT NULL,
            last_active   TEXT NOT NULL,
            message_count INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            tool_calls      TEXT,
            created_at      TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS api_calls (
            id          TEXT PRIMARY KEY,
            endpoint    TEXT NOT NULL,
            method      TEXT NOT NULL,
            user_email  TEXT,
            anon_id     TEXT,
            status_code INTEGER,
            duration_ms INTEGER,
            created_at  TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS survey_responses (
            id          TEXT PRIMARY KEY,
            user_email  TEXT,
            anon_id     TEXT,
            rating      INTEGER,
            improvement TEXT,
            other       TEXT,
            created_at  TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_survey_created ON survey_responses(created_at)")
    print("  Tables created.")


def migrate_admission(cur):
    if not ADMISSION_DB.exists():
        print(f"  WARNING: {ADMISSION_DB} not found, skipping admission data.")
        return
    src = sqlite3.connect(str(ADMISSION_DB))
    src.row_factory = lambda c, r: {d[0]: r[i] for i, d in enumerate(c.description)}

    print("Migrating admission_department...")
    rows = src.execute("SELECT * FROM admission_department").fetchall()
    if rows:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO admission_department
                (id, year, university, campus, track, name, attributes)
            VALUES %s ON CONFLICT DO NOTHING
        """, [
            (r["id"], r["year"], r.get("university") or "",
             r.get("campus") or "", r.get("track") or "", r.get("name") or "",
             r.get("attributes") or "{}") for r in rows
        ])
        max_id = max(r["id"] for r in rows)
        cur.execute(f"SELECT setval(pg_get_serial_sequence('admission_department','id'), {max_id})")
    print(f"  {len(rows)} departments migrated.")

    print("Migrating admission_process...")
    procs = src.execute("SELECT * FROM admission_process").fetchall()
    if procs:
        # Build search_vector inline during insert to avoid a slow bulk UPDATE
        psycopg2.extras.execute_values(cur, """
            INSERT INTO admission_process
                (id, department_id, process_name, process_type, admission_type, quota, content, attributes, search_vector)
            VALUES %s ON CONFLICT DO NOTHING
        """, [
            (p["id"], p["department_id"],
             p["process_name"], p.get("process_type"), p.get("admission_type"),
             p.get("quota"), p.get("content"), p.get("attributes") or "{}",
             (p.get("process_name") or "") + " " + (p.get("content") or "")) for p in procs
        ], template="(%s,%s,%s,%s,%s,%s,%s,%s,to_tsvector('simple',%s))")
        max_id = max(p["id"] for p in procs)
        cur.execute(f"SELECT setval(pg_get_serial_sequence('admission_process','id'), {max_id})")
    print(f"  {len(procs)} processes migrated.")

    print("Migrating admission_result...")
    results = src.execute("SELECT * FROM admission_result").fetchall()
    if results:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO admission_result
                (id, department_id, result_year, process_name, process_id, admission_type,
                 score_type, grade_type, competition_rate, average_score,
                 cut_50, cut_60, cut_70, cut_80, cut_85, cut_90, content, attributes)
            VALUES %s ON CONFLICT DO NOTHING
        """, [
            (r["id"], r["department_id"],
             r["result_year"], r["process_name"], r.get("process_id"),
             r.get("admission_type"), r.get("score_type"), r.get("grade_type"),
             r.get("competition_rate"), r.get("average_score"),
             r.get("cut_50"), r.get("cut_60"), r.get("cut_70"),
             r.get("cut_80"), r.get("cut_85"), r.get("cut_90"),
             r.get("content"), r.get("attributes") or "{}") for r in results
        ])
        max_id = max(r["id"] for r in results)
        cur.execute(f"SELECT setval(pg_get_serial_sequence('admission_result','id'), {max_id})")
    print(f"  {len(results)} results migrated.")
    src.close()


def migrate_users(cur):
    if not USERS_DB.exists():
        print(f"  WARNING: {USERS_DB} not found, skipping users.")
        return
    src = sqlite3.connect(str(USERS_DB))
    src.row_factory = lambda c, r: {d[0]: r[i] for i, d in enumerate(c.description)}

    print("Migrating users...")
    rows = src.execute("SELECT * FROM users").fetchall()
    if rows:
        psycopg2.extras.execute_values(cur, """
            INSERT INTO users (email, google_sub, name, picture, tier, created_at, updated_at)
            VALUES %s ON CONFLICT DO NOTHING
        """, [(r["email"], r.get("google_sub",""), r.get("name",""),
               r.get("picture",""), r.get("tier","free"),
               r.get("created_at",""), r.get("updated_at","")) for r in rows])
    print(f"  {len(rows)} users migrated.")
    src.close()


def main():
    print(f"Connecting to PostgreSQL at {DATABASE_URL[:40]}...")
    conn = pg_conn()
    cur = conn.cursor()
    try:
        create_tables(cur)
        conn.commit()
        migrate_admission(cur)
        conn.commit()
        migrate_users(cur)
        conn.commit()
        print("\nMigration complete!")
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
