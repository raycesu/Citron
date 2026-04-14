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
MAX_EXTRACT_URLS = 50

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
        "linkedin.com",
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
    "/blog",
    "/news",
    "/article",
    "/articles",
    "/press",
    "/careers",
    "/jobs",
    "/job/",
    "/company",
    "/about",
    "/contact",
    "/docs",
    "/documentation",
    "/pricing",
    "/terms",
    "/privacy",
)

_KEEP_QUERY_PARAMS = frozenset({"id", "event", "event_id", "slug"})
_EVENT_TIME_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"\d{1,2}:\d{2}\s?(?:am|pm)?|"
    r"\d{1,2}\s?(?:am|pm)|"
    r"\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}/\d{1,2}(?:/\d{2,4})?"
    r")\b",
    re.IGNORECASE,
)
_EVENT_LOCATION_RE = re.compile(
    r"\b("
    r"venue|location|address|campus|hall|auditorium|center|centre|"
    r"room|online|in-person|in person|virtual|toronto|vancouver|montreal|"
    r"new york|san francisco|boston|chicago|seattle|los angeles"
    r")\b",
    re.IGNORECASE,
)
_EVENT_SIGNUP_RE = re.compile(
    r"\b("
    r"register|registration|rsvp|tickets?|sign[\s\-]?up|apply|application|"
    r"book\s+now|get\s+tickets|join\s+waitlist|reserve\s+spot|claim\s+spot"
    r")\b",
    re.IGNORECASE,
)
_SIGNUP_HREF_TERMS = ("register", "signup", "rsvp", "ticket", "apply", "waitlist")
_LOCATION_ATTR_SELECTORS = (
    '[itemprop="location"]',
    '[itemprop="addressLocality"]',
    '[class*="location"]',
    '[class*="venue"]',
)
_LOCATION_HINT_RE = re.compile(
    r"\b("
    r"toronto|vancouver|montreal|ottawa|calgary|edmonton|new york|san francisco|boston|"
    r"chicago|seattle|los angeles|austin|denver|miami|atlanta|washington|"
    r"online|virtual|in-person|in person|hybrid|"
    r"venue|hall|center|centre|campus|university|auditorium|hotel|conference"
    r")\b",
    re.IGNORECASE,
)
_NON_LOCATION_RE = re.compile(
    r"\b("
    r"cookie|privacy|terms|login|sign in|sign up|subscribe|newsletter|"
    r"share|follow|copyright|all rights reserved|contact us|about us|"
    r"event details|read more|learn more|book now"
    r")\b",
    re.IGNORECASE,
)


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
        if _host_matches(host, "linkedin.com"):
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
    if re.match(r"^-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+$", cleaned):
        return ""
    if cleaned.count("|") >= 2:
        return ""
    if _NON_LOCATION_RE.search(cleaned):
        return ""
    if re.search(r"(facebook|twitter|x-twitter|youtube|telegram|linkedin){2,}", cleaned, re.IGNORECASE):
        return ""
    return cleaned


def _location_score(location: str) -> int:
    score = 0
    lowered = location.lower()
    if _LOCATION_HINT_RE.search(lowered):
        score += 3
    if "," in location:
        score += 2
    if 8 <= len(location) <= 100:
        score += 1
    if any(token in lowered for token in ("street", "st.", "ave", "road", "blvd", "boulevard")):
        score += 1
    return score


def _pick_best_location(candidates: list[str]) -> str:
    best = ""
    best_score = -1
    for raw in candidates:
        cleaned = _clean_location(raw)
        if not cleaned:
            continue
        score = _location_score(cleaned)
        if score > best_score:
            best = cleaned
            best_score = score
    return best


def _normalize_signup_url(page_url: str, raw_url: str) -> str:
    candidate = (raw_url or "").strip()
    if not candidate:
        return ""
    if candidate.startswith(("mailto:", "tel:", "javascript:", "#")):
        return ""
    parsed = urlparse(candidate)
    if parsed.scheme in {"http", "https"}:
        return candidate
    if candidate.startswith("//"):
        scheme = urlparse(page_url).scheme or "https"
        return f"{scheme}:{candidate}"
    page = urlparse(page_url)
    if not page.netloc:
        return ""
    base = f"{page.scheme or 'https'}://{page.netloc}"
    if candidate.startswith("/"):
        return f"{base}{candidate}"
    path = page.path.rsplit("/", 1)[0] if "/" in page.path else ""
    return f"{base}{path}/{candidate}".replace("//", "/").replace(":/", "://", 1)


