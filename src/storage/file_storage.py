"""File-based storage for raw crawled content"""

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, unquote

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger("storage.file")


def sanitize_filename(name: str, max_length: int = 100) -> str:
    """Sanitize a string to be safe for use as a filename"""
    # Replace problematic characters
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    name = name.strip("._")

    # Truncate if too long
    if len(name) > max_length:
        name = name[:max_length]

    return name or "unnamed"


class FileStorage:
    """Store raw files organized by university"""

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or settings.raw_dir
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_university_dir(self, university: str) -> Path:
        """Get directory for a university"""
        safe_name = sanitize_filename(university)
        uni_dir = self.base_path / safe_name
        uni_dir.mkdir(parents=True, exist_ok=True)
        return uni_dir

    def _url_to_filename(self, url: str, extension: Optional[str] = None) -> str:
        """Convert URL to a safe filename"""
        parsed = urlparse(url)

        # Get path filename
        path = unquote(parsed.path)
        filename = path.split("/")[-1] if "/" in path else path

        if not filename or filename == "/":
            # Use URL hash for index pages
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f"page_{url_hash}"

        # Add extension if needed
        if extension and not filename.lower().endswith(extension.lower()):
            filename = f"{filename}{extension}"

        return sanitize_filename(filename)

    def save_html(
        self,
        university: str,
        url: str,
        html_content: str,
        metadata: Optional[dict] = None,
    ) -> Path:
        """Save HTML content to file"""
        uni_dir = self._get_university_dir(university)
        pages_dir = uni_dir / "pages"
        pages_dir.mkdir(exist_ok=True)

        filename = self._url_to_filename(url, ".html")
        file_path = pages_dir / filename

        # Handle duplicates
        counter = 1
        base_path = file_path
        while file_path.exists():
            stem = base_path.stem
            file_path = pages_dir / f"{stem}_{counter}.html"
            counter += 1

        # Write HTML
        file_path.write_text(html_content, encoding="utf-8")

        # Write metadata sidecar
        if metadata:
            import json
            meta_path = file_path.with_suffix(".json")
            meta_data = {
                "url": url,
                "university": university,
                "saved_at": datetime.now().isoformat(),
                **metadata,
            }
            meta_path.write_text(
                json.dumps(meta_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        logger.debug(f"Saved HTML: {file_path}")
        return file_path

    def save_document(
        self,
        university: str,
        url: str,
        content: bytes,
        metadata: Optional[dict] = None,
    ) -> Path:
        """Save a document (PDF, DOC, etc.) to file"""
        uni_dir = self._get_university_dir(university)
        docs_dir = uni_dir / "documents"
        docs_dir.mkdir(exist_ok=True)

        # Get original filename from URL
        parsed = urlparse(url)
        filename = unquote(parsed.path.split("/")[-1])
        filename = sanitize_filename(filename)

        if not filename:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f"document_{url_hash}"

        file_path = docs_dir / filename

        # Handle duplicates
        counter = 1
        base_name = file_path.stem
        suffix = file_path.suffix
        while file_path.exists():
            file_path = docs_dir / f"{base_name}_{counter}{suffix}"
            counter += 1

        # Write content
        file_path.write_bytes(content)

        # Write metadata sidecar
        if metadata:
            import json
            meta_path = file_path.with_suffix(file_path.suffix + ".json")
            meta_data = {
                "url": url,
                "university": university,
                "filename": filename,
                "file_size": len(content),
                "saved_at": datetime.now().isoformat(),
                **metadata,
            }
            meta_path.write_text(
                json.dumps(meta_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )

        logger.debug(f"Saved document: {file_path}")
        return file_path

    def save_text(
        self,
        university: str,
        url: str,
        text_content: str,
        suffix: str = ".txt",
    ) -> Path:
        """Save extracted text content"""
        uni_dir = self._get_university_dir(university)
        text_dir = uni_dir / "text"
        text_dir.mkdir(exist_ok=True)

        filename = self._url_to_filename(url, suffix)
        file_path = text_dir / filename

        # Handle duplicates
        counter = 1
        base_path = file_path
        while file_path.exists():
            stem = base_path.stem
            file_path = text_dir / f"{stem}_{counter}{suffix}"
            counter += 1

        file_path.write_text(text_content, encoding="utf-8")
        logger.debug(f"Saved text: {file_path}")
        return file_path

    def get_university_files(self, university: str) -> dict[str, list[Path]]:
        """Get all files for a university organized by type"""
        uni_dir = self._get_university_dir(university)

        return {
            "pages": list((uni_dir / "pages").glob("*.html")) if (uni_dir / "pages").exists() else [],
            "documents": list((uni_dir / "documents").glob("*")) if (uni_dir / "documents").exists() else [],
            "text": list((uni_dir / "text").glob("*.txt")) if (uni_dir / "text").exists() else [],
        }

    def file_exists(self, university: str, url: str) -> bool:
        """Check if a file for this URL already exists"""
        uni_dir = self._get_university_dir(university)

        # Check in pages
        filename = self._url_to_filename(url, ".html")
        if (uni_dir / "pages" / filename).exists():
            return True

        # Check in documents
        parsed = urlparse(url)
        doc_filename = sanitize_filename(unquote(parsed.path.split("/")[-1]))
        if (uni_dir / "documents" / doc_filename).exists():
            return True

        return False

    def get_stats(self) -> dict:
        """Get storage statistics"""
        stats = {
            "universities": 0,
            "total_pages": 0,
            "total_documents": 0,
            "total_size_bytes": 0,
        }

        for uni_dir in self.base_path.iterdir():
            if uni_dir.is_dir():
                stats["universities"] += 1

                pages_dir = uni_dir / "pages"
                if pages_dir.exists():
                    pages = list(pages_dir.glob("*.html"))
                    stats["total_pages"] += len(pages)
                    stats["total_size_bytes"] += sum(p.stat().st_size for p in pages)

                docs_dir = uni_dir / "documents"
                if docs_dir.exists():
                    docs = [f for f in docs_dir.iterdir() if not f.suffix == ".json"]
                    stats["total_documents"] += len(docs)
                    stats["total_size_bytes"] += sum(d.stat().st_size for d in docs)

        return stats
