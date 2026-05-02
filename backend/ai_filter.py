"""
AI classification layer using Google Gemini (google-genai SDK).
Events are batched in groups of 20 to minimise API calls.
Results are cached by event URL to avoid repeat charges.
"""
import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from backend.database import get_db_context
from backend.filtering import RawEvent, canonicalize_event_url
from backend.models import AICache

logger = logging.getLogger(__name__)

_GEMINI_QUOTA_TZ = ZoneInfo("America/Los_Angeles")
# Set when the API returns a quota / rate-limit style error; cleared on any successful batch.
_gemini_rate_limit_at_utc: datetime | None = None

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
BATCH_SIZE = 20


@dataclass
class ClassifyEventsResult:
    pairs: list[tuple[RawEvent, dict]]
    hit_rate_limit: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_gemini_rate_limit_error(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.ClientError):
        if getattr(exc, "code", None) == 429:
            return True
        status = getattr(exc, "status", None) or ""
        if isinstance(status, str) and "RESOURCE_EXHAUSTED" in status.upper():
            return True
        msg = (getattr(exc, "message", None) or str(exc)).lower()
        if "quota" in msg and ("exceed" in msg or "exhaust" in msg):
            return True
        if "rate limit" in msg or "too many requests" in msg:
            return True
    return False


def record_gemini_rate_limit_hit() -> None:
    global _gemini_rate_limit_at_utc
    _gemini_rate_limit_at_utc = _utc_now()


def clear_gemini_rate_limit_hit() -> None:
    global _gemini_rate_limit_at_utc
    _gemini_rate_limit_at_utc = None


def gemini_rate_limit_active_for_quota_day() -> bool:
    """
    True if we recorded a rate-limit hit and Google's quota day (Pacific) has not rolled over.
    """
    if _gemini_rate_limit_at_utc is None:
        return False
    now = _utc_now()
    hit = _gemini_rate_limit_at_utc
    if hit.tzinfo is None:
        hit = hit.replace(tzinfo=timezone.utc)
    return hit.astimezone(_GEMINI_QUOTA_TZ).date() == now.astimezone(_GEMINI_QUOTA_TZ).date()


# Cached per event URL in AICache; edits here do not change rows already in the cache.
SYSTEM_PROMPT = """You rank and classify blockchain and cryptocurrency events for a student audience in Canada and the United States. Include hackathons, conferences, summits, and campus symposiums. Events may focus on any chain (Ethereum, Solana, Bitcoin, Cosmos, etc.) — do not penalize relevance for being single-chain or multi-chain if the event is genuinely blockchain/crypto-related. University-hosted or campus-affiliated conferences deserve strong consideration (not only hackathons).

Given a list of events, return a JSON array.
For each event return exactly these fields:
0. url (string): copy the exact URL from the input event so responses can be matched safely.
1. relevance_score (integer 1-10): How relevant to blockchain/crypto students and developers (any chain). Score university/college/campus conferences highly when they are clearly blockchain-focused.
2. priority_score (integer 1-10): Start at 3, then:
   +2 if in-person
   +2 if in Canada or USA
   -2 if online only
   -1 if outside North America
   +3 if travel funding exists (grant, stipend, subsidy, reimbursement, covered travel, sponsored attendance, scholarship for travel, etc.)
   +1 if clearly hosted by or primarily for a university/college/campus audience (conference or hackathon)
   Clamp between 1 and 10.
3. has_travel_grant (boolean): true if any travel support is described (grant, stipend, subsidy, reimbursement, covered travel, sponsored trip, etc.)
4. travel_grant_details (string or null): short quote or paraphrase of what is offered
5. tags (array): select from [Solana, Ethereum, DeFi, NFT, AI, Web3, hackathon, conference, workshop, grant, beginner-friendly, in-person, online]
6. summary (string): one concise sentence description
7. is_inperson (boolean)
8. country (string): one of Canada, USA, Online, Other
9. province_state (string or null): e.g. Ontario, California, British Columbia

When the input already lists city/country (or location text clearly naming a Canadian or US city or province), treat that as ground truth for field 8–9. Do not override Canada with USA because the event title sounds American. Online only when the event is clearly virtual-only with no primary Canadian or US host city.

Return ONLY a valid JSON array — no markdown fences, no explanation."""


def _get_client() -> genai.Client:
    if not GEMINI_API_KEY or GEMINI_API_KEY == "your_key_here":
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Add it to your .env file."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _get_cached(db, event_url: str) -> Optional[dict]:
    row = db.query(AICache).filter(AICache.event_url == event_url).first()
    if row:
        try:
            return json.loads(row.classification_json)
        except json.JSONDecodeError:
            return None
    return None


def _save_cache(db, event_url: str, classification: dict) -> None:
    row = db.query(AICache).filter(AICache.event_url == event_url).first()
    payload = json.dumps(classification)
    if row:
        row.classification_json = payload
    else:
        db.add(AICache(event_url=event_url, classification_json=payload))


def _generate_content_sync(
    client: genai.Client, prompt: str
):
    return client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )


