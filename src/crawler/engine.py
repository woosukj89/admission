"""Main crawler orchestrator"""

import asyncio
from collections import deque
from datetime import datetime
from typing import Optional, Callable
from urllib.parse import urlparse

from ..config import settings
from ..models import (
    University,
    CrawledPage,
    CrawledDocument,
    CrawlState,
    CrawlStatus,
    CrawlStats,
)
from ..extractors.pdf_extractor import PDFExtractor
from ..extractors.doc_extractor import DocumentExtractor
from ..extractors.archive_extractor import ArchiveExtractor
from ..storage.file_storage import FileStorage
from ..storage.json_storage import JSONStorage
from ..storage.sqlite_storage import SQLiteStorage
from ..utils.logging import get_logger
from .filters import AdmissionFilter
from .page_crawler import PageCrawler
from .rate_limiter import RateLimiter
from .robots_parser import RobotsParser

logger = get_logger("crawler.engine")


class CrawlerEngine:
    """Orchestrate crawling across universities"""

    def __init__(
        self,
        max_workers: int = None,
        max_depth: int = None,
        rate_limit: float = None,
    ):
        self.max_workers = max_workers or settings.max_workers
        self.max_depth = max_depth or settings.max_depth
        self.rate_limit = rate_limit or settings.rate_limit

        # Components
        self.robots_parser = RobotsParser()
        self.rate_limiter = RateLimiter(default_delay=self.rate_limit)
        self.admission_filter = AdmissionFilter()
        self.page_crawler = PageCrawler(
            robots_parser=self.robots_parser,
            rate_limiter=self.rate_limiter,
            admission_filter=self.admission_filter,
        )

        # Extractors
        self.pdf_extractor = PDFExtractor()
        self.doc_extractor = DocumentExtractor()
        self.archive_extractor = ArchiveExtractor()

        # Storage
        self.file_storage = FileStorage()
        self.json_storage = JSONStorage()
        self.sqlite_storage = SQLiteStorage()

        # State
        self._running = False
        self._stats: dict[str, CrawlStats] = {}

    async def crawl_university(
        self,
        university: University,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> CrawlStats:
        """Crawl a single university"""
        self._running = True
        stats = CrawlStats(university=university.name)
        self._stats[university.name] = stats

        logger.info(f"Starting crawl for {university.name}")

        # Build list of starting URLs (admission URL + main URL with admission paths)
        start_urls = []

        # Add admission URL if available
        if university.admission_url:
            url = university.admission_url
            if not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            start_urls.append(url)

        # Add main URL
        main_url = university.url
        if not main_url.startswith(("http://", "https://")):
            main_url = f"https://{main_url}"
        start_urls.append(main_url)

        # Add common admission paths on main domain
        admission_paths = [
            "/admission", "/ipsi", "/iphak", "/입학", "/모집",
            "/undergraduate", "/graduate", "/international"
        ]
        for path in admission_paths:
            start_urls.append(f"{main_url.rstrip('/')}{path}")

        # Use first URL as base for domain matching
        base_url = start_urls[0]

        # Initialize queue with all starting URLs
        queue: deque[tuple[str, int]] = deque()
        seen_urls: set[str] = set()

        for url in start_urls:
            if url not in seen_urls:
                queue.append((url, 0))
                seen_urls.add(url)

        # Check for existing crawl state in database
        pending = self.sqlite_storage.get_pending_urls(university.name)
        for state in pending:
            if state.url not in seen_urls:
                queue.append((state.url, state.depth))
                seen_urls.add(state.url)

        async with self.page_crawler:
            while queue and self._running:
                current_url, depth = queue.popleft()

                # Skip if already crawled
                if self.sqlite_storage.is_url_crawled(current_url):
                    continue

                # Check depth limit
                if depth > self.max_depth:
                    continue

                try:
                    # Update state to in progress
                    self.sqlite_storage.update_crawl_state(
                        current_url, CrawlStatus.IN_PROGRESS
                    )

                    # Check if it's a document
                    if self.admission_filter.is_document_url(current_url):
                        doc = await self._crawl_document(
                            current_url, university.name
                        )
                        if doc:
                            stats.documents_downloaded += 1
                    else:
                        page = await self.page_crawler.crawl_page(
                            current_url,
                            university.name,
                            depth=depth,
                            base_url=base_url,
                        )

                        if page:
                            stats.pages_crawled += 1

                            if page.is_admission_related:
                                stats.admission_pages_found += 1

                            # Save to all storage backends
                            self._save_page(page)

                            # Add new links to queue
                            for link in page.links:
                                if link not in seen_urls:
                                    # Prioritize admission-related URLs
                                    priority = self.admission_filter.prioritize_url(link)
                                    if priority > 0 or depth < 2:  # Always explore first 2 levels
                                        seen_urls.add(link)
                                        if priority > 50:
                                            queue.appendleft((link, depth + 1))
                                        else:
                                            queue.append((link, depth + 1))

                                        # Add to database queue
                                        self.sqlite_storage.add_to_queue(CrawlState(
                                            url=link,
                                            university=university.name,
                                            depth=depth + 1,
                                        ))

                    # Mark as completed
                    self.sqlite_storage.update_crawl_state(
                        current_url, CrawlStatus.COMPLETED
                    )

                except Exception as e:
                    logger.error(f"Error crawling {current_url}: {e}")
                    stats.errors += 1
                    self.sqlite_storage.update_crawl_state(
                        current_url, CrawlStatus.FAILED, str(e)
                    )

                # Progress callback
                if progress_callback:
                    progress_callback(
                        university.name,
                        stats.pages_crawled + stats.documents_downloaded,
                        len(queue),
                    )

        stats.end_time = datetime.now()
        logger.info(
            f"Completed crawl for {university.name}: "
            f"{stats.pages_crawled} pages, {stats.documents_downloaded} documents, "
            f"{stats.admission_pages_found} admission-related, {stats.errors} errors"
        )

        return stats

    async def _crawl_document(self, url: str, university: str) -> Optional[CrawledDocument]:
        """Download and process a document"""
        from urllib.parse import unquote
        from pathlib import Path
        from ..models import ContentType

        # Generate save path
        parsed = urlparse(url)
        filename = unquote(parsed.path.split("/")[-1])
        save_path = self.file_storage._get_university_dir(university) / "documents" / filename

        doc = await self.page_crawler.download_document(url, university, str(save_path))

        if doc:
            # Handle archive files
            if doc.file_type in (ContentType.ZIP, ContentType.RAR, ContentType.SEVENZ,
                                 ContentType.ALZ, ContentType.EGG):
                await self._process_archive(doc, university)
                return doc

            # Extract text based on file type
            extracted_text = None
            if doc.file_path:
                if doc.file_type == ContentType.PDF:
                    extracted_text = self.pdf_extractor.extract_text(doc.file_path)
                elif doc.file_type in (ContentType.DOC, ContentType.DOCX,
                                       ContentType.HWP, ContentType.HWPX):
                    result = self.doc_extractor.extract(doc.file_path)
                    extracted_text = result.get("text", "")

                if extracted_text:
                    doc.extracted_text = extracted_text

                    # Re-check admission relevance with extracted text
                    is_admission, score = self.admission_filter.is_admission_related(
                        url=url,
                        text=extracted_text,
                        title=doc.filename,
                    )
                    doc.is_admission_related = is_admission
                    doc.admission_score = score

            # Save to storage
            self.sqlite_storage.save_document(doc)
            self.json_storage.save_document(doc)

        return doc

    async def _process_archive(self, archive_doc: CrawledDocument, university: str) -> None:
        """Process an archive file and extract its contents."""
        from pathlib import Path
        from ..models import ContentType

        if not archive_doc.file_path:
            return

        archive_path = Path(archive_doc.file_path)
        extract_dir = archive_path.parent / archive_path.stem

        # Extract archive
        admission_extensions = ['.pdf', '.hwp', '.hwpx', '.doc', '.docx', '.xls', '.xlsx']
        extracted_files = self.archive_extractor.extract_and_filter(
            archive_path,
            extensions=admission_extensions,
            extract_to=extract_dir,
        )

        logger.info(f"Extracted {len(extracted_files)} files from {archive_doc.filename}")

        # Process each extracted file
        for file_path in extracted_files:
            try:
                ext = file_path.suffix.lower().lstrip('.')
                content_type_map = {
                    "pdf": ContentType.PDF,
                    "doc": ContentType.DOC,
                    "docx": ContentType.DOCX,
                    "hwp": ContentType.HWP,
                    "hwpx": ContentType.HWPX,
                    "xls": ContentType.XLS,
                    "xlsx": ContentType.XLSX,
                }
                file_type = content_type_map.get(ext, ContentType.UNKNOWN)

                # Create document record
                extracted_doc = CrawledDocument(
                    url=f"{archive_doc.url}#{file_path.name}",
                    university=university,
                    filename=file_path.name,
                    file_type=file_type,
                    file_path=str(file_path),
                    file_size=file_path.stat().st_size,
                )

                # Extract text
                extracted_text = None
                if file_type == ContentType.PDF:
                    extracted_text = self.pdf_extractor.extract_text(str(file_path))
                elif file_type in (ContentType.DOC, ContentType.DOCX,
                                   ContentType.HWP, ContentType.HWPX):
                    result = self.doc_extractor.extract(str(file_path))
                    extracted_text = result.get("text", "")

                if extracted_text:
                    extracted_doc.extracted_text = extracted_text

                # Check admission relevance
                is_admission, score = self.admission_filter.is_admission_related(
                    url=extracted_doc.url,
                    text=extracted_text or file_path.name,
                    title=file_path.name,
                )
                extracted_doc.is_admission_related = is_admission
                extracted_doc.admission_score = score

                # Save to storage
                self.sqlite_storage.save_document(extracted_doc)
                self.json_storage.save_document(extracted_doc)

                logger.debug(f"Processed extracted file: {file_path.name} (admission={is_admission})")

            except Exception as e:
                logger.error(f"Error processing extracted file {file_path}: {e}")

    def _save_page(self, page: CrawledPage) -> None:
        """Save page to all storage backends"""
        # SQLite
        self.sqlite_storage.save_page(page)

        # JSON
        self.json_storage.save_page(page)

        # File storage (only for admission-related pages to save space)
        if page.is_admission_related and page.html:
            self.file_storage.save_html(
                page.university,
                page.url,
                page.html,
                metadata={
                    "title": page.title,
                    "admission_score": page.admission_score,
                    "depth": page.depth,
                },
            )

    async def crawl_all(
        self,
        universities: list[University],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> dict[str, CrawlStats]:
        """Crawl multiple universities concurrently"""
        self._running = True
        all_stats = {}

        # Create semaphore for limiting concurrent university crawls
        semaphore = asyncio.Semaphore(self.max_workers)

        async def crawl_with_semaphore(uni: University):
            async with semaphore:
                return await self.crawl_university(uni, progress_callback)

        # Run crawls concurrently
        tasks = [crawl_with_semaphore(uni) for uni in universities]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for uni, result in zip(universities, results):
            if isinstance(result, Exception):
                logger.error(f"Failed to crawl {uni.name}: {result}")
                all_stats[uni.name] = CrawlStats(
                    university=uni.name,
                    errors=1,
                    end_time=datetime.now(),
                )
            else:
                all_stats[uni.name] = result

        return all_stats

    def stop(self) -> None:
        """Stop the crawler"""
        self._running = False
        logger.info("Crawler stop requested")

    async def resume(
        self,
        university: Optional[str] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> dict[str, CrawlStats]:
        """Resume crawling from saved state"""
        # Reset failed URLs
        reset_count = self.sqlite_storage.reset_failed_urls(university)
        if reset_count:
            logger.info(f"Reset {reset_count} failed URLs for retry")

        # Get pending URLs grouped by university
        if university:
            pending = self.sqlite_storage.get_pending_urls(university, limit=1000)
            universities_to_crawl = {university}
        else:
            pending = self.sqlite_storage.get_pending_urls(limit=1000)
            universities_to_crawl = {state.university for state in pending}

        if not universities_to_crawl:
            logger.info("No pending URLs to crawl")
            return {}

        # Create University objects for each
        from ..universities.fetcher import UniversityFetcher
        fetcher = UniversityFetcher()
        unis = []
        for uni_name in universities_to_crawl:
            uni = fetcher.find_by_name(uni_name)
            if uni:
                unis.append(uni)
            else:
                # Create minimal University object
                unis.append(University(name=uni_name, url=""))

        return await self.crawl_all(unis, progress_callback)

    def get_stats(self) -> dict:
        """Get current crawl statistics"""
        return {
            "crawler_stats": {name: stats.model_dump() for name, stats in self._stats.items()},
            "storage_stats": {
                "file": self.file_storage.get_stats(),
                "json": self.json_storage.get_stats(),
                "sqlite": self.sqlite_storage.get_stats(),
            },
        }
