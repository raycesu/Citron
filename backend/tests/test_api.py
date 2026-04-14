"""
Integration-style tests for the FastAPI endpoints using TestClient.
Uses an in-memory SQLite database so no files are created or modified.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import AsyncMock, patch

from backend.database import get_db
from backend.main import app
from backend.models import Base, Event, Tag, EventTag
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Test database fixture
# ---------------------------------------------------------------------------

TEST_DB_URL = "sqlite:///:memory:"

# StaticPool ensures all connections share the same in-memory database
test_engine = create_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client():
    app.dependency_overrides[get_db] = override_get_db
    with patch("backend.main.create_tables"):
        with TestClient(app) as c:
            yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def db():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


def _add_event(db, **kwargs):
    defaults = dict(
        title="Test Hackathon",
        normalized_title="test hackathon",
        url="https://test.com/event",
        source="devpost",
        country="Canada",
        province_state="Ontario",
        is_inperson=True,
        is_online=False,
        has_travel_grant=False,
        priority_score=7.0,
        relevance_score=8.0,
        scraped_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ai_classified=True,
    )
    defaults.update(kwargs)
    event = Event(**defaults)
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


# ---------------------------------------------------------------------------
# Stats endpoint
# ---------------------------------------------------------------------------


def test_stats_empty(client):
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 0


def test_stats_with_event(client, db):
    _add_event(db)
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_events"] == 1
    assert data["in_person_events"] == 1
    assert data["canada_us_events"] == 1


# ---------------------------------------------------------------------------
# Events listing endpoint
# ---------------------------------------------------------------------------


def test_list_events_empty(client):
    resp = client.get("/api/events")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_events_returns_event(client, db):
    _add_event(db)
    resp = client.get("/api/events")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["title"] == "Test Hackathon"


def test_filter_inperson(client, db):
    _add_event(db, url="https://a.com", is_inperson=True)
    _add_event(db, url="https://b.com", is_inperson=False, is_online=True)
    resp = client.get("/api/events?inperson=true")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["is_inperson"] is True


def test_filter_travel_grant(client, db):
    _add_event(db, url="https://c.com", has_travel_grant=True)
    _add_event(db, url="https://d.com", has_travel_grant=False)
    resp = client.get("/api/events?travel_grant=true")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1
    assert events[0]["has_travel_grant"] is True


def test_filter_country(client, db):
    _add_event(db, url="https://e.com", country="Canada")
    _add_event(db, url="https://f.com", country="USA")
    resp = client.get("/api/events?country=Canada")
    assert resp.status_code == 200
    assert all(e["country"] == "Canada" for e in resp.json())


def test_filter_by_tag(client, db):
    event = _add_event(db, url="https://g.com")
    tag = Tag(name="Solana")
    db.add(tag)
    db.flush()
    db.add(EventTag(event_id=event.id, tag_id=tag.id))
    db.commit()

    resp = client.get("/api/events?tag=Solana")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 1


def test_tag_filter_no_match(client, db):
    _add_event(db, url="https://h.com")
    resp = client.get("/api/events?tag=NonExistentTag")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Single event endpoint
# ---------------------------------------------------------------------------


def test_get_event_by_id(client, db):
    event = _add_event(db)
    resp = client.get(f"/api/events/{event.id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == event.id


def test_get_event_not_found(client):
    resp = client.get("/api/events/99999")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tags endpoint
# ---------------------------------------------------------------------------


def test_list_tags_empty(client):
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_tags(client, db):
    db.add(Tag(name="Ethereum"))
    db.add(Tag(name="Solana"))
    db.commit()
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "Ethereum" in names
    assert "Solana" in names


# ---------------------------------------------------------------------------
# Scrape endpoint — request contract
# ---------------------------------------------------------------------------

_PIPELINE_MOCK = "backend.main.run_pipeline"

_FULL_REFRESH_RESULT = {
    "scraped": 5,
    "after_filter": 5,
    "scrapers_failed": 0,
    "inserted": 5,
    "updated": 0,
    "stale_deleted": 0,
    "orphan_tags_removed": 0,
    "publish_status": "full_refresh",
    "delete_blocked_reason": None,
    "force_full_refresh_accepted": False,
    "force_full_refresh_rejected_reason": None,
    "elapsed_seconds": 1.0,
    "scraped_at": datetime.now(timezone.utc).isoformat(),
    "gemini_rate_limited": False,
    "current_stats": {
        "total_events": 5,
        "travel_grants": 0,
        "in_person_events": 5,
        "canada_us_events": 5,
        "events_next_7_days": 0,
    },
}


def test_scrape_default_payload(client):
    """POST /api/scrape with empty body calls run_pipeline with no force flag."""
    with patch(_PIPELINE_MOCK, new=AsyncMock(return_value=_FULL_REFRESH_RESULT)) as mock:
        resp = client.post("/api/scrape", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["detail"]["publish_status"] == "full_refresh"
    mock.assert_awaited_once_with(layers=None, force_full_refresh=False)


def test_scrape_with_force_full_refresh(client):
    """POST /api/scrape with force_full_refresh=true passes flag to pipeline."""
    forced_result = {**_FULL_REFRESH_RESULT, "force_full_refresh_accepted": True}
    with patch(_PIPELINE_MOCK, new=AsyncMock(return_value=forced_result)) as mock:
        resp = client.post("/api/scrape", json={"force_full_refresh": True})
    assert resp.status_code == 200
    assert resp.json()["detail"]["force_full_refresh_accepted"] is True
    mock.assert_awaited_once_with(layers=None, force_full_refresh=True)


def test_scrape_with_layers(client):
    """POST /api/scrape with layers list passes them to pipeline."""
    with patch(_PIPELINE_MOCK, new=AsyncMock(return_value=_FULL_REFRESH_RESULT)) as mock:
        resp = client.post("/api/scrape", json={"layers": ["major"]})
    assert resp.status_code == 200
    mock.assert_awaited_once_with(layers=["major"], force_full_refresh=False)


def test_scrape_additive_only_exposes_reason(client):
    """Additive-only result must surface delete_blocked_reason in response detail."""
    blocked_result = {
        **_FULL_REFRESH_RESULT,
        "publish_status": "additive_only",
        "delete_blocked_reason": "candidate count (2) is below 50% of existing (10)",
        "stale_deleted": 0,
    }
    with patch(_PIPELINE_MOCK, new=AsyncMock(return_value=blocked_result)):
        resp = client.post("/api/scrape", json={})
    assert resp.status_code == 200
    detail = resp.json()["detail"]
    assert detail["publish_status"] == "additive_only"
    assert detail["delete_blocked_reason"] is not None
