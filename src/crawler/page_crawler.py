"""Single page crawler"""

import asyncio
from typing import Optional
from urllib.parse import urljoin, urlparse, unquote
import re

import httpx

from ..config import settings
from ..models import CrawledPage, CrawledDocument, ContentType
from ..extractors.html_extractor import HTMLExtractor
from ..utils.korean import decode_safely
from ..utils.logging import get_logger
from .filters import AdmissionFilter
from .robots_parser import RobotsParser
from .rate_limiter import RateLimiter

logger = get_logger("crawler.page")


class PageCrawler:
    """Crawl individual pages and extract content"""

    def __init__(
        self,
        robots_parser: Optional[RobotsParser] = None,
        rate_limiter: Optional[RateLimiter] = None,
        admission_filter: Optional[AdmissionFilter] = None,
    ):
        self.robots_parser = robots_parser or RobotsParser()
        self.rate_limiter = rate_limiter or RateLimiter()
        self.admission_filter = admission_filter or AdmissionFilter()
        self.html_extractor = HTMLExtractor()

        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None or self._client.is_closed:
            import ssl
            # Create SSL context that's more lenient for Korean university sites
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(settings.request_timeout),
                follow_redirects=True,
                verify=False,  # Some Korean university sites have certificate issues
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept-Encoding": "gzip, deflate",
                },
                http2=True,
            )
        return self._client

    async def close(self) -> None:
        """Close HTTP client"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _get_base_domain(self, url: str) -> str:
        """Extract base domain (e.g., korea.ac.kr from oku.korea.ac.kr)"""
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        # Korean university domains typically end with .ac.kr
        parts = netloc.split('.')
        if len(parts) >= 3 and parts[-2] == 'ac' and parts[-1] == 'kr':
            # Return last 3 parts (e.g., korea.ac.kr)
            return '.'.join(parts[-3:])
        elif len(parts) >= 2:
            return '.'.join(parts[-2:])
        return netloc

    def _is_same_domain(self, url1: str, url2: str) -> bool:
        """Check if two URLs are from the same base domain (allows subdomains)"""
        return self._get_base_domain(url1) == self._get_base_domain(url2)

    def _normalize_url(self, url: str, base_url: str) -> Optional[str]:
        """Normalize and validate URL"""
        # Handle relative URLs
        if not url.startswith(("http://", "https://")):
            url = urljoin(base_url, url)

        # Parse and reconstruct
        parsed = urlparse(url)

        # Skip non-HTTP URLs
        if parsed.scheme not in ("http", "https"):
            return None

        # Remove fragment
        url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            url += f"?{parsed.query}"

        return url

    async def can_crawl(self, url: str) -> bool:
        """Check if we can crawl a URL"""
        # Check robots.txt
        if not await self.robots_parser.can_fetch(url):
            logger.debug(f"Blocked by robots.txt: {url}")
            return False

        # Check skip patterns
        if self.admission_filter.should_skip_url(url):
            logger.debug(f"Skipped by filter: {url}")
            return False

        return True

    async def crawl_page(
        self,
        url: str,
        university: str,
        depth: int = 0,
        base_url: Optional[str] = None,
    ) -> Optional[CrawledPage]:
        """Crawl a single page and extract content"""
        base_url = base_url or url

        # Check if we can crawl
        if not await self.can_crawl(url):
            return None

        # Apply rate limiting
        await self.rate_limiter.acquire(url)

        try:
            client = await self._get_client()
            response = await client.get(url)

            # Check status
            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} for {url}")
                return None

            # Check content type
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type.lower():
                logger.debug(f"Non-HTML content type for {url}: {content_type}")
                return None

            # Check size
            content_length = len(response.content)
            if content_length > settings.max_page_size:
                logger.warning(f"Page too large ({content_length} bytes): {url}")
                return None

            # Decode content
            html_content = decode_safely(response.content)

            # Extract content
            extracted = self.html_extractor.extract(html_content, url)

            # Normalize links
            normalized_links = []
            for link in extracted.get("links", []):
                normalized = self._normalize_url(link, url)
                if normalized and self._is_same_domain(normalized, base_url):
                    normalized_links.append(normalized)

            # Check admission relevance
            is_admission, score = self.admission_filter.is_admission_related(
                url=url,
                text=extracted.get("text", ""),
                title=extracted.get("title", ""),
            )

            page = CrawledPage(
                url=url,
                university=university,
                title=extracted.get("title"),
                content=extracted.get("text"),
                html=html_content,
                links=normalized_links,
                content_type=ContentType.HTML,
                is_admission_related=is_admission,
                admission_score=score,
                depth=depth,
            )

            logger.info(
                f"Crawled: {url} (admission={is_admission}, score={score:.2f}, links={len(normalized_links)})"
            )

            return page

        except httpx.TimeoutException:
            logger.warning(f"Timeout crawling {url}")
            return None
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error crawling {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error crawling {url}: {e}")
            return None

    async def download_document(
        self,
        url: str,
        university: str,
        save_path: str,
    ) -> Optional[CrawledDocument]:
        """Download a document (PDF, DOC, etc.)"""
        if not await self.can_crawl(url):
            return None

        await self.rate_limiter.acquire(url)

        try:
            client = await self._get_client()
            response = await client.get(url)

            if response.status_code != 200:
                logger.warning(f"HTTP {response.status_code} downloading {url}")
                return None

            content_length = len(response.content)
            if content_length > settings.max_file_size:
                logger.warning(f"File too large ({content_length} bytes): {url}")
                return None

            # Determine file type
            parsed = urlparse(url)
            filename = unquote(parsed.path.split("/")[-1])
            ext = filename.lower().split(".")[-1] if "." in filename else ""

            content_type_map = {
                "pdf": ContentType.PDF,
                "doc": ContentType.DOC,
                "docx": ContentType.DOCX,
                "hwp": ContentType.HWP,
                "hwpx": ContentType.HWPX,
                "xls": ContentType.XLS,
                "xlsx": ContentType.XLSX,
                "ppt": ContentType.PPT,
                "pptx": ContentType.PPTX,
                "zip": ContentType.ZIP,
                "rar": ContentType.RAR,
                "7z": ContentType.SEVENZ,
                "alz": ContentType.ALZ,
                "egg": ContentType.EGG,
            }
            file_type = content_type_map.get(ext, ContentType.UNKNOWN)

            # Save file
            from pathlib import Path
            save_file = Path(save_path)
            save_file.parent.mkdir(parents=True, exist_ok=True)
            save_file.write_bytes(response.content)

            # Check admission relevance from URL/filename
            is_admission, score = self.admission_filter.is_admission_related(
                url=url,
                text=filename,
            )

            doc = CrawledDocument(
                url=url,
                university=university,
                filename=filename,
                file_type=file_type,
                file_path=str(save_file),
                file_size=content_length,
                is_admission_related=is_admission,
                admission_score=score,
            )

            logger.info(f"Downloaded: {filename} ({content_length} bytes)")
            return doc

        except Exception as e:
            logger.error(f"Error downloading {url}: {e}")
            return None

    async def __aenter__(self):
        await self._get_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
