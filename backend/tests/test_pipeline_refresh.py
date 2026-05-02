"""
Tests for the snapshot + reconcile pipeline introduced by the full-refresh strategy.

All DB interaction is routed through an in-memory SQLite database that shares a
single connection via StaticPool, mirroring the pattern used in test_api.py.
Network calls (scrapers, Gemini) are always mocked.
"""
import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.ai_filter import ClassifyEventsResult
from backend.filtering import RawEvent, canonicalize_event_url
from backend.models import AICache, Base, Event, EventTag, Tag

# ---------------------------------------------------------------------------
# In-memory test database shared across all tests in this module
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"

_test_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)


@contextmanager
def _test_db_context():
    """Drop-in replacement for backend.database.get_db_context using the test engine."""
    db = _TestSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture()
def db():
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PATCHES = {
    "db": "backend.scraper.get_db_context",
    "ai": "backend.scraper.classify_events",
    "scrapers": "backend.scraper.run_scrapers",
}


def _raw(url, title="ETH Hackathon", source="devpost", start_date=None):
    """Create a RawEvent whose title always passes the blockchain keyword filter."""
    return RawEvent(title=title, url=url, source=source, start_date=start_date)


def _cls_result(pairs):
    return ClassifyEventsResult(pairs=pairs, hit_rate_limit=False)


def _default_cls(event, relevance=7, priority=6, tags=None):
    return (
        event,
        {
            "relevance_score": relevance,
            "priority_score": priority,
            "tags": tags or ["hackathon"],
            "has_travel_grant": False,
            "is_inperson": True,
            "country": "Canada",
            "province_state": "Ontario",
            "summary": "A great hackathon",
        },
    )


def _add_event(db, url, source="devpost", title="ETH Conference", consecutive_misses=0):
    e = Event(
        title=title,
        normalized_title=title.lower(),
        url=url,
        source=source,
        country="Canada",
        is_inperson=True,
        is_online=False,
        has_travel_grant=False,
        priority_score=5.0,
        relevance_score=5.0,
        scraped_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ai_classified=True,
        consecutive_misses=consecutive_misses,
    )
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


def _run(coro):
    """Run an async coroutine synchronously (no pytest-asyncio required)."""
    return asyncio.run(coro)


def test_merge_event_country_prefers_canada_when_gemini_says_usa():
    """Luma/Devpost often have correct geo; Gemini can mislabel from US-centric text."""
    from backend.scraper import _merge_event_country

    raw = RawEvent(
        title="ETH Meetup Toronto",
        url="https://luma.com/e/x",
        source="luma",
        country="Canada",
        province_state="Ontario",
    )
    cls = {"country": "USA", "relevance_score": 8}
    assert _merge_event_country(raw, cls) == "Canada"


def test_merge_event_country_prefers_usa_when_gemini_says_canada():
    """Symmetric: parsed US state should not be overwritten by the model."""
    from backend.scraper import _merge_event_country

    raw = RawEvent(
        title="Hackathon",
        url="https://devpost.com/x",
        source="devpost",
        country="USA",
        province_state="California",
    )
    cls = {"country": "Canada"}
    assert _merge_event_country(raw, cls) == "USA"


def test_merge_event_country_luma_canada_without_province_still_wins_vs_usa():
    from backend.scraper import _merge_event_country

    raw = RawEvent(
        title="Web3 Night",
        url="https://luma.com/e/y",
        source="luma",
        country="Canada",
        province_state="",
    )
    cls = {"country": "USA"}
    assert _merge_event_country(raw, cls) == "Canada"


def test_upsert_enriches_missing_date_and_vague_location_from_description(db):
    from backend.scraper import _upsert_event

    raw = RawEvent(
        title="Web3 Builders Weekend",
        url="https://example.com/builders-weekend",
        source="search_discovery",
        description=(
            "Hosted in Montreal and happening on March 27th-28th, 2027. "
            "Register now for the event."
        ),
        location="Canada",
        country="Canada",
    )
    scan_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    status, event_id = _upsert_event(db, raw, {}, scan_ts)

    assert status == "inserted"
    assert event_id is not None

    row = db.query(Event).filter(Event.id == event_id).first()
    assert row is not None
    assert row.city == "Montreal"
    assert row.location.startswith("Montreal")
    assert row.start_date is not None
    assert row.start_date.year == 2027
    assert row.start_date.month == 3
    assert row.start_date.day == 27
    assert row.end_date is not None
    assert row.end_date.year == 2027
    assert row.end_date.month == 3
    assert row.end_date.day == 28


