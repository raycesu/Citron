"""
ETHGlobal scraper.
The events page at https://ethglobal.com/events is a React Server Component
site. We extract event data from the HTML card structure, parsing dates,
locations, and event types from badge elements.
"""
import calendar
import json
import logging
import re
from datetime import datetime

import httpx
from dateutil import parser as dateparser
from selectolax.parser import HTMLParser

from backend.filtering import RawEvent
from backend.scrapers.base import BaseScraper
from backend.scrapers.devpost import _infer_country_province

logger = logging.getLogger(__name__)

EVENTS_URL = "https://ethglobal.com/events"

MONTH_MAP: dict[str, int] = {}
for _i in range(1, 13):
    _name = calendar.month_name[_i]
    _abbr = calendar.month_abbr[_i]
    MONTH_MAP[_name.lower()] = _i
    MONTH_MAP[_abbr.lower()] = _i

ONLINE_EVENT_TYPES = {"async hackathon", "online hackathon", "online"}


class ETHGlobalScraper(BaseScraper):
    NAME = "ethglobal"
    LAYER = "major"

    async def scrape(self) -> list[RawEvent]:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await self._get(client, EVENTS_URL)
            html = resp.text

        events = self._try_next_data(html)
        if not events:
            events = self._parse_html(html)
        return events

    # ------------------------------------------------------------------
    # __NEXT_DATA__ path (kept for forward-compatibility if they add it back)
    # ------------------------------------------------------------------
    def _try_next_data(self, html: str) -> list[RawEvent]:
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
            props = data.get("props", {}).get("pageProps", {})
            events_data = (
                props.get("events")
                or props.get("upcomingEvents")
                or props.get("allEvents")
                or []
            )
            results = []
            for item in events_data:
                event = self._parse_json_item(item)
                if event:
                    results.append(event)
            return results
        except (json.JSONDecodeError, KeyError) as exc:
            logger.debug(f"ETHGlobal __NEXT_DATA__ parse error: {exc}")
            return []

    def _parse_json_item(self, item: dict) -> RawEvent | None:
        try:
            title = (item.get("name") or item.get("title") or "").strip()
            slug = item.get("slug") or item.get("id") or ""
            url = f"https://ethglobal.com/events/{slug}" if slug else ""
            if not title or not url:
                return None

            location = item.get("location") or item.get("city") or ""
            country, province_state = _infer_country_province(location)

            start_date = _safe_parse(item.get("startDate") or item.get("start_date"))
            end_date = _safe_parse(item.get("endDate") or item.get("end_date"))

            is_online = (
                _is_online_signal(title, location, item.get("format", ""))
            )

            return RawEvent(
                title=title,
                url=url,
                description=item.get("description") or "",
                source=self.NAME,
                location=location,
                city=location.split(",")[0].strip() if location else "",
                country=country,
                province_state=province_state,
                start_date=start_date,
                end_date=end_date,
                is_online=is_online,
                is_inperson=not is_online,
                has_travel_grant=bool(item.get("travelGrant") or item.get("travel_grant")),
            )
        except Exception as exc:
            logger.debug(f"ETHGlobal item parse error: {exc}")
            return None

    # ------------------------------------------------------------------
    # HTML card extraction (primary path)
    # ------------------------------------------------------------------
    def _parse_html(self, html: str) -> list[RawEvent]:
        tree = HTMLParser(html)
        results = []
        now = datetime.now()

        for card in tree.css("a[href*='/events/']"):
            try:
                href = card.attributes.get("href", "")
                if not href or href == "/events":
                    continue
                url = f"https://ethglobal.com{href}" if href.startswith("/") else href

                section = card.css_first("section")
                if not section:
                    continue

                title_node = section.css_first("h2, h3")
                title = title_node.text(strip=True) if title_node else ""
                if not title:
                    continue

                # --- Dates from the white date widget ---
                start_date, end_date = self._extract_dates(section, title, now)

                # --- Location and type from rounded badges ---
                location, event_type = self._extract_badges(section)

                country, province_state = _infer_country_province(location)
                city = location.split(",")[0].strip() if location else ""

                is_online = _is_online_signal(title, location, event_type)

                results.append(
                    RawEvent(
                        title=title,
                        url=url,
                        source=self.NAME,
                        location=location,
                        city=city,
                        country=country,
                        province_state=province_state,
                        start_date=start_date,
                        end_date=end_date,
                        is_online=is_online,
                        is_inperson=not is_online,
                    )
                )
            except Exception:
                continue

        return results

    def _extract_dates(
        self, section, title: str, now: datetime
    ) -> tuple[datetime | None, datetime | None]:
        date_div = section.css_first("div[class*='bg-white'][class*='text-black']")
        if not date_div:
            return None, None

        month_el = date_div.css_first("div.uppercase")
        if not month_el:
            return None, None

        month_text = month_el.text(strip=True)
        day_spans = date_div.css("span")
        days = [s.text(strip=True) for s in day_spans
                if s.text(strip=True) and s.text(strip=True).isdigit()]

        parts = re.split(r"[—–\-]", month_text)
        start_month_str = parts[0].strip()
        end_month_str = parts[-1].strip() if len(parts) > 1 else start_month_str

        start_month = MONTH_MAP.get(start_month_str.lower())
        end_month = MONTH_MAP.get(end_month_str.lower())
        if not start_month:
            return None, None
        if not end_month:
            end_month = start_month

        year = _infer_year(title, start_month, now)

        start_day = int(days[0]) if days else 1
        end_day = int(days[-1]) if len(days) > 1 else start_day

        try:
            start_date = datetime(year, start_month, start_day)
        except ValueError:
            return None, None
        try:
            end_year = year + 1 if end_month < start_month else year
            end_date = datetime(end_year, end_month, end_day)
        except ValueError:
            end_date = start_date

        return start_date, end_date

    def _extract_badges(self, section) -> tuple[str, str]:
        """Return (location, event_type) from rounded-full badge spans."""
        badges = section.css("span[class*='rounded-full']")
        location = ""
        event_type = ""
        for badge in badges:
            text = badge.text(strip=True)
            if not text:
                continue
            lower = text.lower()
            if "hackathon" in lower or "conference" in lower or "summit" in lower:
                event_type = text
            elif "," in text:
                location = text.replace(",", ", ")
        return location, event_type


def _safe_parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return dateparser.parse(str(value), ignoretz=True)
    except Exception:
        return None


def _infer_year(title: str, month: int, now: datetime) -> int:
    year_match = re.search(r"20[2-3]\d", title)
    if year_match:
        return int(year_match.group())
    if month >= now.month:
        return now.year
    return now.year + 1


def _is_online_signal(title: str, location: str, event_type: str) -> bool:
    combined = f"{title} {location}".lower()
    if "online" in combined or "virtual" in combined or "remote" in combined:
        return True
    if event_type.lower() in ONLINE_EVENT_TYPES:
        return True
    return False
