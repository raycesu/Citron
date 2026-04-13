"""
Scraper unit tests using stored HTML fixtures.
These tests verify parser logic without making network requests.
"""
import json
import pytest

from backend.scrapers.devpost import DevpostScraper, _infer_country_province


# ---------------------------------------------------------------------------
# Devpost parser helpers
# ---------------------------------------------------------------------------


def test_infer_country_canada():
    country, province = _infer_country_province("Toronto, Ontario")
    assert country == "Canada"
    assert province == "Ontario"


def test_infer_country_usa():
    country, province = _infer_country_province("San Francisco, California")
    assert country == "USA"
    assert province == "California"


def test_infer_country_online():
    country, province = _infer_country_province("Online")
    assert country == "Online"
    assert province == ""


def test_infer_country_other():
    country, province = _infer_country_province("Berlin, Germany")
    assert country == "Other"


def test_devpost_parse_full_item():
    scraper = DevpostScraper()
    item = {
        "title": "ETH Toronto 2025",
        "url": "https://devpost.com/hackathons/eth-toronto",
        "displayed_location": {"location": "Toronto, Ontario"},
        "submission_period_dates": "Jun 15 - Jun 17, 2025",
        "prize_amount": "$50,000",
        "tagline": "Build the future of DeFi",
    }
    event = scraper._parse(item)
    assert event is not None
    assert event.title == "ETH Toronto 2025"
    assert event.country == "Canada"
    assert event.province_state == "Ontario"
    assert event.city == "Toronto"
    assert event.prize_pool == "$50,000"
    assert event.source == "devpost"


def test_devpost_parse_missing_url_returns_none():
    scraper = DevpostScraper()
    item = {"title": "Some Hackathon", "url": ""}
    result = scraper._parse(item)
    assert result is None


def test_devpost_parse_missing_title_returns_none():
    scraper = DevpostScraper()
    item = {"title": "", "url": "https://devpost.com/hackathons/something"}
    result = scraper._parse(item)
    assert result is None


# ---------------------------------------------------------------------------
# SearchDiscoveryScraper constants and concurrency behavior
# ---------------------------------------------------------------------------


def test_search_discovery_request_delay_is_lower_than_base():
    """SearchDiscoveryScraper must override REQUEST_DELAY below the global default."""
    from backend.scrapers.base import BaseScraper
    from backend.scrapers.base import REQUEST_DELAY as BASE_DELAY
    from backend.scrapers.search_discovery import SearchDiscoveryScraper

    scraper = SearchDiscoveryScraper()
    assert scraper.REQUEST_DELAY < BASE_DELAY
    # Sanity: the base class default should be unchanged
    assert BaseScraper.REQUEST_DELAY == BASE_DELAY


def test_search_discovery_extraction_cap_constant():
    """MAX_EXTRACT_URLS must be less than the old hard-coded cap of 80."""
    from backend.scrapers.search_discovery import MAX_EXTRACT_URLS

    assert MAX_EXTRACT_URLS < 80
    assert MAX_EXTRACT_URLS > 0


def test_search_discovery_concurrency_constant():
    """EXTRACT_CONCURRENCY must be a positive integer."""
    from backend.scrapers.search_discovery import EXTRACT_CONCURRENCY

    assert isinstance(EXTRACT_CONCURRENCY, int)
    assert EXTRACT_CONCURRENCY > 0


def test_search_discovery_scrape_respects_cap(monkeypatch):
    """scrape() must not pass more than MAX_EXTRACT_URLS to _extract_event."""
    import asyncio
    from backend.scrapers.search_discovery import SearchDiscoveryScraper, MAX_EXTRACT_URLS

    scraper = SearchDiscoveryScraper()

    # Generate more URLs than the cap
    fake_urls = [f"https://example.com/event-{i}" for i in range(MAX_EXTRACT_URLS + 20)]
    extracted_urls: list[str] = []

    async def fake_search(client, query):
        return fake_urls

    async def fake_extract(client, url):
        extracted_urls.append(url)
        return None

    monkeypatch.setattr(scraper, "_search", fake_search)
    monkeypatch.setattr(scraper, "_extract_event", fake_extract)

    asyncio.run(scraper.scrape())

    assert len(extracted_urls) <= MAX_EXTRACT_URLS


def test_search_discovery_scrape_collects_valid_events(monkeypatch):
    """scrape() must return all non-None events from concurrent extraction."""
    import asyncio
    from backend.scrapers.search_discovery import SearchDiscoveryScraper
    from backend.filtering import RawEvent

    scraper = SearchDiscoveryScraper()
    fake_event = RawEvent(
        title="Blockchain Hack 2026",
        url="https://example.com/hack",
        source="search_discovery",
    )

    async def fake_search(client, query):
        return ["https://example.com/a", "https://example.com/b", "https://example.com/c"]

    call_count = 0

    async def fake_extract(client, url):
        nonlocal call_count
        call_count += 1
        # Return a real event for the first URL, None for the rest
        if url == "https://example.com/a":
            return fake_event
        return None

    monkeypatch.setattr(scraper, "_search", fake_search)
    monkeypatch.setattr(scraper, "_extract_event", fake_extract)

    events = asyncio.run(scraper.scrape())

    assert len(events) == 1
    assert events[0].title == "Blockchain Hack 2026"
    # All three URLs were attempted despite one returning None
    assert call_count == 3


def test_search_discovery_scrape_skips_extraction_exceptions(monkeypatch):
    """Exceptions inside _extract_event must not prevent other URLs from completing."""
    import asyncio
    from backend.scrapers.search_discovery import SearchDiscoveryScraper
    from backend.filtering import RawEvent

    scraper = SearchDiscoveryScraper()
    good_event = RawEvent(
        title="Good Event",
        url="https://example.com/good",
        source="search_discovery",
    )

    async def fake_search(client, query):
        return ["https://example.com/bad", "https://example.com/good"]

    async def fake_extract(client, url):
        if "bad" in url:
            raise RuntimeError("network timeout")
        return good_event

    monkeypatch.setattr(scraper, "_search", fake_search)
    monkeypatch.setattr(scraper, "_extract_event", fake_extract)

    events = asyncio.run(scraper.scrape())

    assert len(events) == 1
    assert events[0].title == "Good Event"


# ---------------------------------------------------------------------------
# ETHGlobal JSON item parser
# ---------------------------------------------------------------------------


def test_ethglobal_parse_item():
    from backend.scrapers.ethglobal import ETHGlobalScraper

    scraper = ETHGlobalScraper()
    item = {
        "name": "ETHGlobal Toronto",
        "slug": "toronto2025",
        "location": "Toronto",
        "description": "Ethereum hackathon in Toronto",
        "startDate": "2025-08-10",
        "endDate": "2025-08-12",
        "travelGrant": True,
    }
    event = scraper._parse_json_item(item)
    assert event is not None
    assert event.url == "https://ethglobal.com/events/toronto2025"
    assert event.has_travel_grant is True
    assert event.country == "Canada"
