"""
One-off / maintenance: remove events from deprecated sources and generic listing sites.

Deletes rows where:
  - source is devfolio, dorahacks, or github_lists, OR
  - url contains eventbrite.com, eventbrite.ca, meetup.com, devfolio.co, or dorahacks.io

Also removes matching ai_cache rows and event_tags links.

Run from repo root (so .env is found):
  backend/.venv/bin/python -m backend.purge_unwanted_events
"""
from __future__ import annotations

from sqlalchemy import or_

from backend.database import get_db_context
from backend.models import AICache, Event, EventTag

SCRAPER_SOURCES = ("devfolio", "dorahacks", "github_lists")

URL_SUBSTRINGS = (
    "eventbrite.com",
    "eventbrite.ca",
    "meetup.com",
    "devfolio.co",
    "dorahacks.io",
)


def purge() -> int:
    with get_db_context() as db:
        url_clauses = [Event.url.ilike(f"%{s}%") for s in URL_SUBSTRINGS]
        match = or_(Event.source.in_(SCRAPER_SOURCES), *url_clauses)
        events = db.query(Event).filter(match).all()
        if not events:
            return 0

        ids = [e.id for e in events]
        urls = [e.url for e in events]

        db.query(EventTag).filter(EventTag.event_id.in_(ids)).delete(synchronize_session=False)
        db.query(AICache).filter(AICache.event_url.in_(urls)).delete(synchronize_session=False)
        db.query(Event).filter(Event.id.in_(ids)).delete(synchronize_session=False)

        return len(ids)


if __name__ == "__main__":
    n = purge()
    print(f"Deleted {n} event(s) (and related tags / AI cache rows).")
