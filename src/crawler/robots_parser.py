"""robots.txt parser for ethical crawling"""

import asyncio
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from ..config import settings
from ..utils.logging import get_logger

logger = get_logger("crawler.robots")


class RobotsParser:
    """Parse and check robots.txt for crawling permissions"""

    def __init__(self, user_agent: Optional[str] = None):
        self.user_agent = user_agent or settings.user_agent
        self._cache: dict[str, RobotFileParser] = {}
        self._lock = asyncio.Lock()

    def _get_robots_url(self, url: str) -> str:
        """Get robots.txt URL for a given URL"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    async def _fetch_robots(self, base_url: str) -> Optional[RobotFileParser]:
        """Fetch and parse robots.txt for a domain"""
        robots_url = self._get_robots_url(base_url)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    robots_url,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                )

                if response.status_code == 200:
                    rp = RobotFileParser()
                    rp.set_url(robots_url)
                    rp.parse(response.text.splitlines())
                    return rp
                elif response.status_code in (404, 403):
                    # No robots.txt or access denied - allow everything
                    rp = RobotFileParser()
                    rp.set_url(robots_url)
                    rp.parse([])  # Empty rules = allow all
                    return rp

        except Exception as e:
            logger.debug(f"Could not fetch robots.txt from {robots_url}: {e}")

        # Return permissive parser on error
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.parse([])
        return rp

    async def get_parser(self, url: str) -> RobotFileParser:
        """Get or fetch robots.txt parser for a URL"""
        robots_url = self._get_robots_url(url)

        async with self._lock:
            if robots_url not in self._cache:
                parser = await self._fetch_robots(url)
                if parser:
                    self._cache[robots_url] = parser

            return self._cache.get(robots_url)

    async def can_fetch(self, url: str) -> bool:
        """Check if we're allowed to fetch a URL"""
        parser = await self.get_parser(url)

        if parser is None:
            # If we couldn't get robots.txt, be permissive
            return True

        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception as e:
            logger.debug(f"Error checking robots.txt for {url}: {e}")
            return True

    async def get_crawl_delay(self, url: str) -> Optional[float]:
        """Get crawl delay specified in robots.txt"""
        parser = await self.get_parser(url)

        if parser is None:
            return None

        try:
            delay = parser.crawl_delay(self.user_agent)
            return float(delay) if delay else None
        except Exception:
            return None

    def clear_cache(self) -> None:
        """Clear the robots.txt cache"""
        self._cache.clear()