async def _call_gemini(
    client: genai.Client, events: list[RawEvent]
) -> tuple[list[dict], bool]:
    """
    Send one batch to Gemini. Returns (classifications, rate_limited).
    On rate limit, classifications is empty and rate_limited is True.
    """
    payload = [
        {
            "title": e.title,
            "description": (e.description or "")[:600],
            "url": e.url,
            "location": e.location or "",
            "city": e.city or "",
            "country": e.country or "",
            "is_online": e.is_online,
            "is_inperson": e.is_inperson,
            "has_travel_grant": e.has_travel_grant,
        }
        for e in events
    ]

    prompt = f"Classify these {len(events)} events:\n{json.dumps(payload, indent=2)}"
    try:
        response = await asyncio.to_thread(_generate_content_sync, client, prompt)
        clear_gemini_rate_limit_hit()
        result = json.loads(response.text)
        if isinstance(result, list):
            return result, False
        logger.warning("Gemini returned non-list JSON; skipping batch")
        return [], False
    except json.JSONDecodeError as exc:
        logger.error(f"Gemini JSON decode error: {exc}")
        return [], False
    except Exception as exc:
        if _is_gemini_rate_limit_error(exc):
            logger.warning(f"Gemini rate limit / quota: {exc}")
            record_gemini_rate_limit_hit()
            return [], True
        logger.error(f"Gemini error: {exc}")
        return [], False


def _normalize_classifications_by_url(classifications: list[dict]) -> dict[str, dict]:
    by_url: dict[str, dict] = {}
    for item in classifications:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url")
        if not isinstance(raw_url, str):
            continue
        canonical_url = canonicalize_event_url(raw_url)
        if not canonical_url:
            continue
        by_url[canonical_url] = item
    return by_url


def _classification_url_variants(url: str) -> list[str]:
    canonical = canonicalize_event_url(url)
    if not canonical:
        return []

    variants = {canonical}
    if canonical.startswith("https://"):
        variants.add("http://" + canonical[len("https://") :])
    elif canonical.startswith("http://"):
        variants.add("https://" + canonical[len("http://") :])
    return [u for u in variants if u]


def _assign_classifications_to_batch(
    batch: list[RawEvent], classifications: list[dict]
) -> tuple[dict[str, dict], int, int, int]:
    """
    Returns:
      - event.url -> chosen classification
      - url_keyed_matches count
      - fallback_assigned count
      - unassigned count
    """
    assigned: dict[str, dict] = {}
    url_keyed_matches = 0
    fallback_assigned = 0

    # Primary path: URL keyed assignment with lightweight URL variants.
    by_url = _normalize_classifications_by_url(classifications)
    for ev in batch:
        for key in _classification_url_variants(ev.url):
            cls = by_url.get(key)
            if cls:
                assigned[ev.url] = cls
                url_keyed_matches += 1
                break

    if len(assigned) == len(batch):
        return assigned, url_keyed_matches, fallback_assigned, 0

    # Deterministic fallback: assign remaining rows by stable index order.
    unmatched_events = [ev for ev in batch if ev.url not in assigned]
    unmatched_rows: list[tuple[int, dict]] = []
    for idx, row in enumerate(classifications):
        if not isinstance(row, dict):
            continue
        if any(row is matched for matched in assigned.values()):
            continue
        unmatched_rows.append((idx, row))

    for ev, (_, row) in zip(unmatched_events, unmatched_rows):
        assigned[ev.url] = row
        fallback_assigned += 1

    unassigned = max(len(batch) - len(assigned), 0)
    return assigned, url_keyed_matches, fallback_assigned, unassigned


async def classify_events(events: list[RawEvent]) -> ClassifyEventsResult:
    """
    Classify all events with caching.
    Only uncached events consume API quota.
    """
    if not events:
        return ClassifyEventsResult(pairs=[], hit_rate_limit=False)

    client = _get_client()
    results: list[tuple[RawEvent, dict]] = []
    to_classify: list[RawEvent] = []
    cache_updates: list[tuple[str, dict]] = []
    hit_rate_limit = False

    for event in events:
        event.url = canonicalize_event_url(event.url) or event.url

    with get_db_context() as db:
        for event in events:
            cached = _get_cached(db, event.url)
            if cached:
                results.append((event, cached))
            else:
                to_classify.append(event)

        logger.info(
            f"AI classify: {len(results)} cached, {len(to_classify)} new (batches of {BATCH_SIZE})"
        )

    for i in range(0, len(to_classify), BATCH_SIZE):
        batch = to_classify[i : i + BATCH_SIZE]
        classifications, rate_limited = await _call_gemini(client, batch)
        if rate_limited:
            hit_rate_limit = True
            for ev in batch:
                results.append((ev, {}))
            for ev in to_classify[i + BATCH_SIZE :]:
                results.append((ev, {}))
            break
        if not classifications:
            for ev in batch:
                results.append((ev, {}))
            continue

        assigned_by_event_url, url_keyed_matches, fallback_assigned, unassigned = (
            _assign_classifications_to_batch(batch, classifications)
        )

        if unassigned > 0:
            logger.warning(
                "Gemini batch low-confidence assignment: batch_size=%s rows_returned=%s "
                "url_keyed_matches=%s fallback_assigned=%s unassigned=%s",
                len(batch),
                len(classifications),
                url_keyed_matches,
                fallback_assigned,
                unassigned,
            )
        else:
            logger.info(
                "Gemini batch assignment summary: batch_size=%s rows_returned=%s "
                "url_keyed_matches=%s fallback_assigned=%s unassigned=%s",
                len(batch),
                len(classifications),
                url_keyed_matches,
                fallback_assigned,
                unassigned,
            )

        matched_count = 0
        for ev in batch:
            cls = assigned_by_event_url.get(ev.url, {})
            if cls:
                matched_count += 1
                cache_updates.append((ev.url, cls))
            results.append((ev, cls))

        if matched_count < len(batch):
            logger.warning(
                f"Gemini batch partial URL match: matched {matched_count}/{len(batch)} events"
            )

    if cache_updates:
        with get_db_context() as db:
            for event_url, classification in cache_updates:
                _save_cache(db, event_url, classification)

    return ClassifyEventsResult(pairs=results, hit_rate_limit=hit_rate_limit)
