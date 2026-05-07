"""SQLite storage for searchable index"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, Generator

from ..config import settings
from ..models import CrawledPage, CrawledDocument, CrawlStatus, CrawlState
from ..utils.logging import get_logger

logger = get_logger("storage.sqlite")


class SQLiteStorage:
    """SQLite storage for crawl state and searchable index"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema"""
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    university TEXT NOT NULL,
                    title TEXT,
                    content TEXT,
                    content_type TEXT DEFAULT 'html',
                    is_admission_related BOOLEAN DEFAULT 0,
                    admission_score REAL DEFAULT 0.0,
                    depth INTEGER DEFAULT 0,
                    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(url)
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    university TEXT NOT NULL,
                    filename TEXT,
                    file_type TEXT,
                    extracted_text TEXT,
                    file_path TEXT,
                    file_size INTEGER DEFAULT 0,
                    is_admission_related BOOLEAN DEFAULT 0,
                    admission_score REAL DEFAULT 0.0,
                    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(url)
                );

                CREATE TABLE IF NOT EXISTS crawl_state (
                    url TEXT PRIMARY KEY,
                    university TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    depth INTEGER DEFAULT 0,
                    retries INTEGER DEFAULT 0,
                    error TEXT,
                    last_attempt TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS crawl_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    university TEXT NOT NULL,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    pages_crawled INTEGER DEFAULT 0,
                    documents_downloaded INTEGER DEFAULT 0,
                    admission_pages_found INTEGER DEFAULT 0,
                    errors INTEGER DEFAULT 0
                );

                -- Indexes for faster queries
                CREATE INDEX IF NOT EXISTS idx_pages_university ON pages(university);
                CREATE INDEX IF NOT EXISTS idx_pages_admission ON pages(is_admission_related);
                CREATE INDEX IF NOT EXISTS idx_pages_score ON pages(admission_score DESC);
                CREATE INDEX IF NOT EXISTS idx_documents_university ON documents(university);
                CREATE INDEX IF NOT EXISTS idx_crawl_state_status ON crawl_state(status);
                CREATE INDEX IF NOT EXISTS idx_crawl_state_university ON crawl_state(university);
            """)

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get database connection with row factory"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # Page operations
    def save_page(self, page: CrawledPage) -> int:
        """Save or update a crawled page"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO pages (url, university, title, content, content_type,
                                   is_admission_related, admission_score, depth, crawled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    is_admission_related = excluded.is_admission_related,
                    admission_score = excluded.admission_score,
                    crawled_at = excluded.crawled_at
            """, (
                page.url,
                page.university,
                page.title,
                page.content,
                page.content_type.value,
                page.is_admission_related,
                page.admission_score,
                page.depth,
                page.crawled_at.isoformat(),
            ))
            return cursor.lastrowid

    def get_page(self, url: str) -> Optional[dict]:
        """Get a page by URL"""
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT * FROM pages WHERE url = ?", (url,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def search_pages(
        self,
        query: str,
        university: Optional[str] = None,
        admission_only: bool = False,
        limit: int = 100,
    ) -> list[dict]:
        """Search pages by content"""
        sql = "SELECT * FROM pages WHERE content LIKE ?"
        params = [f"%{query}%"]

        if university:
            sql += " AND university = ?"
            params.append(university)

        if admission_only:
            sql += " AND is_admission_related = 1"

        sql += " ORDER BY admission_score DESC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    # Document operations
    def save_document(self, doc: CrawledDocument) -> int:
        """Save or update a document"""
        with self._get_connection() as conn:
            cursor = conn.execute("""
                INSERT INTO documents (url, university, filename, file_type,
                                       extracted_text, file_path, file_size,
                                       is_admission_related, admission_score, crawled_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    extracted_text = excluded.extracted_text,
                    file_path = excluded.file_path,
                    is_admission_related = excluded.is_admission_related,
                    admission_score = excluded.admission_score,
                    crawled_at = excluded.crawled_at
            """, (
                doc.url,
                doc.university,
                doc.filename,
                doc.file_type.value,
                doc.extracted_text,
                doc.file_path,
                doc.file_size,
                doc.is_admission_related,
                doc.admission_score,
                doc.crawled_at.isoformat(),
            ))
            return cursor.lastrowid

    # Crawl state operations
    def add_to_queue(self, state: CrawlState) -> None:
        """Add URL to crawl queue"""
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO crawl_state (url, university, status, depth)
                VALUES (?, ?, ?, ?)
            """, (state.url, state.university, state.status.value, state.depth))

    def add_urls_to_queue(self, urls: list[str], university: str, depth: int = 0) -> int:
        """Add multiple URLs to queue"""
        with self._get_connection() as conn:
            cursor = conn.executemany("""
                INSERT OR IGNORE INTO crawl_state (url, university, status, depth)
                VALUES (?, ?, 'pending', ?)
            """, [(url, university, depth) for url in urls])
            return cursor.rowcount

    def get_pending_urls(
        self,
        university: Optional[str] = None,
        limit: int = 100,
    ) -> list[CrawlState]:
        """Get pending URLs to crawl"""
        sql = "SELECT * FROM crawl_state WHERE status = 'pending'"
        params = []

        if university:
            sql += " AND university = ?"
            params.append(university)

        sql += " ORDER BY depth ASC, url ASC LIMIT ?"
        params.append(limit)

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [CrawlState(
                url=row["url"],
                university=row["university"],
                status=CrawlStatus(row["status"]),
                depth=row["depth"],
                retries=row["retries"],
                error=row["error"],
            ) for row in cursor.fetchall()]

    def update_crawl_state(
        self,
        url: str,
        status: CrawlStatus,
        error: Optional[str] = None,
    ) -> None:
        """Update crawl state for a URL"""
        with self._get_connection() as conn:
            if status == CrawlStatus.FAILED:
                conn.execute("""
                    UPDATE crawl_state
                    SET status = ?, error = ?, retries = retries + 1, last_attempt = ?
                    WHERE url = ?
                """, (status.value, error, datetime.now().isoformat(), url))
            else:
                conn.execute("""
                    UPDATE crawl_state
                    SET status = ?, last_attempt = ?
                    WHERE url = ?
                """, (status.value, datetime.now().isoformat(), url))

    def is_url_crawled(self, url: str) -> bool:
        """Check if URL has been crawled"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM crawl_state WHERE url = ? AND status = 'completed'",
                (url,)
            )
            return cursor.fetchone() is not None

    def reset_failed_urls(self, university: Optional[str] = None, max_retries: int = 3) -> int:
        """Reset failed URLs for retry"""
        sql = "UPDATE crawl_state SET status = 'pending' WHERE status = 'failed' AND retries < ?"
        params = [max_retries]

        if university:
            sql += " AND university = ?"
            params.append(university)

        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return cursor.rowcount

    # Session operations
    def start_session(self, university: str) -> int:
        """Start a new crawl session"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO crawl_sessions (university) VALUES (?)",
                (university,)
            )
            return cursor.lastrowid

    def end_session(self, session_id: int, stats: dict) -> None:
        """End a crawl session with stats"""
        with self._get_connection() as conn:
            conn.execute("""
                UPDATE crawl_sessions
                SET completed_at = ?,
                    pages_crawled = ?,
                    documents_downloaded = ?,
                    admission_pages_found = ?,
                    errors = ?
                WHERE id = ?
            """, (
                datetime.now().isoformat(),
                stats.get("pages_crawled", 0),
                stats.get("documents_downloaded", 0),
                stats.get("admission_pages_found", 0),
                stats.get("errors", 0),
                session_id,
            ))

    # Statistics
    def get_stats(self) -> dict:
        """Get overall statistics"""
        with self._get_connection() as conn:
            stats = {}

            # Page stats
            cursor = conn.execute("SELECT COUNT(*) FROM pages")
            stats["total_pages"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM pages WHERE is_admission_related = 1")
            stats["admission_pages"] = cursor.fetchone()[0]

            # Document stats
            cursor = conn.execute("SELECT COUNT(*) FROM documents")
            stats["total_documents"] = cursor.fetchone()[0]

            cursor = conn.execute("SELECT COUNT(*) FROM documents WHERE is_admission_related = 1")
            stats["admission_documents"] = cursor.fetchone()[0]

            # Queue stats
            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM crawl_state
                GROUP BY status
            """)
            stats["queue"] = {row["status"]: row["count"] for row in cursor.fetchall()}

            # University count
            cursor = conn.execute("SELECT COUNT(DISTINCT university) FROM pages")
            stats["universities"] = cursor.fetchone()[0]

            return stats

    def get_university_stats(self, university: str) -> dict:
        """Get statistics for a specific university"""
        with self._get_connection() as conn:
            stats = {"university": university}

            cursor = conn.execute(
                "SELECT COUNT(*) FROM pages WHERE university = ?",
                (university,)
            )
            stats["pages"] = cursor.fetchone()[0]

            cursor = conn.execute(
                "SELECT COUNT(*) FROM pages WHERE university = ? AND is_admission_related = 1",
                (university,)
            )
            stats["admission_pages"] = cursor.fetchone()[0]

            cursor = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE university = ?",
                (university,)
            )
            stats["documents"] = cursor.fetchone()[0]

            cursor = conn.execute("""
                SELECT status, COUNT(*) as count
                FROM crawl_state
                WHERE university = ?
                GROUP BY status
            """, (university,))
            stats["queue"] = {row["status"]: row["count"] for row in cursor.fetchall()}

            return stats
