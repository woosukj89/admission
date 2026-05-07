"""Rate limiter for polite crawling"""

import asyncio
import time
from typing import Optional
from urllib.parse import urlparse

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger("crawler.rate_limiter")


class RateLimiter:
    """Per-domain rate limiter for crawling"""

    def __init__(self, default_delay: Optional[float] = None):
        self.default_delay = default_delay or settings.rate_limit
        self._last_request: dict[str, float] = {}
        self._domain_delays: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL"""
        parsed = urlparse(url)
        return parsed.netloc

    def set_domain_delay(self, domain: str, delay: float) -> None:
        """Set a specific delay for a domain (e.g., from robots.txt)"""
        self._domain_delays[domain] = delay
        logger.debug(f"Set delay for {domain}: {delay}s")

    def get_delay(self, url: str) -> float:
        """Get the delay for a URL's domain"""
        domain = self._get_domain(url)
        return self._domain_delays.get(domain, self.default_delay)

    async def acquire(self, url: str) -> None:
        """Wait until we can make a request to the URL's domain"""
        domain = self._get_domain(url)
        delay = self.get_delay(url)

        async with self._lock:
            now = time.time()
            last = self._last_request.get(domain, 0)
            wait_time = last + delay - now

            if wait_time > 0:
                logger.debug(f"Rate limiting {domain}: waiting {wait_time:.2f}s")
                await asyncio.sleep(wait_time)

            self._last_request[domain] = time.time()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class SemaphoreRateLimiter:
    """Rate limiter using semaphores for concurrent requests"""

    def __init__(
        self,
        max_concurrent: int = 5,
        per_domain_limit: int = 2,
        delay: Optional[float] = None
    ):
        self.max_concurrent = max_concurrent
        self.per_domain_limit = per_domain_limit
        self.delay = delay or settings.rate_limit

        self._global_semaphore = asyncio.Semaphore(max_concurrent)
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _get_domain(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc

    async def _get_domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        """Get or create semaphore for a domain"""
        async with self._lock:
            if domain not in self._domain_semaphores:
                self._domain_semaphores[domain] = asyncio.Semaphore(self.per_domain_limit)
            return self._domain_semaphores[domain]

    async def acquire(self, url: str) -> tuple[asyncio.Semaphore, asyncio.Semaphore]:
        """Acquire both global and domain semaphores"""
        domain = self._get_domain(url)
        domain_sem = await self._get_domain_semaphore(domain)

        await self._global_semaphore.acquire()
        await domain_sem.acquire()

        # Apply delay
        async with self._lock:
            now = time.time()
            last = self._last_request.get(domain, 0)
            wait_time = last + self.delay - now

            if wait_time > 0:
                await asyncio.sleep(wait_time)

            self._last_request[domain] = time.time()

        return self._global_semaphore, domain_sem

    def release(self, url: str, semaphores: tuple[asyncio.Semaphore, asyncio.Semaphore]) -> None:
        """Release acquired semaphores"""
        global_sem, domain_sem = semaphores
        domain_sem.release()
        global_sem.release()
