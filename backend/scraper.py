"""
Pipeline orchestrator — snapshot + reconcile model.

Flow:
  run_scrapers() → filter → dedupe → classify (cache-first) → reconcile(upsert + prune)

Each full scan builds a complete candidate set and atomically reconciles the
database against it.  Destructive deletes are gated behind safety checks so a
partial or anomalous scrape never wipes valid data.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from backend.ai_filter import classify_events, gemini_rate_limit_active_for_quota_day
from backend.database import get_db_context
from backend.filtering import (
    RawEvent,
    deduplicate_raw_events,
    filter_events,
    filter_future_events,
    normalize_title,
)
from backend.models import Event, EventTag, Tag
from backend.scrapers.devpost import DevpostScraper
from backend.scrapers.ethglobal import ETHGlobalScraper
from backend.scrapers.luma import LumaScraper
from backend.scrapers.search_discovery import SearchDiscoveryScraper

logger = logging.getLogger(__name__)

# Province names produced by `_infer_country_province` / Luma — used to keep
# Canada when Gemini mislabels US-centric copy as USA.
_CANADIAN_PROVINCES = frozenset(
    {
        "Ontario",
        "British Columbia",
        "Alberta",
        "Quebec",
        "Nova Scotia",
        "New Brunswick",
        "Manitoba",
        "Saskatchewan",
    }
)


def _merge_event_country(raw: RawEvent, cls: dict) -> str:
    """
    Merge Gemini `country` with scraper geography.

    The model often infers USA from English descriptions even when structured
    scraper fields place the event in Canada. Prefer confident scraper signals
    (Luma geo, Canadian province) over a conflicting AI label.
    """
    if not cls:
        return raw.country or ""

    ai = (cls.get("country") or "").strip()
    raw_c = (raw.country or "").strip()
    if not ai:
        return raw_c

    raw_prov = (raw.province_state or "").strip()
    is_canadian_province = raw_prov in _CANADIAN_PROVINCES

    if ai == "Online":
        if raw.is_online:
            return "Online"
        if raw_c in ("Canada", "USA"):
            return raw_c

    # Both say Canada vs USA: trust scraper when we parsed a province/state
    if raw_c in ("Canada", "USA") and ai in ("Canada", "USA") and raw_c != ai:
        if raw_prov:
            return raw_c

    if raw_c == "Canada" and ai in ("USA", "Other"):
        if raw.source == "luma" or is_canadian_province:
            return "Canada"

    if raw_c == "USA" and ai == "Canada" and raw.source == "luma":
        return "USA"

    return ai


MAJOR_SCRAPERS = [DevpostScraper, ETHGlobalScraper]
MINOR_SCRAPERS = [LumaScraper]
SEARCH_SCRAPERS = [SearchDiscoveryScraper]

# Block destructive deletes when the candidate count falls below this fraction
# of the existing row count for the same sources.
ANOMALOUS_DROP_THRESHOLD = 0.50

# Delete a DB row after it has been absent from this many consecutive healthy
# full scans.  1 means delete on the first clean miss.
STALE_MISS_THRESHOLD = 1

# String caps aligned with backend.models.Event (Postgres enforces VARCHAR limits).
_STR_TITLE = 500
_STR_URL = 1000
_STR_SOURCE = 100
_STR_CITY = 200
_STR_COUNTRY = 100
_STR_PROVINCE = 100
_STR_PRIZE_POOL = 200
_STR_TAG = 100


def _clamp_str(value: Optional[str], max_len: int) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    if len(s) <= max_len:
        return s
    return s[:max_len].rstrip()


def _clamp_optional_str(value: Optional[str], max_len: int) -> Optional[str]:
    if value is None:
        return None
    s = _clamp_str(value, max_len)
    return s or None


_last_scrape_at: datetime | None = None


def get_last_scrape_at() -> datetime | None:
    return _last_scrape_at


async def run_scrapers(
    layers: list[str] | None = None,
) -> tuple[list[RawEvent], int, set[str]]:
    """
    Run scrapers concurrently for the requested layers.
    Returns (events, num_failed_scrapers, scraped_source_names).
    """
    all_layers: dict[str, list] = {
        "major": MAJOR_SCRAPERS,
        "minor": MINOR_SCRAPERS,
        "search": SEARCH_SCRAPERS,
    }

    if layers is None:
        layers = list(all_layers.keys())

    scrapers_to_run: list = []
    for layer in layers:
        scrapers_to_run.extend(all_layers.get(layer, []))

    instances = [cls() for cls in scrapers_to_run]
    results = await asyncio.gather(*[s.safe_scrape() for s in instances])

    failed_count = sum(1 for s in instances if s._had_error)
    scraped_sources = {s.NAME for s in instances}

    raw: list[RawEvent] = []
    for batch in results:
        raw.extend(batch)

    logger.info(
        f"Scrapers collected {len(raw)} raw events "
        f"({failed_count} scraper(s) failed, sources={scraped_sources})"
    )
    return raw, failed_count, scraped_sources


async def run_pipeline(layers: list[str] | None = None) -> dict:
    """
    Full ingestion cycle using snapshot + reconcile.

    All filtered events from this scan are classified (Gemini cache-first) and
    upserted.  Events absent from this scan (within the scraped sources) have
    their consecutive_misses counter incremented; they are deleted only when the
    scan passes both safety gates (no scraper failures, no anomalous count drop).
    """
    global _last_scrape_at

    start = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info(f"Pipeline start – layers={layers}")

    # --- 1. Scrape ---
    raw_events, failed_scraper_count, scraped_sources = await run_scrapers(layers)

    # --- 2. Deterministic filter + future-only ---
    filtered = filter_events(raw_events)
    filtered = filter_future_events(filtered)
    logger.info(f"After deterministic filter: {len(filtered)} events")

    # --- 3. Intra-batch deduplicate ---
    filtered = deduplicate_raw_events(filtered)

    # --- 4. AI classification of the full filtered batch (cache-first) ---
    classified_pairs: list[tuple[RawEvent, dict]] = []
    if filtered:
        try:
            ce_result = await classify_events(filtered)
            classified_pairs = ce_result.pairs
            if not classified_pairs:
                logger.warning("AI returned no results – storing events without scores")
                classified_pairs = [(ev, {}) for ev in filtered]
        except EnvironmentError as exc:
            logger.warning(f"Skipping AI classification: {exc}")
            classified_pairs = [(ev, {}) for ev in filtered]
        except Exception as exc:
            logger.error(f"AI classification failed: {exc}")
            classified_pairs = [(ev, {}) for ev in filtered]

    # --- 5. Reconcile: upsert, stale detection, prune ---
    scan_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    inserted = 0
    updated = 0
    stale_deleted = 0
    orphan_tags_removed = 0
    publish_status = "full_refresh"
    delete_blocked_reason: Optional[str] = None

    with get_db_context() as db:
        # Safety gate — compare candidate count to existing rows for same sources
        existing_count = 0
        if scraped_sources:
            existing_count = (
                db.query(func.count(Event.id))
                .filter(Event.source.in_(scraped_sources))
                .scalar()
                or 0
            )

        scrape_healthy = failed_scraper_count == 0
        drop_ok = (
            existing_count == 0
            or len(filtered) >= existing_count * ANOMALOUS_DROP_THRESHOLD
        )

        if not scrape_healthy:
            publish_status = "additive_only"
            delete_blocked_reason = f"{failed_scraper_count} scraper(s) failed"
        elif not drop_ok:
            publish_status = "additive_only"
            delete_blocked_reason = (
                f"candidate count ({len(filtered)}) is below "
                f"{int(ANOMALOUS_DROP_THRESHOLD * 100)}% of existing ({existing_count})"
            )

        can_delete = publish_status == "full_refresh"

        # Upsert every classified event; track which DB row IDs were touched
        seen_event_ids: set[int] = set()
        for raw_ev, classification in classified_pairs:
            try:
                status, ev_id = _upsert_event(db, raw_ev, classification, scan_ts)
                if ev_id is not None:
                    seen_event_ids.add(ev_id)
                if status == "inserted":
                    inserted += 1
                elif status == "updated":
                    updated += 1
            except Exception as exc:
                logger.error(f"Upsert error for {raw_ev.url}: {exc}")

        # Stale detection — only when the scan is healthy and produced results
        if can_delete and scraped_sources and seen_event_ids:
            to_delete_ids: list[int] = []
            missing_events = (
                db.query(Event)
                .filter(Event.source.in_(scraped_sources))
                .filter(Event.id.notin_(seen_event_ids))
                .all()
            )
            for ev in missing_events:
                new_miss_count = (ev.consecutive_misses or 0) + 1
                if new_miss_count >= STALE_MISS_THRESHOLD:
                    to_delete_ids.append(ev.id)
                    # Expunge so SQLAlchemy does not attempt an ORM UPDATE flush
                    # for this object after the bulk DELETE removes it.
                    db.expunge(ev)
                else:
                    ev.consecutive_misses = new_miss_count

            if to_delete_ids:
                # SQLite does not enforce FK cascades by default; delete links first
                db.query(EventTag).filter(
                    EventTag.event_id.in_(to_delete_ids)
                ).delete(synchronize_session=False)
                db.query(Event).filter(
                    Event.id.in_(to_delete_ids)
                ).delete(synchronize_session=False)
                stale_deleted = len(to_delete_ids)
                orphan_tags_removed = _cleanup_orphan_tags(db)

        logger.info(
            f"Reconcile: inserted={inserted}, updated={updated}, "
            f"stale_deleted={stale_deleted}, status={publish_status}"
        )

        # Capture live stats from the same DB session/instance so the frontend
        # can display accurate counts immediately without a separate API call.
        # This matters on Vercel where each invocation gets its own ephemeral DB.
        _now = datetime.now(timezone.utc).replace(tzinfo=None)
        _week = _now + timedelta(days=7)
        current_stats = {
            "total_events": db.query(func.count(Event.id)).scalar() or 0,
            "travel_grants": db.query(func.count(Event.id)).filter(Event.has_travel_grant.is_(True)).scalar() or 0,
            "in_person_events": db.query(func.count(Event.id)).filter(Event.is_inperson.is_(True)).scalar() or 0,
            "canada_us_events": db.query(func.count(Event.id)).filter(Event.country.in_(["Canada", "USA"])).scalar() or 0,
            "events_next_7_days": db.query(func.count(Event.id)).filter(
                and_(Event.start_date >= _now, Event.start_date <= _week)
            ).scalar() or 0,
        }

    _last_scrape_at = datetime.now(timezone.utc).replace(tzinfo=None)
    elapsed = (_last_scrape_at - start).total_seconds()

    summary = {
        "scraped": len(raw_events),
        "after_filter": len(filtered),
        "scrapers_failed": failed_scraper_count,
        "inserted": inserted,
        "updated": updated,
        "stale_deleted": stale_deleted,
        "orphan_tags_removed": orphan_tags_removed,
        "publish_status": publish_status,
        "delete_blocked_reason": delete_blocked_reason,
        "elapsed_seconds": round(elapsed, 1),
        "scraped_at": _last_scrape_at.isoformat(),
        "gemini_rate_limited": gemini_rate_limit_active_for_quota_day(),
        "current_stats": current_stats,
    }
    logger.info(f"Pipeline complete: {summary}")
    return summary


def _upsert_event(
    db: Session,
    raw: RawEvent,
    cls: dict,
    scan_ts: datetime,
) -> tuple[str, Optional[int]]:
    """
    Upsert a single event and return (status, event_id).
    status is one of: 'inserted', 'updated', 'skip'.
    'skip' is returned when a fuzzy-matched canonical row is marked seen but
    no new row is inserted (avoids duplicates with different URLs).
    """
    url_db = _clamp_str(raw.url, _STR_URL)
    title_db = _clamp_str(raw.title, _STR_TITLE)
    norm = _clamp_str(normalize_title(title_db), _STR_TITLE)

    existing = db.query(Event).filter(Event.url == url_db).first()

    if not existing:
        # Normalised-title + city + same calendar-day dedupe
        city_lower = (raw.city or "").lower().strip()
        if len(city_lower) > _STR_CITY:
            city_lower = city_lower[:_STR_CITY]
        candidate = (
            db.query(Event)
            .filter(
                Event.normalized_title == norm,
                Event.city == city_lower,
            )
            .first()
        )
        if candidate and candidate.start_date and raw.start_date:
            if candidate.start_date.date() == raw.start_date.date():
                # Mark the canonical row as seen in this scan; skip duplicate insert
                candidate.last_seen_at = scan_ts
                candidate.consecutive_misses = 0
                return "skip", candidate.id

    # Compute derived fields
    relevance = float(cls.get("relevance_score") or 0)
    priority = float(cls.get("priority_score") or 0)
    has_grant = bool(cls.get("has_travel_grant", raw.has_travel_grant))
    country = _merge_event_country(raw, cls) or ""

    if cls.get("is_inperson") is not None:
        is_inperson = bool(cls["is_inperson"])
    elif country == "Online" or raw.is_online:
        is_inperson = False
    else:
        is_inperson = raw.is_inperson

    province_state = _clamp_str(
        cls.get("province_state") or raw.province_state or "", _STR_PROVINCE
    )
    summary = cls.get("summary") or ""
    grant_details = cls.get("travel_grant_details") or raw.travel_grant_details
    ai_tags: list[str] = cls.get("tags") or []
    merged_tags = list(set(raw.raw_tags + ai_tags))

    country_db = _clamp_str(country, _STR_COUNTRY)
    location_db = _clamp_str(raw.location, 10_000)
    city_db = _clamp_str(raw.city, _STR_CITY)
    source_db = _clamp_str(raw.source, _STR_SOURCE)
    prize_db = _clamp_optional_str(raw.prize_pool, _STR_PRIZE_POOL)

    if existing:
        # Refresh raw fields so dates, locations, descriptions stay current
        existing.title = title_db
        existing.normalized_title = norm
        existing.description = raw.description or existing.description
        existing.location = location_db or existing.location
        existing.city = city_db or existing.city
        existing.start_date = raw.start_date or existing.start_date
        existing.end_date = raw.end_date or existing.end_date
        existing.deadline = raw.deadline or existing.deadline
        existing.prize_pool = prize_db if raw.prize_pool else existing.prize_pool
        existing.is_online = raw.is_online
        existing.is_inperson = is_inperson
        existing.has_travel_grant = has_grant
        existing.travel_grant_details = grant_details or existing.travel_grant_details
        # Refresh AI fields
        existing.relevance_score = relevance
        existing.priority_score = priority
        existing.country = country_db or existing.country
        existing.province_state = province_state or existing.province_state
        existing.summary = summary or existing.summary
        existing.ai_classified = bool(cls)
        existing.last_seen_at = scan_ts
        existing.consecutive_misses = 0
        _sync_tags(db, existing, merged_tags)
        return "updated", existing.id

    event = Event(
        title=title_db,
        normalized_title=norm,
        description=raw.description,
        url=url_db,
        summary=summary,
        source=source_db,
        location=location_db,
        city=city_db,
        country=country_db,
        province_state=province_state,
        start_date=raw.start_date,
        end_date=raw.end_date,
        deadline=raw.deadline,
        prize_pool=prize_db,
        is_online=raw.is_online,
        is_inperson=is_inperson,
        has_travel_grant=has_grant,
        travel_grant_details=grant_details,
        relevance_score=relevance,
        priority_score=priority,
        scraped_at=scan_ts,
        ai_classified=bool(cls),
        last_seen_at=scan_ts,
        consecutive_misses=0,
    )
    db.add(event)
    db.flush()
    _sync_tags(db, event, merged_tags)
    return "inserted", event.id


def _sync_tags(db: Session, event: Event, tag_names: list[str]) -> None:
    """
    Replace the event's tag set with exactly tag_names.
    Removes old associations and creates any missing Tag rows.
    """
    # Remove all existing associations for this event
    db.query(EventTag).filter(EventTag.event_id == event.id).delete(
        synchronize_session=False
    )
    for name in tag_names:
        name = _clamp_str(name.strip(), _STR_TAG)
        if not name:
            continue
        tag = db.query(Tag).filter(Tag.name == name).first()
        if not tag:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        db.add(EventTag(event_id=event.id, tag_id=tag.id))


def _cleanup_orphan_tags(db: Session) -> int:
    """Delete Tag rows that have no remaining EventTag associations."""
    orphan_ids = [
        row[0]
        for row in db.query(Tag.id)
        .outerjoin(EventTag, Tag.id == EventTag.tag_id)
        .filter(EventTag.tag_id.is_(None))
        .all()
    ]
    if orphan_ids:
        db.query(Tag).filter(Tag.id.in_(orphan_ids)).delete(synchronize_session=False)
    return len(orphan_ids)
