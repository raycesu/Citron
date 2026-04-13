"""
Devpost scraper – uses the public JSON API.
Endpoint: https://devpost.com/api/hackathons
"""
import logging
from datetime import datetime
from typing import Any

import httpx
from dateutil import parser as dateparser

from backend.filtering import RawEvent
from backend.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

API_BASE = "https://devpost.com/api/hackathons"
PAGES_TO_FETCH = 10


class DevpostScraper(BaseScraper):
    NAME = "devpost"
    LAYER = "major"

    async def scrape(self) -> list[RawEvent]:
        events: list[RawEvent] = []
        async with httpx.AsyncClient() as client:
            for page in range(1, PAGES_TO_FETCH + 1):
                try:
                    resp = await self._get(
                        client,
                        API_BASE,
                        params={
                            "challenge_type[]": "hackathon",
                            "open_to[]": "public",
                            "status[]": ["upcoming", "open"],
                            "page": page,
                        },
                    )
                    data = resp.json()
                    hackathons = data.get("hackathons", [])
                    if not hackathons:
                        break
                    for item in hackathons:
                        event = self._parse(item)
                        if event:
                            events.append(event)
                except Exception as exc:
                    logger.warning(f"Devpost page {page} error: {exc}")
                    break
        return events

    def _parse(self, item: dict[str, Any]) -> RawEvent | None:
        try:
            title: str = item.get("title", "").strip()
            url: str = item.get("url", "").strip()
            if not title or not url:
                return None

            location_data = item.get("displayed_location") or {}
            location = location_data.get("location", "")

            # Resolve country from location string heuristically
            country, province_state = _infer_country_province(location)

            start_date = _parse_date(item.get("submission_period_dates", ""))
            prize = item.get("prize_amount", "")
            description = item.get("tagline", "") or ""

            # Detect online/in-person
            is_online = "online" in location.lower() or location.strip() == ""
            is_inperson = not is_online

            return RawEvent(
                title=title,
                url=url,
                description=description,
                source=self.NAME,
                location=location,
                city=_extract_city(location),
                country=country,
                province_state=province_state,
                start_date=start_date,
                prize_pool=str(prize) if prize else None,
                is_online=is_online,
                is_inperson=is_inperson,
            )
        except Exception as exc:
            logger.debug(f"Devpost parse error: {exc}")
            return None


def _parse_date(date_str: str) -> datetime | None:
    if not date_str:
        return None
    try:
        parts = date_str.split(" - ")
        return dateparser.parse(parts[0].strip(), ignoretz=True)
    except Exception:
        return None


def _extract_city(location: str) -> str:
    if not location:
        return ""
    parts = [p.strip() for p in location.split(",")]
    return parts[0] if parts else ""


def _infer_country_province(location: str) -> tuple[str, str]:
    lower = location.lower()
    canadian_provinces = {
        "ontario": "Ontario",
        "british columbia": "British Columbia",
        "alberta": "Alberta",
        "quebec": "Quebec",
        "nova scotia": "Nova Scotia",
        "new brunswick": "New Brunswick",
        "manitoba": "Manitoba",
        "saskatchewan": "Saskatchewan",
        "toronto": "Ontario",
        "vancouver": "British Columbia",
        "montreal": "Quebec",
        "calgary": "Alberta",
        "ottawa": "Ontario",
        "waterloo": "Ontario",
    }
    us_states = {
        "california": "California",
        "new york": "New York",
        "texas": "Texas",
        "massachusetts": "Massachusetts",
        "illinois": "Illinois",
        "washington": "Washington",
        "san francisco": "California",
        "new york city": "New York",
        "nyc": "New York",
        "boston": "Massachusetts",
        "chicago": "Illinois",
        "seattle": "Washington",
        "austin": "Texas",
        "los angeles": "California",
    }

    for key, province in canadian_provinces.items():
        if key in lower:
            return "Canada", province

    for key, state in us_states.items():
        if key in lower:
            return "USA", state

    if "canada" in lower:
        return "Canada", ""
    if ", usa" in lower or "united states" in lower:
        return "USA", ""
    if "online" in lower or not location.strip():
        return "Online", ""

    return "Other", ""
