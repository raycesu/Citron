"""
Tests for the snapshot + reconcile pipeline introduced by the full-refresh strategy.

All DB interaction is routed through an in-memory SQLite database that shares a
single connection via StaticPool, mirroring the pattern used in test_api.py.
Network calls (scrapers, Gemini) are always mocked.
"""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.ai_filter import ClassifyEventsResult
from backend.filtering import RawEvent
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
