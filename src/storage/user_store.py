"""User store — SQLite locally, PostgreSQL on cloud (via DATABASE_URL)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from .db_factory import get_conn, is_postgres

_DB_PATH = Path(__file__).parent.parent.parent / "data" / "users.db"


class UserStore:
    def __init__(self, db_path: Path = _DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        if is_postgres:
            return  # Tables created by migration/migrate_to_postgres.py
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    email        TEXT PRIMARY KEY,
                    google_sub   TEXT,
                    name         TEXT,
                    picture      TEXT,
                    tier         TEXT NOT NULL DEFAULT 'free',
                    billing_key  TEXT,
                    customer_key TEXT,
                    subscription_end TEXT,
                    revenuecat_id TEXT,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS app_config (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            # Seed default config values
            conn.execute(
                "INSERT OR IGNORE INTO app_config (key, value) VALUES ('daily_free_limit', '5')"
            )
            conn.execute(
                "INSERT OR IGNORE INTO app_config (key, value) VALUES ('daily_paid_limit', '5')"
            )
            conn.execute("""
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
            # Migrate: add columns that may not exist in older DBs
            for col, definition in [
                ("billing_key", "TEXT"),
                ("customer_key", "TEXT"),
                ("revenuecat_id", "TEXT"),
                ("password_hash", "TEXT"),
                ("email_verified", "INTEGER DEFAULT 0"),
                ("verification_code", "TEXT"),
                ("verification_expires", "TEXT"),
                ("auth_method", "TEXT DEFAULT 'google'"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
                except sqlite3.OperationalError:
                    pass

    def get_config(self, key: str, default: str = "") -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM app_config WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else default

    def set_config(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO app_config (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @contextmanager
    def _conn(self) -> Generator:
        with get_conn(db_path=self.db_path) as conn:
            yield conn

    def get_tier(self, email: str) -> str:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT tier, subscription_end FROM users WHERE email = ?", (email,)
            ).fetchone()
        if not row:
            return "free"
        tier = row["tier"]
        sub_end = row["subscription_end"]
        if tier == "paid" and sub_end:
            end_dt = datetime.fromisoformat(sub_end)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > end_dt:
                return "free"
        return tier

    def upsert_user(self, email: str, google_sub: str, name: str, picture: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO users (email, google_sub, name, picture, tier, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'free', ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    google_sub = excluded.google_sub,
                    name = excluded.name,
                    picture = excluded.picture,
                    updated_at = excluded.updated_at
            """, (email, google_sub, name, picture, now, now))

    def set_tier(self, email: str, tier: str, subscription_end: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET tier = ?, subscription_end = ?, updated_at = ?
                WHERE email = ?
            """, (tier, subscription_end, now, email))

    def set_billing_key(self, email: str, billing_key: str, customer_key: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET billing_key = ?, customer_key = ?, updated_at = ?
                WHERE email = ?
            """, (billing_key, customer_key, now, email))

    def get_billing_key(self, email: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT billing_key FROM users WHERE email = ?", (email,)
            ).fetchone()
        return row["billing_key"] if row else None

    def set_revenuecat_id(self, email: str, revenuecat_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET revenuecat_id = ?, updated_at = ?
                WHERE email = ?
            """, (revenuecat_id, now, email))

    def create_email_user(self, email: str, password_hash: str, verification_code: str, verification_expires: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO users (email, google_sub, name, picture, tier, auth_method,
                    password_hash, email_verified, verification_code, verification_expires,
                    created_at, updated_at)
                VALUES (?, '', ?, '', 'free', 'email', ?, 0, ?, ?, ?, ?)
                ON CONFLICT(email) DO NOTHING
            """, (email, email.split("@")[0], password_hash, verification_code, verification_expires, now, now))

    def get_email_user(self, email: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
        return dict(row) if row else None

    def verify_email(self, email: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET email_verified = 1, verification_code = NULL,
                    verification_expires = NULL, updated_at = ?
                WHERE email = ?
            """, (now, email))

    def set_verification_code(self, email: str, code: str, expires: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("""
                UPDATE users SET verification_code = ?, verification_expires = ?, updated_at = ?
                WHERE email = ?
            """, (code, expires, now, email))

    def get_profile(self, email: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM user_profiles WHERE email = ?", (email,)
            ).fetchone()
        if not row:
            return None
        return dict(row)

    def upsert_profile(self, email: str, **fields) -> None:
        allowed = {"gender", "school_name", "school_region", "school_type",
                   "graduation_year", "track", "interests"}
        data = {k: v for k, v in fields.items() if k in allowed}
        now = datetime.now(timezone.utc).isoformat()
        data["email"] = email
        data["updated_at"] = now
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        updates = ", ".join(f"{k} = excluded.{k}" for k in data if k != "email")
        with self._conn() as conn:
            conn.execute(
                f"INSERT INTO user_profiles ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(email) DO UPDATE SET {updates}",
                list(data.values()),
            )


_instance: UserStore | None = None


def get_user_store() -> UserStore:
    global _instance
    if _instance is None:
        _instance = UserStore()
    return _instance
