"""DB connection factory — SQLite locally, PostgreSQL on cloud (via DATABASE_URL).

Usage:
    from src.storage.db_factory import get_conn, PLACEHOLDER, is_postgres

    with get_conn() as conn:
        conn.execute("SELECT * FROM users WHERE email = ?", (email,))
        # ? is auto-converted to %s for PostgreSQL
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DATABASE_URL = os.environ.get("DATABASE_URL", "")
is_postgres = DATABASE_URL.startswith("postgres")

PLACEHOLDER = "%s" if is_postgres else "?"

_DEFAULT_DB = Path(__file__).parent.parent.parent / "data" / "users.db"


class _PGRow:
    """PostgreSQL row compatible with sqlite3.Row: supports row[0], row["col"], dict(row)."""

    def __init__(self, data: dict):
        self._d = dict(data)
        self._vals = list(self._d.values())

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        return self._d[key]

    def get(self, key, default=None):
        return self._d.get(key, default)

    def keys(self):
        return self._d.keys()

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __contains__(self, key):
        return key in self._d


class _PGCursorWrapper:
    """Wraps psycopg2 RealDictCursor, converting ? → %s and rows to _PGRow."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql: str, params=()):
        self._cur.execute(sql.replace("?", "%s"), params or ())
        return self

    def executemany(self, sql: str, seq):
        self._cur.executemany(sql.replace("?", "%s"), seq)

    def fetchone(self):
        row = self._cur.fetchone()
        return _PGRow(row) if row is not None else None

    def fetchall(self):
        return [_PGRow(r) for r in (self._cur.fetchall() or [])]

    def __iter__(self):
        for r in self._cur:
            yield _PGRow(r)


class DBConn:
    """Unified connection for SQLite and PostgreSQL.

    Provides a sqlite3-compatible interface so all stores can use ? placeholders
    and dict(row) / row[0] / row["col"] access patterns regardless of backend.
    """

    def __init__(self, raw_conn, is_pg: bool):
        self._raw = raw_conn
        self._is_pg = is_pg
        if is_pg:
            import psycopg2.extras
            self._pg = _PGCursorWrapper(
                raw_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            )

    def execute(self, sql: str, params=()):
        if self._is_pg:
            return self._pg.execute(sql, params)
        return self._raw.execute(sql, params)

    def executemany(self, sql: str, seq):
        if self._is_pg:
            self._pg.executemany(sql, seq)
        else:
            self._raw.executemany(sql, seq)

    def executescript(self, sql: str):
        """Run multiple SQL statements. No-op for PostgreSQL (use migration script)."""
        if not self._is_pg:
            self._raw.executescript(sql)


@contextmanager
def get_conn(db_path: str | Path | None = None) -> Generator[DBConn, None, None]:
    """Yield a unified DB connection; commit on success, rollback on error."""
    if is_postgres:
        import psycopg2
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
        _db = Path(db_path) if db_path else _DEFAULT_DB
        raw = sqlite3.connect(str(_db))
        raw.row_factory = sqlite3.Row
        conn = DBConn(raw, is_pg=False)
        try:
            yield conn
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()
