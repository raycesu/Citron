"""
Luma (luma.com) scraper.
Targets multiple discovery pages and uses the paginated JSON API when available.
Falls back to __NEXT_DATA__ extraction from the rendered HTML.
"""
import json
import logging
import re

import httpx
from dateutil import parser as dateparser

from backend.filtering import RawEvent, is_valid_event_title, is_valid_luma_url
from backend.scrapers.base import BaseScraper
from backend.scrapers.devpost import _infer_country_province

logger = logging.getLogger(__name__)

DISCOVERY_URLS = [
    # Web3 / blockchain categories
    "https://luma.com/crypto",
    "https://luma.com/web3",
    "https://luma.com/solana",
    # Tech & startup categories
    "https://luma.com/ai",
    "https://luma.com/startups",
    "https://luma.com/hackathon",
    # City pages for major Canadian & US tech hubs
    "https://luma.com/toronto",
    "https://luma.com/waterloo",
    "https://luma.com/ottawa",
    "https://luma.com/vancouver",
    "https://luma.com/montreal",
    "https://luma.com/calgary",
    "https://luma.com/nyc",
    "https://luma.com/sf",
    "https://luma.com/boston",
    "https://luma.com/seattle",
    "https://luma.com/austin",
    "https://luma.com/chicago",
    "https://luma.com/los-angeles",
    "https://luma.com/denver",
    "https://luma.com/miami",
    "https://luma.com/atlanta",
    "https://luma.com/philadelphia",
    "https://luma.com/dc",
]

# Luma's public discover API endpoint (GET as of 2026)
API_URL = "https://api.luma.com/discover/get-paginated-events"

# Categories to query via the paginated API (in addition to HTML scraping)
API_CATEGORIES = ["crypto", "web3", "ethereum", "ai", "startups"]


class LumaScraper(BaseScraper):
    NAME = "luma"
    LAYER = "minor"

    async def scrape(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Try the paginated API first for each category
            for category in API_CATEGORIES:
                try:
                    batch = await self._scrape_api(client, category)
                    for e in batch:
                        if e.url not in seen_urls:
                            seen_urls.add(e.url)
                            events.append(e)
                except Exception as exc:
                    logger.debug(f"Luma API category {category} error: {exc}")

            # Also scrape HTML discovery pages
            for page_url in DISCOVERY_URLS:
                try:
                    batch = await self._scrape_page(client, page_url)
                    for e in batch:
                        if e.url not in seen_urls:
                            seen_urls.add(e.url)
                            events.append(e)
                except Exception as exc:
                    logger.warning(f"Luma page {page_url} error: {exc}")

        return events

    async def _scrape_api(self, client: httpx.AsyncClient, category: str) -> list[RawEvent]:
        """Fetch events from Luma's paginated discover API (GET as of 2026)."""
        results: list[RawEvent] = []
        pagination_cursor = None

        for _ in range(3):  # up to 3 pages per category
            try:
                params: dict = {
                    "category_slugs": category,
                    "period": "future",
                }
                if pagination_cursor:
                    params["pagination_cursor"] = pagination_cursor

                resp = await client.get(
                    API_URL,
                    params=params,
                    headers={**self.headers, "Accept": "application/json"},
                    timeout=20,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
                entries = data.get("entries") or data.get("events") or []
                for item in entries:
                    event = self._parse_event(item)
                    if event:
                        results.append(event)

                pagination_cursor = data.get("next_cursor") or data.get("pagination_cursor")
                if not pagination_cursor or not entries:
                    break
            except Exception as exc:
                logger.debug(f"Luma API page error for {category}: {exc}")
                break

        return results

    async def _scrape_page(self, client: httpx.AsyncClient, url: str) -> list[RawEvent]:
        resp = await self._get(client, url)
        html = resp.text

        # Try __NEXT_DATA__ JSON first
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(1))
                raw_events = self._extract_from_next_data(data)
                if raw_events:
                    return raw_events
            except json.JSONDecodeError:
                pass

        return []

    def _extract_from_next_data(self, data: dict) -> list[RawEvent]:
        results = []
        try:
            props = data.get("props", {}).get("pageProps", {})
            # New structure (luma.com 2026): initialData.data.entries
            init = props.get("initialData") or {}
            if isinstance(init, dict):
                inner = init.get("data") or {}
                events_list = (
                    inner.get("entries")
                    or inner.get("events")
                    or props.get("events")
                    or props.get("initialEvents")
                    or props.get("featuredEvents")
                    or []
                )
            else:
                events_list = (
                    props.get("events")
                    or props.get("initialEvents")
                    or props.get("featuredEvents")
                    or []
                )
            for item in events_list:
                event = self._parse_event(item)
                if event:
                    results.append(event)
        except Exception as exc:
            logger.debug(f"Luma next data extraction error: {exc}")
        return results

    def _parse_event(self, item: dict) -> RawEvent | None:
        try:
            event_data = item.get("event") or item
            title = (event_data.get("name") or "").strip()
            url = event_data.get("url") or ""
            if not url and event_data.get("api_id"):
                url = f"https://luma.com/{event_data['api_id']}"
            if not title or not url:
                return None
            if not is_valid_event_title(title) or not is_valid_luma_url(url):
                return None

            geo = event_data.get("geo_address_info") or {}
            city = geo.get("city") or event_data.get("city") or ""
            country_code = geo.get("country_code") or ""
            full_country = geo.get("country") or ""

            # Map country codes to our taxonomy
            if country_code in ("CA", "CAN") or "canada" in full_country.lower():
                country = "Canada"
            elif country_code in ("US", "USA") or "united states" in full_country.lower():
                country = "USA"
            elif event_data.get("virtual"):
                country = "Online"
            else:
                country = "Other" if full_country else "Online"

            _, province_state = _infer_country_province(
                f"{city} {full_country}"
            )

            start_at = event_data.get("start_at") or ""
            end_at = event_data.get("end_at") or ""

            is_virtual = bool(event_data.get("virtual") or event_data.get("zoom_meeting_url"))
            is_inperson = not is_virtual

            return RawEvent(
                title=title,
                url=url if url.startswith("http") else f"https://luma.com/{url}",
                description=event_data.get("description") or "",
                source=self.NAME,
                location=geo.get("full_address") or city,
                city=city,
                country=country,
                province_state=province_state,
                start_date=_safe_parse(start_at),
                end_date=_safe_parse(end_at),
                is_online=is_virtual,
                is_inperson=is_inperson,
            )
        except Exception as exc:
            logger.debug(f"Luma event parse error: {exc}")
            return None


def _safe_parse(value: str | None):
    if not value:
        return None
    try:
        return dateparser.parse(str(value), ignoretz=True)
    except Exception:
        return None
