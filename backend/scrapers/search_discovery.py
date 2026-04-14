"""
Layer 3 – Search-based event discovery.

Runs structured search queries against the Brave Search API (if a key is
configured) or falls back to DuckDuckGo HTML scraping. Each scrape executes
SEARCH_QUERIES[:MAX_QUERIES] (see constants below). Discovered URLs are
fetched and parsed concurrently (bounded by EXTRACT_CONCURRENCY) into
RawEvent objects via a lightweight extractor.
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from backend.filtering import RawEvent, is_valid_event_title
from backend.scrapers.base import BaseScraper
from backend.scrapers.devpost import _infer_country_province

logger = logging.getLogger(__name__)

BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")
BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
DDG_URL = "https://html.duckduckgo.com/html/"

SEARCH_QUERIES = [
    "site:luma.com blockchain OR web3 hackathon North America 2026",
    "site:luma.com blockchain OR web3 conference Canada 2026",
    "site:luma.com web3 hackathon \"travel\" OR \"scholarship\" OR \"sponsored\" 2026",
    "site:luma.com blockchain university OR student event North America 2026",
    "site:luma.com hacker house blockchain North America 2026",
    "site:devpost.com blockchain OR web3 hackathon North America 2026",
    "site:devpost.com student blockchain hackathon \"travel grant\" OR \"travel reimbursement\"",
    "site:eventbrite.com blockchain OR web3 conference OR hackathon North America 2026",
    "blockchain hackathon \"travel stipend\" OR \"travel grant\" North America 2026",
    "web3 hackathon \"scholarship\" OR \"free registration\" student Canada USA 2026",
    "ETHGlobal hackathon North America 2026",
    "blockchain conference Canada USA 2026",
    "web3 developer summit North America 2026",
    "university blockchain hackathon OR conference North America 2026",
    "student blockchain club conference \"travel subsidy\" OR \"sponsored\" 2026",
    "MLH OR ETHGlobal blockchain hackathon student 2026",
    "site:luma.com university OR campus blockchain conference OR symposium Canada USA 2026",
]

MAX_QUERIES = 17  # covers all queries within daily Brave budget

# Maximum number of discovered URLs to fetch and parse per scan. Keeps runtime
# predictable; the most relevant URLs tend to appear in early query results.
MAX_EXTRACT_URLS = 30

# How many discovered-URL fetches may run concurrently. Bounded to stay polite
# while still giving a large speedup over the previous serial loop.
EXTRACT_CONCURRENCY = 8

_EVENT_LD_TYPES = frozenset(
    {
        "Event",
        "SocialEvent",
        "BusinessEvent",
        "EducationEvent",
        "Hackathon",
        "Festival",
        "MusicEvent",
        "SportsEvent",
    }
)

_SEARCH_BLACKLIST_HOSTS = frozenset(
    {
        "google.com",
        "twitter.com",
        "youtube.com",
        "wikipedia.org",
        "reddit.com",
    }
)

_NON_EVENT_PATH_TERMS = (
    "/calendar",
    "/events",
    "/discover",
    "/explore",
    "/community",
    "/search",
    "/tag/",
    "/tags/",
    "/categories",
    "/category/",
)

_KEEP_QUERY_PARAMS = frozenset({"id", "event", "event_id", "slug"})


def _canonicalize_url(raw_url: str) -> str:
    parsed = urlparse(raw_url.strip())
    scheme = parsed.scheme.lower() or "https"
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path or ""
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    if path == "/":
        path = ""
    query = urlencode(
        sorted(
            [
                (k, v)
                for k, v in parse_qsl(parsed.query, keep_blank_values=False)
                if k.lower() in _KEEP_QUERY_PARAMS
            ]
        ),
        doseq=True,
    )
    return urlunparse((scheme, host, path, "", query, ""))


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def _is_ethglobal_url(raw_url: str) -> bool:
    try:
        host = urlparse(raw_url).netloc.lower().replace("www.", "")
        return _host_matches(host, "ethglobal.com")
    except Exception:
        return False


def _is_non_event_url(raw_url: str) -> bool:
    try:
        parsed = urlparse(raw_url)
        host = parsed.netloc.lower().replace("www.", "")
        if not host:
            return True

        lowered = raw_url.lower()
        path = (parsed.path or "").lower().rstrip("/")
        if any(_host_matches(host, blocked) for blocked in _SEARCH_BLACKLIST_HOSTS):
            return True

        # Generic listing/community pages are noisy and often not a single event.
        if any(term in lowered for term in _NON_EVENT_PATH_TERMS):
            # Keep known event path formats on popular platforms.
            if _host_matches(host, "eventbrite.com") and "/e/" in path:
                return False
            if _host_matches(host, "devpost.com") and "/hackathons/" in path:
                return False
            if _host_matches(host, "luma.com") and (
                path.startswith("/e/") or path.startswith("/event/")
            ):
                return False
            return True
        return False
    except Exception:
        return True


def _clean_location(raw_location: str) -> str:
    cleaned = re.sub(r"\s+", " ", (raw_location or "")).strip()
    if not cleaned:
        return ""
    if len(cleaned) > 180:
        return ""
    if cleaned.lower().startswith("skip to content"):
        return ""
    if re.search(r"(facebook|twitter|x-twitter|youtube|telegram|linkedin){2,}", cleaned, re.IGNORECASE):
        return ""
    return cleaned


def _ld_type_matches(types) -> bool:
    if types is None:
        return False
    if isinstance(types, str):
        types = [types]
    for t in types:
        if not isinstance(t, str):
            continue
        if t in _EVENT_LD_TYPES or t.endswith("Event"):
            return True
    return False


def _parse_date_value(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        ts = float(val)
        if ts > 1e12:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
        except (OSError, ValueError, OverflowError):
            return None
    s = str(val).strip()
    if not s:
        return None
    try:
        return dateparser.parse(s, ignoretz=True)
    except (ValueError, TypeError, OverflowError):
        return None


def _collect_ld_event_dates(obj, starts: list, ends: list) -> None:
    if isinstance(obj, dict):
        if _ld_type_matches(obj.get("@type")):
            sd = _parse_date_value(obj.get("startDate") or obj.get("startTime"))
            ed = _parse_date_value(obj.get("endDate") or obj.get("endTime"))
            if sd:
                starts.append(sd)
            if ed:
                ends.append(ed)
        for v in obj.values():
            _collect_ld_event_dates(v, starts, ends)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ld_event_dates(item, starts, ends)


def _meta_content(tree: HTMLParser, prop: str) -> str:
    n = tree.css_first(f'meta[property="{prop}"]')
    if not n:
        n = tree.css_first(f'meta[name="{prop}"]')
    if not n:
        return ""
    return (n.attributes.get("content") or "").strip()


def extract_event_datetimes_from_tree(tree: HTMLParser) -> tuple[datetime | None, datetime | None]:
    """Best-effort event start/end from JSON-LD, Open Graph event tags, microdata, and <time>."""
    starts: list[datetime] = []
    ends: list[datetime] = []

    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _collect_ld_event_dates(data, starts, ends)

    if starts or ends:
        start_d = min(starts) if starts else None
        end_d = max(ends) if ends else None
        return start_d, end_d

    og_s = _parse_date_value(_meta_content(tree, "event:start_time"))
    og_e = _parse_date_value(_meta_content(tree, "event:end_time"))
    if og_s or og_e:
        return og_s, og_e

    for node in tree.css('[itemprop="startDate"], [itemprop="endDate"]'):
        ip = node.attributes.get("itemprop", "")
        raw = node.attributes.get("datetime") or node.attributes.get("content") or ""
        dt = _parse_date_value(raw)
        if not dt:
            continue
        if ip == "startDate":
            starts.append(dt)
        elif ip == "endDate":
            ends.append(dt)

    times: list[datetime] = []
    for tnode in tree.css("time[datetime]"):
        dt = _parse_date_value(tnode.attributes.get("datetime"))
        if dt:
            times.append(dt)

    if starts or ends:
        start_d = min(starts) if starts else None
        end_d = max(ends) if ends else None
        return start_d, end_d

    if len(times) >= 2:
        return min(times), max(times)
    if len(times) == 1:
        return times[0], None
    return None, None


class SearchDiscoveryScraper(BaseScraper):
    NAME = "search_discovery"
    LAYER = "search"
    # Lower than the global 2 s default: search pages and individual event pages
    # are fetched in high volume, so politeness is maintained via EXTRACT_CONCURRENCY
    # rather than a long serial delay.
    REQUEST_DELAY: float = 0.3

    async def scrape(self) -> list[RawEvent]:
        discovered_urls: set[str] = set()
        if not BRAVE_API_KEY:
            logger.warning(
                "Search discovery: BRAVE_SEARCH_API_KEY is unset — DuckDuckGo often "
                "returns no parseable results for automated clients; set the key for reliable Layer 3."
            )
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for query in SEARCH_QUERIES[:MAX_QUERIES]:
                urls = await self._search(client, query)
                for discovered in urls:
                    canonical = _canonicalize_url(discovered)
                    if _is_ethglobal_url(canonical):
                        continue
                    if _is_non_event_url(canonical):
                        continue
                    discovered_urls.add(canonical)
        if not discovered_urls:
            logger.warning("Search discovery: no URLs from any query (try Brave API key or check DDG HTML selectors).")

        urls_to_fetch = list(discovered_urls)[:MAX_EXTRACT_URLS]
        sem = asyncio.Semaphore(EXTRACT_CONCURRENCY)

        async def _bounded_extract(client: httpx.AsyncClient, url: str) -> RawEvent | None:
            async with sem:
                try:
                    return await self._extract_event(client, url)
                except Exception as exc:
                    logger.debug(f"SearchDiscovery extract error for {url}: {exc}")
                    return None

        async with httpx.AsyncClient(follow_redirects=True) as client:
            results = await asyncio.gather(
                *[_bounded_extract(client, url) for url in urls_to_fetch]
            )

        return [ev for ev in results if ev is not None]

    async def _search(self, client: httpx.AsyncClient, query: str) -> list[str]:
        if BRAVE_API_KEY:
            return await self._brave_search(client, query)
        return await self._ddg_search(client, query)

    async def _brave_search(self, client: httpx.AsyncClient, query: str) -> list[str]:
        try:
            resp = await self._get(
                client,
                BRAVE_API_URL,
                params={"q": query, "count": 10},
                headers={**self.headers, "Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            )
            data = resp.json()
            return [r["url"] for r in data.get("web", {}).get("results", [])]
        except Exception as exc:
            logger.debug(f"Brave search error: {exc}")
            return []

    async def _ddg_search(self, client: httpx.AsyncClient, query: str) -> list[str]:
        """DuckDuckGo HTML search as a free fallback."""
        try:
            resp = await self._get(
                client,
                DDG_URL,
                params={"q": query},
                headers={**self.headers, "Content-Type": "application/x-www-form-urlencoded"},
            )
            tree = HTMLParser(resp.text)
            urls = []
            for a in tree.css("a.result__a"):
                href = a.attributes.get("href", "")
                if href.startswith("http"):
                    urls.append(href)
            return urls[:10]
        except Exception as exc:
            logger.debug(f"DuckDuckGo search error: {exc}")
            return []

    async def _extract_event(self, client: httpx.AsyncClient, url: str) -> RawEvent | None:
        """Best-effort extraction from a discovered URL."""
        if _is_ethglobal_url(url) or _is_non_event_url(url):
            return None

        try:
            resp = await self._get(client, url)
            tree = HTMLParser(resp.text)

            title_node = (
                tree.css_first("h1")
                or tree.css_first('meta[property="og:title"]')
                or tree.css_first("title")
            )
            if title_node:
                title = title_node.attributes.get("content", "") or title_node.text(strip=True)
            else:
                title = ""

            if not title or not is_valid_event_title(title):
                return None

            desc_node = tree.css_first('meta[property="og:description"]') or tree.css_first(
                'meta[name="description"]'
            )
            description = desc_node.attributes.get("content", "") if desc_node else ""

            location = ""
            location_meta = (
                _meta_content(tree, "event:location")
                or _meta_content(tree, "og:locality")
                or _meta_content(tree, "place:location:latitude")
            )
            if location_meta:
                location = location_meta
            else:
                location_node = tree.css_first(
                    '[itemprop="location"], [itemprop="addressLocality"], [class*="location"], [class*="venue"]'
                )
                location = location_node.text(strip=True) if location_node else ""
            location = _clean_location(location)
            country, province_state = _infer_country_province(location)

            start_date, end_date = extract_event_datetimes_from_tree(tree)

            return RawEvent(
                title=title[:300],
                url=url,
                description=description[:800],
                source=self.NAME,
                location=location,
                country=country,
                province_state=province_state,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception:
            return None
