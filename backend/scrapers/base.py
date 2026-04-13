"""Abstract base class shared by all Citron scrapers."""
import asyncio
import logging
from abc import ABC, abstractmethod

import httpx

from backend.filtering import RawEvent

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

REQUEST_DELAY = 2.0  # default seconds between requests


class BaseScraper(ABC):
    NAME: str = "base"
    LAYER: str = "major"  # major | minor | search
    # Subclasses can lower this for scrapers that fetch many individual pages
    REQUEST_DELAY: float = REQUEST_DELAY

    def __init__(self) -> None:
        self.logger = logging.getLogger(self.__class__.__name__)
        self.headers = DEFAULT_HEADERS.copy()
        self._had_error: bool = False

    @abstractmethod
    async def scrape(self) -> list[RawEvent]:
        """Fetch and return normalised events from this source."""
        ...

    async def safe_scrape(self) -> list[RawEvent]:
        """Wrapper that swallows exceptions so one failing source never breaks the pipeline."""
        self._had_error = False
        try:
            events = await self.scrape()
            self.logger.info(f"[{self.NAME}] collected {len(events)} events")
            return events
        except Exception as exc:
            self.logger.error(f"[{self.NAME}] scrape failed: {exc}", exc_info=True)
            self._had_error = True
            return []

    async def _get(self, client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
        """Throttled GET with a small delay to be polite."""
        await asyncio.sleep(self.REQUEST_DELAY)
        timeout = kwargs.pop("timeout", 20)
        extra_headers = kwargs.pop("headers", None)
        merged_headers = {**self.headers, **(extra_headers or {})}
        response = await client.get(url, headers=merged_headers, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response
