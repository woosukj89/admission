"""JSON-based storage for structured data"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..config import settings
from ..models import CrawledPage, CrawledDocument, University
from ..utils.logging import get_logger
from .file_storage import sanitize_filename

logger = get_logger("storage.json")


class JSONStorage:
    """Store and export data in JSON format"""

    def __init__(self, base_path: Optional[Path] = None):
        self.base_path = base_path or settings.json_dir
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_university_dir(self, university: str) -> Path:
        """Get JSON directory for a university"""
        safe_name = sanitize_filename(university)
        uni_dir = self.base_path / safe_name
        uni_dir.mkdir(parents=True, exist_ok=True)
        return uni_dir

    def _serialize(self, obj) -> dict:
        """Convert object to JSON-serializable dict"""
        if hasattr(obj, "model_dump"):
            data = obj.model_dump()
        elif hasattr(obj, "__dict__"):
            data = obj.__dict__.copy()
        else:
            data = obj

        # Convert datetime objects
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()

        return data

    def save_page(self, page: CrawledPage) -> Path:
        """Save a crawled page to JSON"""
        uni_dir = self._get_university_dir(page.university)
        pages_file = uni_dir / "pages.jsonl"

        # Append to JSONL file
        with open(pages_file, "a", encoding="utf-8") as f:
            data = self._serialize(page)
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

        return pages_file

    def save_document(self, doc: CrawledDocument) -> Path:
        """Save a crawled document metadata to JSON"""
        uni_dir = self._get_university_dir(doc.university)
        docs_file = uni_dir / "documents.jsonl"

        with open(docs_file, "a", encoding="utf-8") as f:
            data = self._serialize(doc)
            f.write(json.dumps(data, ensure_ascii=False) + "\n")

        return docs_file

    def save_pages_batch(self, pages: list[CrawledPage], university: str) -> Path:
        """Save multiple pages at once"""
        uni_dir = self._get_university_dir(university)
        pages_file = uni_dir / "pages.jsonl"

        with open(pages_file, "a", encoding="utf-8") as f:
            for page in pages:
                data = self._serialize(page)
                f.write(json.dumps(data, ensure_ascii=False) + "\n")

        logger.info(f"Saved {len(pages)} pages to {pages_file}")
        return pages_file

    def export_admission_pages(self, university: str) -> Path:
        """Export only admission-related pages to a single JSON file"""
        uni_dir = self._get_university_dir(university)
        pages_file = uni_dir / "pages.jsonl"
        export_file = uni_dir / "admissions.json"

        admission_pages = []

        if pages_file.exists():
            with open(pages_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        page = json.loads(line)
                        if page.get("is_admission_related"):
                            admission_pages.append(page)
                    except json.JSONDecodeError:
                        continue

        # Sort by admission score
        admission_pages.sort(key=lambda p: p.get("admission_score", 0), reverse=True)

        with open(export_file, "w", encoding="utf-8") as f:
            json.dump({
                "university": university,
                "exported_at": datetime.now().isoformat(),
                "total_pages": len(admission_pages),
                "pages": admission_pages,
            }, f, ensure_ascii=False, indent=2)

        logger.info(f"Exported {len(admission_pages)} admission pages to {export_file}")
        return export_file

    def export_all(self, output_dir: Optional[Path] = None) -> Path:
        """Export all data to a consolidated JSON file"""
        output_dir = output_dir or self.base_path
        export_file = output_dir / "all_admissions.json"

        all_data = {
            "exported_at": datetime.now().isoformat(),
            "universities": [],
        }

        for uni_dir in self.base_path.iterdir():
            if not uni_dir.is_dir():
                continue

            university_name = uni_dir.name
            uni_data = {
                "name": university_name,
                "pages": [],
                "documents": [],
            }

            # Load pages
            pages_file = uni_dir / "pages.jsonl"
            if pages_file.exists():
                with open(pages_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            page = json.loads(line)
                            if page.get("is_admission_related"):
                                uni_data["pages"].append(page)
                        except json.JSONDecodeError:
                            continue

            # Load documents
            docs_file = uni_dir / "documents.jsonl"
            if docs_file.exists():
                with open(docs_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            doc = json.loads(line)
                            if doc.get("is_admission_related"):
                                uni_data["documents"].append(doc)
                        except json.JSONDecodeError:
                            continue

            if uni_data["pages"] or uni_data["documents"]:
                all_data["universities"].append(uni_data)

        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)

        logger.info(f"Exported all data to {export_file}")
        return export_file

    def load_pages(self, university: str) -> list[dict]:
        """Load all pages for a university"""
        uni_dir = self._get_university_dir(university)
        pages_file = uni_dir / "pages.jsonl"

        pages = []
        if pages_file.exists():
            with open(pages_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        pages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return pages

    def load_documents(self, university: str) -> list[dict]:
        """Load all documents for a university"""
        uni_dir = self._get_university_dir(university)
        docs_file = uni_dir / "documents.jsonl"

        docs = []
        if docs_file.exists():
            with open(docs_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        docs.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return docs

    def get_stats(self) -> dict:
        """Get storage statistics"""
        stats = {
            "universities": 0,
            "total_pages": 0,
            "admission_pages": 0,
            "total_documents": 0,
            "admission_documents": 0,
        }

        for uni_dir in self.base_path.iterdir():
            if not uni_dir.is_dir():
                continue

            stats["universities"] += 1

            pages_file = uni_dir / "pages.jsonl"
            if pages_file.exists():
                with open(pages_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            page = json.loads(line)
                            stats["total_pages"] += 1
                            if page.get("is_admission_related"):
                                stats["admission_pages"] += 1
                        except json.JSONDecodeError:
                            continue

            docs_file = uni_dir / "documents.jsonl"
            if docs_file.exists():
                with open(docs_file, "r", encoding="utf-8") as f:
                    for line in f:
                        try:
                            doc = json.loads(line)
                            stats["total_documents"] += 1
                            if doc.get("is_admission_related"):
                                stats["admission_documents"] += 1
                        except json.JSONDecodeError:
                            continue

        return stats
