"""
Citron FastAPI application entry point.
Serves REST API on /api/* and the built React frontend as static files.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

load_dotenv()

from backend.database import create_tables, get_db
from backend.models import Event, EventTag, Tag
from backend.ai_filter import gemini_rate_limit_active_for_quota_day
from backend.scraper import get_last_scrape_at, run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

FRONTEND_BUILD = Path(__file__).parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(title="Citron", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic response schemas
# ---------------------------------------------------------------------------


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: Optional[str] = ""
    url: str
    summary: Optional[str] = ""
    source: Optional[str] = ""
    location: Optional[str] = ""
    city: Optional[str] = ""
    country: Optional[str] = ""
    province_state: Optional[str] = ""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    prize_pool: Optional[str] = None
    is_online: bool = False
    is_inperson: bool = True
    has_travel_grant: bool = False
    travel_grant_details: Optional[str] = None
    relevance_score: float = 0.0
    priority_score: float = 0.0
    scraped_at: Optional[datetime] = None
    ai_classified: bool = False
    tags: list[TagOut] = []


class StatsOut(BaseModel):
    total_events: int
    travel_grants: int
    in_person_events: int
    canada_us_events: int
    events_next_7_days: int
    last_scraped_at: Optional[str] = None
    gemini_rate_limited_today: bool = False


class ScrapeRequest(BaseModel):
    layers: Optional[list[str]] = None
    force_full_refresh: bool = False


class ScrapeResult(BaseModel):
    status: str
    # publish_status: "full_refresh" | "additive_only"
    # inserted, updated, stale_deleted, delete_blocked_reason, etc. are all in detail
    detail: dict


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------


@app.get("/api/events", response_model=list[EventOut])
def list_events(
    tag: Optional[str] = Query(None),
    travel_grant: Optional[bool] = Query(None),
    country: Optional[str] = Query(None),
    inperson: Optional[bool] = Query(None),
    province_state: Optional[str] = Query(None),
    sort: str = Query("priority"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Event)

    if tag:
        tag_row = db.query(Tag).filter(Tag.name == tag).first()
        if tag_row:
            q = q.join(EventTag, Event.id == EventTag.event_id).filter(
                EventTag.tag_id == tag_row.id
            )
        else:
            return []

    if travel_grant is True:
        q = q.filter(Event.has_travel_grant.is_(True))
    if inperson is True:
        q = q.filter(Event.is_inperson.is_(True))
    if country:
        q = q.filter(Event.country == country)
    if province_state:
        q = q.filter(Event.province_state == province_state)

    sort_map = {
        "priority": Event.priority_score.desc(),
        "soonest": Event.start_date.asc(),
        "latest_added": Event.scraped_at.desc(),
        "relevance": Event.relevance_score.desc(),
    }
    order = sort_map.get(sort, Event.priority_score.desc())
    q = q.order_by(order)

    return q.offset(offset).limit(limit).all()


@app.get("/api/events/{event_id}", response_model=EventOut)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/api/stats", response_model=StatsOut)
def get_stats(db: Session = Depends(get_db)):
    total = db.query(func.count(Event.id)).scalar() or 0
    grants = db.query(func.count(Event.id)).filter(Event.has_travel_grant.is_(True)).scalar() or 0
    in_person = db.query(func.count(Event.id)).filter(Event.is_inperson.is_(True)).scalar() or 0
    canada_us = (
        db.query(func.count(Event.id))
        .filter(Event.country.in_(["Canada", "USA"]))
        .scalar()
        or 0
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    week_ahead = now + timedelta(days=7)
    next_7 = (
        db.query(func.count(Event.id))
        .filter(and_(Event.start_date >= now, Event.start_date <= week_ahead))
        .scalar()
        or 0
    )
    last_scrape = get_last_scrape_at()

    return StatsOut(
        total_events=total,
        travel_grants=grants,
        in_person_events=in_person,
        canada_us_events=canada_us,
        events_next_7_days=next_7,
        last_scraped_at=last_scrape.isoformat() if last_scrape else None,
        gemini_rate_limited_today=gemini_rate_limit_active_for_quota_day(),
    )


@app.post("/api/scrape", response_model=ScrapeResult)
async def trigger_scrape(body: ScrapeRequest = ScrapeRequest()):
    """
    Manually trigger a scrape cycle.

    Accepts an optional JSON body:
      {
        "layers": ["major", "minor", "search"],   // omit for all layers
        "force_full_refresh": false               // set true for a one-time
                                                  // migration pass when the
                                                  // deterministic filter was
                                                  // deliberately tightened
      }

    force_full_refresh bypasses the anomalous-drop threshold but is still
    blocked if any scrapers fail or the candidate count is below
    FULL_REFRESH_MIN_CANDIDATES (env var, default 1).
    """
    try:
        result = await run_pipeline(
            layers=body.layers,
            force_full_refresh=body.force_full_refresh,
        )
        return ScrapeResult(status="ok", detail=result)
    except Exception as exc:
        logger.error(f"Manual scrape failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/tags")
def list_tags(db: Session = Depends(get_db)):
    return [{"id": t.id, "name": t.name} for t in db.query(Tag).order_by(Tag.name).all()]


# ---------------------------------------------------------------------------
# Serve React frontend (production)
# ---------------------------------------------------------------------------

if FRONTEND_BUILD.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_BUILD / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        index = FRONTEND_BUILD / "index.html"
        return FileResponse(str(index))