def _extract_ld_location(obj) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        for item in obj:
            candidate = _extract_ld_location(item)
            if candidate:
                return candidate
        return ""
    if not isinstance(obj, dict):
        return ""
    pieces: list[str] = []
    for key in ("name", "address", "streetAddress", "addressLocality", "addressRegion", "addressCountry"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            pieces.append(val.strip())
        elif key == "address":
            nested = _extract_ld_location(val)
            if nested:
                pieces.append(nested)
    return ", ".join(dict.fromkeys(pieces))


def _extract_ld_signup_url(obj) -> str:
    if isinstance(obj, str):
        return obj.strip()
    if isinstance(obj, list):
        for item in obj:
            candidate = _extract_ld_signup_url(item)
            if candidate:
                return candidate
        return ""
    if not isinstance(obj, dict):
        return ""
    offers = obj.get("offers")
    if offers:
        offer_url = _extract_ld_signup_url(offers)
        if offer_url:
            return offer_url
    for key in ("url", "sameAs", "registrationUrl"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _collect_ld_event_details(
    obj,
    starts: list[datetime],
    ends: list[datetime],
    locations: list[str],
    signup_urls: list[str],
) -> None:
    if isinstance(obj, dict):
        if _ld_type_matches(obj.get("@type")):
            sd = _parse_date_value(obj.get("startDate") or obj.get("startTime"))
            ed = _parse_date_value(obj.get("endDate") or obj.get("endTime"))
            if sd:
                starts.append(sd)
            if ed:
                ends.append(ed)
            loc = _extract_ld_location(obj.get("location"))
            if loc:
                locations.append(loc)
            signup = _extract_ld_signup_url(obj)
            if signup:
                signup_urls.append(signup)
        for v in obj.values():
            _collect_ld_event_details(v, starts, ends, locations, signup_urls)
    elif isinstance(obj, list):
        for item in obj:
            _collect_ld_event_details(item, starts, ends, locations, signup_urls)


def _extract_signup_url_from_tree(tree: HTMLParser, page_url: str) -> str:
    for meta_key in ("event:ticket_url", "event:register_url", "og:url"):
        content = _meta_content(tree, meta_key)
        normalized = _normalize_signup_url(page_url, content)
        if normalized and any(term in normalized.lower() for term in _SIGNUP_HREF_TERMS):
            return normalized

    for node in tree.css("a[href], button[data-href], form[action]"):
        raw_target = (
            node.attributes.get("href")
            or node.attributes.get("data-href")
            or node.attributes.get("action")
            or ""
        )
        if not raw_target:
            continue
        node_text = (node.text(strip=True) or "").lower()
        lowered_target = raw_target.lower()
        if not any(term in lowered_target or term in node_text for term in _SIGNUP_HREF_TERMS):
            continue
        normalized = _normalize_signup_url(page_url, raw_target)
        if normalized:
            return normalized
    return ""


def extract_event_details_from_tree(tree: HTMLParser, page_url: str) -> tuple[datetime | None, datetime | None, str, str]:
    """Structured-first extraction of datetime, location, and signup URL."""
    starts: list[datetime] = []
    ends: list[datetime] = []
    locations: list[str] = []
    signup_urls: list[str] = []

    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        _collect_ld_event_details(data, starts, ends, locations, signup_urls)

    if not starts and not ends:
        og_s = _parse_date_value(_meta_content(tree, "event:start_time"))
        og_e = _parse_date_value(_meta_content(tree, "event:end_time"))
        if og_s:
            starts.append(og_s)
        if og_e:
            ends.append(og_e)

    if not starts and not ends:
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

    if not starts and not ends:
        times: list[datetime] = []
        for tnode in tree.css("time[datetime]"):
            dt = _parse_date_value(tnode.attributes.get("datetime"))
            if dt:
                times.append(dt)
        if len(times) >= 2:
            starts.append(min(times))
            ends.append(max(times))
        elif len(times) == 1:
            starts.append(times[0])

    location = _pick_best_location(locations)
    if not location:
        location = _pick_best_location(
            [
                _meta_content(tree, "event:location"),
                _meta_content(tree, "og:locality"),
                _meta_content(tree, "geo.placename"),
                _meta_content(tree, "event:venue"),
            ]
        )
    if not location:
        dom_candidates = [node.text(strip=True) for node in tree.css(", ".join(_LOCATION_ATTR_SELECTORS))]
        location = _pick_best_location(dom_candidates[:8])

    signup_url = ""
    for raw_signup in signup_urls:
        normalized = _normalize_signup_url(page_url, raw_signup)
        if normalized:
            signup_url = normalized
            break
    if not signup_url:
        signup_url = _extract_signup_url_from_tree(tree, page_url)

    start_date = min(starts) if starts else None
    end_date = max(ends) if ends else None
    return start_date, end_date, location, signup_url


def _has_event_page_signals(
    tree: HTMLParser,
    title: str,
    description: str,
    location: str,
    signup_url: str,
    start_date: datetime | None,
    end_date: datetime | None,
) -> bool:
    """Accept discovered pages only when at least 2 of 3 event-detail signals exist."""
    page_text = " ".join(
        part
        for part in [
            title,
            description,
            location,
            _meta_content(tree, "description"),
            _meta_content(tree, "og:description"),
            _meta_content(tree, "event:location"),
            (tree.body.text(separator=" ", strip=True)[:4000] if tree.body else ""),
        ]
        if part
    )
    lowered = page_text.lower()
    has_time_signal = bool(start_date or end_date or _EVENT_TIME_RE.search(lowered))
    has_location_signal = bool(location or _EVENT_LOCATION_RE.search(lowered))
    has_signup_signal = bool(
        signup_url
        or _EVENT_SIGNUP_RE.search(signup_url.lower() if signup_url else "")
        or _EVENT_SIGNUP_RE.search((tree.body.text(separator=" ", strip=True)[:1200] if tree.body else "").lower())
        or _EVENT_SIGNUP_RE.search(lowered)
        or tree.css_first('a[href*="register"], a[href*="signup"], a[href*="rsvp"], a[href*="ticket"]')
        or tree.css_first('button[class*="register"], button[class*="signup"], button[class*="rsvp"]')
    )
    score = int(has_time_signal) + int(has_location_signal) + int(has_signup_signal)
    return score >= 2


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
    start_d, end_d, _, _ = extract_event_details_from_tree(tree, "")
    return start_d, end_d


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

            start_date, end_date, location, signup_url = extract_event_details_from_tree(tree, url)
            country, province_state = _infer_country_province(location)

            if not _has_event_page_signals(
                tree=tree,
                title=title,
                description=description,
                location=location,
                signup_url=signup_url,
                start_date=start_date,
                end_date=end_date,
            ):
                return None

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