def test_upsert_does_not_downgrade_specific_location_or_existing_dates(db):
    from backend.scraper import _upsert_event

    existing_start = datetime(2027, 3, 10)
    existing_end = datetime(2027, 3, 12)
    raw = RawEvent(
        title="ETH Toronto Summit",
        url="https://example.com/eth-toronto",
        source="devpost",
        description=(
            "Hosted in Montreal and happening on March 27th-28th, 2027. "
            "This sentence should not override specific structured fields."
        ),
        location="Toronto, Ontario",
        city="Toronto",
        country="Canada",
        province_state="Ontario",
        start_date=existing_start,
        end_date=existing_end,
    )
    scan_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    status, event_id = _upsert_event(db, raw, {}, scan_ts)

    assert status == "inserted"
    assert event_id is not None

    row = db.query(Event).filter(Event.id == event_id).first()
    assert row is not None
    assert row.city == "Toronto"
    assert row.location == "Toronto, Ontario"
    assert row.start_date == existing_start
    assert row.end_date == existing_end


def test_upsert_does_not_replace_existing_description_with_sparse_text(db):
    from backend.scraper import _upsert_event

    event = Event(
        title="ETH Summit",
        normalized_title="eth summit",
        description="Long existing event description with venue details and schedule.",
        url="https://example.com/summit",
        source="devpost",
        country="Canada",
        is_inperson=True,
        is_online=False,
        has_travel_grant=False,
        priority_score=5.0,
        relevance_score=5.0,
        scraped_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ai_classified=True,
        consecutive_misses=0,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    incoming = RawEvent(
        title="ETH Summit",
        url="https://example.com/summit",
        source="devpost",
        description="TBD",
    )

    scan_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    status, event_id = _upsert_event(db, incoming, {}, scan_ts)

    assert status == "updated"
    assert event_id == event.id
    db.refresh(event)
    assert event.description == "Long existing event description with venue details and schedule."


# ---------------------------------------------------------------------------
# 1. Full successful refresh — inserts new events
# ---------------------------------------------------------------------------


def test_full_refresh_inserts_new_events(db):
    """A healthy scan with new URLs should insert them and report full_refresh."""
    events = [_raw("https://a.com", "ETH Hack")]
    pairs = [_default_cls(events[0])]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=(events, 0, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["publish_status"] == "full_refresh"
    assert result["inserted"] == 1
    assert result["stale_deleted"] == 0

    row = db.query(Event).filter(Event.url == "https://a.com").first()
    assert row is not None
    assert row.title == "ETH Hack"
    assert row.consecutive_misses == 0


# ---------------------------------------------------------------------------
# 2. Full refresh updates raw + AI fields on existing events
# ---------------------------------------------------------------------------


def test_full_refresh_updates_existing_event(db):
    """Events already in the DB should have their fields refreshed, not duplicated."""
    existing = _add_event(db, "https://b.com", title="ETH Toronto Hackathon")

    updated_raw = _raw("https://b.com", title="ETH Toronto Hackathon 2025")
    pairs = [_default_cls(updated_raw, relevance=9, priority=8)]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([updated_raw], 0, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["updated"] == 1
    assert result["inserted"] == 0

    db.expire_all()
    row = db.query(Event).filter(Event.url == "https://b.com").first()
    assert row.title == "ETH Toronto Hackathon 2025"
    assert row.relevance_score == 9
    assert row.consecutive_misses == 0


# ---------------------------------------------------------------------------
# 3. Missing events are deleted after a healthy full scan
# ---------------------------------------------------------------------------


def test_stale_event_deleted_after_healthy_scan(db):
    """Events absent from a healthy scan should be deleted (STALE_MISS_THRESHOLD=1)."""
    _add_event(db, "https://gone.com", source="devpost")

    # Scan returns a different event — "gone.com" is now absent
    new_event = _raw("https://new.com")
    pairs = [_default_cls(new_event)]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([new_event], 0, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["publish_status"] == "full_refresh"
    assert result["stale_deleted"] == 1

    db.expire_all()
    assert db.query(Event).filter(Event.url == "https://gone.com").first() is None
    assert db.query(Event).filter(Event.url == "https://new.com").first() is not None


# ---------------------------------------------------------------------------
# 4. Partial scraper failure blocks destructive deletes
# ---------------------------------------------------------------------------


def test_partial_scraper_failure_blocks_delete(db):
    """When a scraper fails, consecutive_misses must not increment and no deletions happen."""
    _add_event(db, "https://kept.com", source="devpost")

    new_event = _raw("https://other.com")
    pairs = [_default_cls(new_event)]

    # failed_count = 1 simulates one scraper erroring out
    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([new_event], 1, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["publish_status"] == "additive_only"
    assert result["stale_deleted"] == 0
    assert "scraper" in (result["delete_blocked_reason"] or "").lower()

    db.expire_all()
    # Original event must still exist, unchanged
    kept = db.query(Event).filter(Event.url == "https://kept.com").first()
    assert kept is not None
    assert kept.consecutive_misses == 0


# ---------------------------------------------------------------------------
# 5. Anomalous count drop blocks destructive deletes
# ---------------------------------------------------------------------------


def test_anomalous_count_drop_blocks_delete(db):
    """If the new candidate count is < 50 % of existing, deletions must be blocked."""
    # Add 10 existing events
    for i in range(10):
        _add_event(db, f"https://event-{i}.com", source="devpost")

    # Scan returns only 2 events (20 % of 10 — below the 50 % threshold)
    new_events = [_raw(f"https://fresh-{i}.com") for i in range(2)]
    pairs = [_default_cls(e) for e in new_events]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=(new_events, 0, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["publish_status"] == "additive_only"
    assert result["stale_deleted"] == 0
    assert result["delete_blocked_reason"] is not None

    db.expire_all()
    # All original 10 events must still be present
    assert db.query(Event).count() == 12  # 10 old + 2 new


# ---------------------------------------------------------------------------
# 6. Tag set is replaced, not appended
# ---------------------------------------------------------------------------


def test_tag_set_is_replaced_on_update(db):
    """Re-scanning an existing event should replace its tags, not accumulate them."""
    existing = _add_event(db, "https://tag-test.com", source="devpost", title="Blockchain Workshop")
    # Seed with an old tag
    old_tag = Tag(name="OldTag")
    db.add(old_tag)
    db.flush()
    db.add(EventTag(event_id=existing.id, tag_id=old_tag.id))
    db.commit()

    updated_raw = _raw("https://tag-test.com", title="Blockchain Workshop")
    pairs = [_default_cls(updated_raw, tags=["hackathon", "Ethereum"])]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([updated_raw], 0, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        _run(run_pipeline())

    db.expire_all()
    row = db.query(Event).filter(Event.url == "https://tag-test.com").first()
    tag_names = {t.name for t in row.tags}
    assert "OldTag" not in tag_names
    assert "hackathon" in tag_names
    assert "Ethereum" in tag_names


# ---------------------------------------------------------------------------
# 7. Orphan tags are cleaned up after stale event deletion
# ---------------------------------------------------------------------------


def test_orphan_tags_removed_after_stale_delete(db):
    """Tags that belong only to deleted events should be removed."""
    existing = _add_event(db, "https://orphan-source.com", source="devpost", title="Web3 Summit")
    orphan_tag = Tag(name="UniqueOrphanTag")
    db.add(orphan_tag)
    db.flush()
    db.add(EventTag(event_id=existing.id, tag_id=orphan_tag.id))
    db.commit()

    new_event = _raw("https://new-event.com")
    pairs = [_default_cls(new_event, tags=["hackathon"])]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([new_event], 0, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["stale_deleted"] == 1
    assert result["orphan_tags_removed"] >= 1

    db.expire_all()
    assert db.query(Tag).filter(Tag.name == "UniqueOrphanTag").first() is None


# ---------------------------------------------------------------------------
# 8. AI cache is reused — Gemini is not called for cached URLs
# ---------------------------------------------------------------------------


def test_ai_cache_reused_on_second_scan(db):
    """A second scan for the same URL should read from AICache, not call Gemini."""
    # Pre-populate the AI cache
    cached_cls = {
        "relevance_score": 8,
        "priority_score": 7,
        "tags": ["hackathon"],
        "has_travel_grant": False,
        "is_inperson": True,
        "country": "Canada",
        "province_state": "Ontario",
        "summary": "Cached summary",
    }
    with _test_db_context() as session:
        import json
        session.add(
            AICache(
                event_url="https://cached.com",
                classification_json=json.dumps(cached_cls),
            )
        )

    event = _raw("https://cached.com")

    # classify_events itself reads from AICache internally; we verify it
    # returns the cached result without hitting Gemini by mocking _call_gemini.
    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([event], 0, {"devpost"}))),
        patch(_PATCHES["db"], _test_db_context),
        patch("backend.ai_filter.get_db_context", _test_db_context),
        patch("backend.ai_filter._call_gemini", new=AsyncMock(return_value=([], False))) as mock_gemini,
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    # Gemini should never have been called because the URL was cached
    mock_gemini.assert_not_called()
    assert result["inserted"] == 1


def test_classify_events_matches_results_by_url_not_position():
    from backend.ai_filter import classify_events

    e1 = RawEvent(title="ETH Alpha", url="https://example.com/a")
    e2 = RawEvent(title="ETH Beta", url="https://example.com/b")

    async def fake_call(_client, _batch):
        # Intentionally reversed output order to verify URL-keyed matching.
        return (
            [
                {"url": "https://example.com/b", "relevance_score": 2, "priority_score": 2},
                {"url": "https://example.com/a", "relevance_score": 9, "priority_score": 9},
            ],
            False,
        )

    with (
        patch("backend.ai_filter.get_db_context", _test_db_context),
        patch("backend.ai_filter._get_client", return_value=object()),
        patch("backend.ai_filter._call_gemini", new=AsyncMock(side_effect=fake_call)),
    ):
        result = _run(classify_events([e1, e2]))

    mapped = {event.url: cls for event, cls in result.pairs}
    assert mapped["https://example.com/a"]["relevance_score"] == 9
    assert mapped["https://example.com/b"]["relevance_score"] == 2


def test_classify_events_uses_canonical_cache_key():
    from backend.ai_filter import classify_events

    canonical_url = canonicalize_event_url("https://example.com/e")
    with _test_db_context() as session:
        session.add(
            AICache(
                event_url=canonical_url,
                classification_json=json.dumps({"url": canonical_url, "relevance_score": 7}),
            )
        )

    event = RawEvent(
        title="ETH Canonical",
        url="https://www.example.com/e/?utm_source=abc#top",
    )
    with (
        patch("backend.ai_filter.get_db_context", _test_db_context),
        patch("backend.ai_filter._get_client", return_value=object()),
        patch("backend.ai_filter._call_gemini", new=AsyncMock(return_value=([], False))) as mock_gemini,
    ):
        result = _run(classify_events([event]))

    mock_gemini.assert_not_called()
    assert result.pairs[0][1]["relevance_score"] == 7


def test_classify_events_falls_back_when_rows_missing_url():
    from backend.ai_filter import classify_events

    e1 = RawEvent(title="ETH One", url="https://example.com/one")
    e2 = RawEvent(title="ETH Two", url="https://example.com/two")

    async def fake_call(_client, _batch):
        # No URL keys, but row count aligns with batch size.
        return (
            [
                {"relevance_score": 4, "priority_score": 5, "tags": ["conference"]},
                {"relevance_score": 8, "priority_score": 9, "tags": ["hackathon"]},
            ],
            False,
        )

    with (
        patch("backend.ai_filter.get_db_context", _test_db_context),
        patch("backend.ai_filter._get_client", return_value=object()),
        patch("backend.ai_filter._call_gemini", new=AsyncMock(side_effect=fake_call)),
    ):
        result = _run(classify_events([e1, e2]))

    mapped = {event.url: cls for event, cls in result.pairs}
    assert mapped["https://example.com/one"]["relevance_score"] == 4
    assert mapped["https://example.com/two"]["relevance_score"] == 8


def test_classify_events_partial_url_match_fills_unmatched_by_order():
    from backend.ai_filter import classify_events

    e1 = RawEvent(title="ETH One", url="https://example.com/one")
    e2 = RawEvent(title="ETH Two", url="https://example.com/two")
    e3 = RawEvent(title="ETH Three", url="https://example.com/three")

    async def fake_call(_client, _batch):
        return (
            [
                {"url": "https://example.com/two", "relevance_score": 6, "priority_score": 6},
                {"relevance_score": 3, "priority_score": 4},
                {"relevance_score": 9, "priority_score": 9},
            ],
            False,
        )

    with (
        patch("backend.ai_filter.get_db_context", _test_db_context),
        patch("backend.ai_filter._get_client", return_value=object()),
        patch("backend.ai_filter._call_gemini", new=AsyncMock(side_effect=fake_call)),
    ):
        result = _run(classify_events([e1, e2, e3]))

    mapped = {event.url: cls for event, cls in result.pairs}
    # URL-keyed match still wins for the directly keyed event.
    assert mapped["https://example.com/two"]["relevance_score"] == 6
    # Other events are deterministically filled instead of being dropped.
    assert mapped["https://example.com/one"] != {}
    assert mapped["https://example.com/three"] != {}


# ---------------------------------------------------------------------------
# 9. Stale events from non-scraped sources are never touched
# ---------------------------------------------------------------------------


def test_stale_detection_scoped_to_scraped_sources(db):
    """Events from sources not included in the active scan must never be deleted."""
    _add_event(db, "https://luma-event.com", source="luma")

    new_event = _raw("https://devpost-event.com", source="devpost")
    pairs = [_default_cls(new_event)]

    # Only "devpost" layer runs; "luma" events must be untouched
    with (
        patch(
            _PATCHES["scrapers"],
            new=AsyncMock(return_value=([new_event], 0, {"devpost"})),
        ),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["stale_deleted"] == 0

    db.expire_all()
    luma_row = db.query(Event).filter(Event.url == "https://luma-event.com").first()
    assert luma_row is not None
    assert luma_row.consecutive_misses == 0


# ---------------------------------------------------------------------------
# 10. force_full_refresh bypasses anomalous-drop when candidates >= min floor
# ---------------------------------------------------------------------------


def test_force_full_refresh_bypasses_drop_threshold(db):
    """
    force_full_refresh=True with a small-but-non-zero candidate set should
    unlock full_refresh status and allow stale deletions even when candidate
    count is below ANOMALOUS_DROP_THRESHOLD.
    """
    for i in range(10):
        _add_event(db, f"https://old-{i}.com", source="devpost")

    # Only 2 events survive the harsher filter — normally triggers additive_only
    new_events = [_raw(f"https://fresh-{i}.com") for i in range(2)]
    pairs = [_default_cls(e) for e in new_events]

    import backend.scraper as scraper_mod

    original_threshold = scraper_mod.ANOMALOUS_DROP_THRESHOLD
    original_min = scraper_mod.FULL_REFRESH_MIN_CANDIDATES
    try:
        scraper_mod.ANOMALOUS_DROP_THRESHOLD = 0.50  # default
        scraper_mod.FULL_REFRESH_MIN_CANDIDATES = 1   # floor: at least 1

        with (
            patch(_PATCHES["scrapers"], new=AsyncMock(return_value=(new_events, 0, {"devpost"}))),
            patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
            patch(_PATCHES["db"], _test_db_context),
        ):
            from backend.scraper import run_pipeline

            result = _run(run_pipeline(force_full_refresh=True))
    finally:
        scraper_mod.ANOMALOUS_DROP_THRESHOLD = original_threshold
        scraper_mod.FULL_REFRESH_MIN_CANDIDATES = original_min

    assert result["publish_status"] == "full_refresh"
    assert result["force_full_refresh_accepted"] is True
    assert result["stale_deleted"] == 10

    db.expire_all()
    assert db.query(Event).count() == 2


# ---------------------------------------------------------------------------
# 11. force_full_refresh with candidates below min floor is still blocked
# ---------------------------------------------------------------------------


def test_force_full_refresh_blocked_below_min_candidates(db):
    """
    force_full_refresh=True must still be blocked when candidate count is below
    FULL_REFRESH_MIN_CANDIDATES to prevent wiping the DB on an empty scrape.
    """
    for i in range(10):
        _add_event(db, f"https://protected-{i}.com", source="devpost")

    # Scrape returns 0 events — even force should not delete anything
    empty: list = []
    pairs: list = []

    import backend.scraper as scraper_mod

    original_min = scraper_mod.FULL_REFRESH_MIN_CANDIDATES
    try:
        scraper_mod.FULL_REFRESH_MIN_CANDIDATES = 1

        with (
            patch(_PATCHES["scrapers"], new=AsyncMock(return_value=(empty, 0, {"devpost"}))),
            patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
            patch(_PATCHES["db"], _test_db_context),
        ):
            from backend.scraper import run_pipeline

            result = _run(run_pipeline(force_full_refresh=True))
    finally:
        scraper_mod.FULL_REFRESH_MIN_CANDIDATES = original_min

    assert result["publish_status"] == "additive_only"
    assert result["force_full_refresh_accepted"] is False
    assert result["force_full_refresh_rejected_reason"] is not None
    assert "FULL_REFRESH_MIN_CANDIDATES" in (result["delete_blocked_reason"] or "")
    assert result["stale_deleted"] == 0

    db.expire_all()
    assert db.query(Event).count() == 10


# ---------------------------------------------------------------------------
# 12. force_full_refresh with scraper failures is still blocked
# ---------------------------------------------------------------------------


def test_force_full_refresh_blocked_on_scraper_failure(db):
    """
    force_full_refresh=True must not override scraper-failure safety: a failed
    scraper means the candidate set is incomplete and deletes must be blocked.
    """
    for i in range(10):
        _add_event(db, f"https://safe-{i}.com", source="devpost")

    new_events = [_raw(f"https://new-{i}.com") for i in range(2)]
    pairs = [_default_cls(e) for e in new_events]

    with (
        # failed_count=1 simulates one scraper erroring
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=(new_events, 1, {"devpost"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline(force_full_refresh=True))

    assert result["publish_status"] == "additive_only"
    assert result["force_full_refresh_accepted"] is False
    assert result["stale_deleted"] == 0

    db.expire_all()
    assert db.query(Event).count() == 12  # 10 original + 2 inserted


def test_specialized_source_overrides_search_discovery_duplicate(db):
    """
    When a fuzzy duplicate exists, a specialized source should overwrite
    low-quality search_discovery metadata instead of creating a duplicate row.
    """
    existing = Event(
        title="Get Your Tickets",
        normalized_title="ethglobal new york 2026",
        url="https://external-site.example/event/ethglobal-ny",
        source="search_discovery",
        location="SearchSearchSearchFacebookX-twitterYoutubeTelegramLinkedin",
        city="new york city",
        country="USA",
        start_date=datetime(2026, 6, 12),
        is_inperson=True,
        is_online=False,
        has_travel_grant=False,
        priority_score=2.0,
        relevance_score=2.0,
        scraped_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ai_classified=True,
        consecutive_misses=0,
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    incoming = RawEvent(
        title="ETHGlobal New York 2026",
        url="https://ethglobal.com/events/new-york-2026?utm_source=search",
        source="ethglobal",
        location="New York City, United States, New York",
        city="new york city",
        country="USA",
        start_date=datetime(2026, 6, 12),
    )
    pairs = [_default_cls(incoming, relevance=9, priority=9)]

    with (
        patch(_PATCHES["scrapers"], new=AsyncMock(return_value=([incoming], 0, {"ethglobal"}))),
        patch(_PATCHES["ai"], new=AsyncMock(return_value=_cls_result(pairs))),
        patch(_PATCHES["db"], _test_db_context),
    ):
        from backend.scraper import run_pipeline

        result = _run(run_pipeline())

    assert result["inserted"] == 0
    assert result["updated"] == 1

    db.expire_all()
    rows = db.query(Event).all()
    assert len(rows) == 1
    assert rows[0].title == "ETHGlobal New York 2026"
    assert rows[0].source == "ethglobal"
    assert rows[0].url == "https://ethglobal.com/events/new-york-2026"
