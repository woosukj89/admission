"""Analytics store — SQLite locally, PostgreSQL on cloud (via DATABASE_URL)."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from .db_factory import get_conn, is_postgres

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "users.db"


class AnalyticsStore:
    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        if is_postgres:
            return  # Tables created by migration/migrate_to_postgres.py
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id            TEXT PRIMARY KEY,
                    user_email    TEXT,
                    anon_id       TEXT,
                    started_at    TEXT NOT NULL,
                    last_active   TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id              TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    tool_calls      TEXT,
                    created_at      TEXT NOT NULL
                )
            """)
            conn.execute("""
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    stat_date            TEXT PRIMARY KEY,
                    unique_users         INTEGER DEFAULT 0,
                    anonymous_users      INTEGER DEFAULT 0,
                    total_questions      INTEGER DEFAULT 0,
                    new_signups          INTEGER DEFAULT 0,
                    signups_with_profile INTEGER DEFAULT 0
                )
            """)
            # Indexes for common queries
            for ddl in [
                "CREATE INDEX IF NOT EXISTS idx_conv_email ON conversations(user_email)",
                "CREATE INDEX IF NOT EXISTS idx_conv_anon ON conversations(anon_id)",
                "CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id)",
                "CREATE INDEX IF NOT EXISTS idx_api_email ON api_calls(user_email)",
                "CREATE INDEX IF NOT EXISTS idx_api_created ON api_calls(created_at)",
            ]:
                conn.execute(ddl)

    @contextmanager
    def _conn(self) -> Generator:
        with get_conn(db_path=self.db_path) as conn:
            yield conn

    def get_or_create_conversation(
        self, user_email: str | None, anon_id: str | None
    ) -> str:
        """Return existing open conversation id or create a new one."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            if user_email:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE user_email = ? ORDER BY last_active DESC LIMIT 1",
                    (user_email,),
                ).fetchone()
            elif anon_id:
                row = conn.execute(
                    "SELECT id FROM conversations WHERE anon_id = ? ORDER BY last_active DESC LIMIT 1",
                    (anon_id,),
                ).fetchone()
            else:
                row = None

            if row:
                return row["id"]

            conv_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO conversations (id, user_email, anon_id, started_at, last_active) VALUES (?, ?, ?, ?, ?)",
                (conv_id, user_email, anon_id, now, now),
            )
            return conv_id

    def log_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_calls: list[str] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO messages (id, conversation_id, role, content, tool_calls, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    conversation_id,
                    role,
                    content,
                    json.dumps(tool_calls, ensure_ascii=False) if tool_calls else None,
                    now,
                ),
            )
            conn.execute(
                "UPDATE conversations SET last_active = ?, message_count = message_count + 1 WHERE id = ?",
                (now, conversation_id),
            )

    def log_api_call(
        self,
        endpoint: str,
        method: str,
        user_email: str | None,
        anon_id: str | None,
        status_code: int,
        duration_ms: int,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_calls (id, endpoint, method, user_email, anon_id, status_code, duration_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), endpoint, method, user_email, anon_id, status_code, duration_ms, now),
            )

    def get_overview(self) -> dict:
        with self._conn() as conn:
            unique_logged = conn.execute(
                "SELECT COUNT(DISTINCT user_email) FROM conversations WHERE user_email IS NOT NULL AND user_email != ''"
            ).fetchone()[0]
            unique_anon = conn.execute(
                "SELECT COUNT(DISTINCT anon_id) FROM conversations WHERE (user_email IS NULL OR user_email = '') AND anon_id IS NOT NULL"
            ).fetchone()[0]
            total_questions = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE role = 'user'"
            ).fetchone()[0]
            total_signups = conn.execute(
                "SELECT COUNT(*) FROM users WHERE email_verified = 1 OR auth_method = 'google'"
            ).fetchone()
            signups = total_signups[0] if total_signups else 0
        return {
            "unique_logged_in_users": unique_logged,
            "unique_anonymous_users": unique_anon,
            "total_questions_asked": total_questions,
            "total_signups": signups,
        }

    def get_daily_questions(self, days: int = 7) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT DATE(created_at) as day, COUNT(*) as count
                FROM messages WHERE role = 'user'
                GROUP BY day ORDER BY day DESC LIMIT ?
            """, (days,)).fetchall()
        return [dict(r) for r in rows]


_instance: AnalyticsStore | None = None


def get_analytics_store() -> AnalyticsStore:
    global _instance
    if _instance is None:
        _instance = AnalyticsStore()
    return _instance
