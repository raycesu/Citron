"""
Unit tests for deterministic filtering and deduplication logic.
These tests never touch the network or the database.
"""
from datetime import datetime

import pytest

from backend.filtering import (
    RawEvent,
    deduplicate_raw_events,
    filter_events,
    filter_future_events,
    is_relevant_event,
    is_trusted_source,
    normalize_title,
)


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------


def test_normalize_title_lowercases():
    assert normalize_title("ETHGlobal Hackathon 2024") == "ethglobal hackathon 2024"


def test_normalize_title_strips_punctuation():
    assert normalize_title("Web3 Summit: Day 1!") == "web3 summit day 1"


def test_normalize_title_collapses_whitespace():
    assert normalize_title("  Solana  Hacker   House  ") == "solana hacker house"


# ---------------------------------------------------------------------------
# is_trusted_source
# ---------------------------------------------------------------------------


def test_trusted_source_ethglobal():
    assert is_trusted_source("https://ethglobal.com/events/paris") is True


def test_trusted_source_devpost():
    assert is_trusted_source("https://devpost.com/hackathons/web3-hack") is True


def test_not_trusted_source_random():
    assert is_trusted_source("https://meetup.com/random-event") is False


# ---------------------------------------------------------------------------
# is_relevant_event
# ---------------------------------------------------------------------------


def _make(title="", description="", url="https://example.com"):
    return RawEvent(title=title, url=url, description=description)


def test_relevant_blockchain_keyword():
    event = _make(title="Build on Ethereum Workshop")
    assert is_relevant_event(event) is True


def test_relevant_hackathon_with_blockchain_keyword():
    event = _make(title="University Web3 Hackathon 2024")
    assert is_relevant_event(event) is True


def test_relevant_in_description():
    event = _make(title="Student Tech Conference", description="Focusing on DeFi and smart contracts")
    assert is_relevant_event(event) is True


def test_irrelevant_event_rejected():
    event = _make(title="Annual BBQ Contest", description="Grilling, food, prizes")
    assert is_relevant_event(event) is False


def test_irrelevant_tech_only_without_campus_or_event_anchor():
    event = _make(
        title="Generative AI Product Launch",
        description="Startup networking and pitches for SaaS founders",
    )
    assert is_relevant_event(event) is False


def test_tech_campus_without_blockchain_rejected():
    event = _make(
        title="Computer vision intensive",
        description="Graduate cohort; on campus",
    )
    assert is_relevant_event(event) is False


def test_trusted_domain_still_requires_keywords():
    event = _make(title="Annual Event", url="https://ethglobal.com/events/annual")
    assert is_relevant_event(event) is False


def test_trusted_domain_passes_with_blockchain_keyword():
    event = _make(title="ETHGlobal — L2 Build Day", url="https://ethglobal.com/events/l2-day")
    assert is_relevant_event(event) is True


# ---------------------------------------------------------------------------
# filter_future_events
# ---------------------------------------------------------------------------


def test_future_event_kept():
    ev = RawEvent(title="Future Hack", url="https://a.com", start_date=datetime(2099, 1, 1))
    result = filter_future_events([ev])
    assert len(result) == 1


def test_past_event_removed():
    ev = RawEvent(title="Past Hack", url="https://b.com", start_date=datetime(2000, 1, 1))
    result = filter_future_events([ev])
    assert len(result) == 0


def test_no_date_event_kept():
    ev = RawEvent(title="No Date Hack", url="https://c.com")
    result = filter_future_events([ev])
    assert len(result) == 1


def test_ongoing_event_kept_via_end_date():
    ev = RawEvent(
        title="Ongoing Hack",
        url="https://d.com",
        start_date=datetime(2000, 1, 1),
        end_date=datetime(2099, 12, 31),
    )
    result = filter_future_events([ev])
    assert len(result) == 1


def test_search_discovery_past_year_in_title_dropped():
    past_y = datetime.now().year - 1
    ev = RawEvent(
        title=f"Blockchain Summit {past_y}",
        url="https://example.com/e",
        source="search_discovery",
    )
    result = filter_future_events([ev])
    assert len(result) == 0


def test_search_discovery_current_year_in_title_kept_without_dates():
    y = datetime.now().year
    ev = RawEvent(
        title=f"Blockchain Summit {y}",
        url="https://example.com/e",
        source="search_discovery",
    )
    result = filter_future_events([ev])
    assert len(result) == 1


def test_search_discovery_no_year_in_title_still_kept():
    ev = RawEvent(
        title="Weekly Web3 Developer Meetup",
        url="https://example.com/e",
        source="search_discovery",
    )
    result = filter_future_events([ev])
    assert len(result) == 1


def test_extract_event_datetimes_from_json_ld():
    from selectolax.parser import HTMLParser

    from backend.scrapers.search_discovery import extract_event_datetimes_from_tree

    html = """<!DOCTYPE html><html><head>
    <script type="application/ld+json">
    {"@type":"Event","startDate":"2027-06-01T10:00:00","endDate":"2027-06-03T18:00:00"}
    </script></head><body></body></html>"""
    tree = HTMLParser(html)
    start_d, end_d = extract_event_datetimes_from_tree(tree)
    assert start_d is not None and start_d.year == 2027 and start_d.month == 6
    assert end_d is not None and end_d.day == 3


# ---------------------------------------------------------------------------
# deduplicate_raw_events
# ---------------------------------------------------------------------------


def test_dedup_by_url():
    events = [
        RawEvent(title="Web3 Hack", url="https://same.com"),
        RawEvent(title="Web3 Hack", url="https://same.com"),
    ]
    result = deduplicate_raw_events(events)
    assert len(result) == 1


def test_dedup_by_title_date_city():
    dt = datetime(2025, 6, 1)
    events = [
        RawEvent(title="Ethereum Summit", url="https://a.com", start_date=dt, city="Toronto"),
        RawEvent(title="Ethereum Summit", url="https://b.com", start_date=dt, city="Toronto"),
    ]
    result = deduplicate_raw_events(events)
    assert len(result) == 1


def test_no_false_dedup_different_city():
    dt = datetime(2025, 6, 1)
    events = [
        RawEvent(title="Ethereum Summit", url="https://a.com", start_date=dt, city="Toronto"),
        RawEvent(title="Ethereum Summit", url="https://b.com", start_date=dt, city="Vancouver"),
    ]
    result = deduplicate_raw_events(events)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# filter_events (combined)
# ---------------------------------------------------------------------------


def test_filter_events_rejects_irrelevant():
    events = [
        RawEvent(title="Blockchain Workshop", url="https://a.com"),
        RawEvent(title="Cooking Class", url="https://b.com"),
    ]
    result = filter_events(events)
    assert len(result) == 1
    assert result[0].title == "Blockchain Workshop"


def test_art_conference_without_crypto_rejected():
    event = _make(
        title="Contemporary Art Conference 2026",
        description="Galleries, curators, and collectors summit",
    )
    assert is_relevant_event(event) is False


def test_agricultural_fintech_summit_without_crypto_rejected():
    event = _make(
        title="Agri-Fintech Summit",
        description="Farm finance, lending, and rural banking innovation",
    )
    assert is_relevant_event(event) is False


def test_eth_abbreviation_in_title_passes():
    event = _make(title="ETH Denver Side Events", description="Meet builders")
    assert is_relevant_event(event) is True
