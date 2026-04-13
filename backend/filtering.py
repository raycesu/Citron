"""
Deterministic event filtering applied before any AI classification.
Only events passing this stage are forwarded to Gemini, keeping API costs low.

Gates for blockchain/crypto hackathons and conferences (any chain). Broad tech
terms only count when paired with campus/student/event context in the same text;
Canada/US fit is handled downstream (Gemini + API filters), not here.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

BLOCKCHAIN_KEYWORDS: set[str] = {
    "blockchain", "web3", "crypto", "cryptocurrency", "ethereum", "solana",
    "defi", "smart contract", "smart contracts", "zk", "zero knowledge",
    "zero-knowledge", "layer2", "layer 2", "l2", "nft", "polygon", "avalanche",
    "cardano", "polkadot", "chainlink", "protocol", "dao", "dapp",
    "decentralized", "metamask", "wallet", "token", "dex", "yield farming",
    "staking", "consensus", "validator", "substrate", "near protocol",
    "arbitrum", "optimism", "base chain", "starknet", "scroll", "zksync",
    "web 3", "ipfs", "filecoin", "cosmos", "ibc protocol",
    "bitcoin", "lightning network", "sui", "aptos", "rollup", "validium",
}

HACKATHON_KEYWORDS: set[str] = {
    "hackathon", "hack", "buildathon", "buidlathon", "hacker house",
    "workshop", "conference", "summit", "devcon", "devconnect",
    "bootcamp", "boot camp", "developer grant", "bounty", "sprint",
    "symposium", "colloquium", "student chapter",
}

# Broader tech keywords — only match when EVENT_OR_CAMPUS_ANCHORS appear in the same text
TECH_KEYWORDS: set[str] = {
    "artificial intelligence", "machine learning", "deep learning",
    "large language model", "llm", "generative ai", "gen ai",
    "data science", "data engineering", "cybersecurity", "infosec",
    "open source", "developer", "software engineer", "software development",
    "startup", "fintech", "edtech", "healthtech", "cleantech",
    "university hackathon", "student hackathon", "coding competition",
    "programming contest", "tech event", "tech conference",
    "computer science", "computer vision", "robotics",
    "pitch competition",
}

# Required in title+description when only TECH_KEYWORDS match (not blockchain/hackathon)
EVENT_OR_CAMPUS_ANCHORS: frozenset[str] = frozenset(
    {
        "university",
        "college",
        "campus",
        "student",
        "students",
        "undergraduate",
        "graduate",
        "faculty",
        "hackathon",
        "conference",
        "summit",
        "symposium",
        "workshop",
        "meetup",
    }
)

TRUSTED_SOURCES: set[str] = {
    # Hackathon / blockchain-native platforms — listings skip keyword checks
    "ethglobal.com",
    "devpost.com",
    "devfolio.co",
    "dorahacks.io",
    "encode.club",
    "gitcoin.co",
    "superteam.fun",
    "solana.com",
    "near.org",
    "polkadot.network",
    # NOTE: luma.com intentionally excluded — Luma is a general-purpose
    # event platform and must pass the keyword filter to avoid art/social events.
}

# Luma slugs that are navigation/category/auth pages, not individual events.
LUMA_NON_EVENT_SLUGS: set[str] = {
    "signin", "sign-in", "signup", "sign-up", "login", "logout",
    "explore", "discover", "settings", "profile", "about",
    "pricing", "help", "support", "terms", "privacy",
    "crypto", "web3", "ethereum", "solana", "polygon",
    "ethglobal", "ethcc", "superteam", "0xpolygon",
    "calendar", "home", "search", "notifications",
}

# Titles that are clearly page UI/navigation, not events.
_NON_EVENT_TITLE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^sign\s*in$", re.IGNORECASE),
    re.compile(r"^sign\s*up$", re.IGNORECASE),
    re.compile(r"^log\s*in$", re.IGNORECASE),
    re.compile(r"^log\s*out$", re.IGNORECASE),
    re.compile(r"^explore\s*(events)?$", re.IGNORECASE),
    re.compile(r"^discover\s*(events)?$", re.IGNORECASE),
    re.compile(r"^skip\s+to\s+(main\s+)?content$", re.IGNORECASE),
    re.compile(r"^featured\s+in\s*\w*$", re.IGNORECASE),
    re.compile(r"^(home|menu|navigation|footer|header|sidebar)$", re.IGNORECASE),
    re.compile(r"^(create|submit|post)\s+(an?\s+)?event$", re.IGNORECASE),
    re.compile(r"^(my\s+)?(calendar|events|tickets|profile|settings|account)$", re.IGNORECASE),
    re.compile(r"^(back|next|previous|show\s+more|load\s+more|see\s+all|view\s+all)", re.IGNORECASE),
    re.compile(r"^community(\s+hubs?)?\s*(\(\d+\))?$", re.IGNORECASE),
    re.compile(r"^(meetups|conferences|workshops)\s*(\(\d+\))?$", re.IGNORECASE),
    re.compile(r"^for\s+(organizers|hosts|creators)$", re.IGNORECASE),
    re.compile(r"^cowork\s+sign\s*up", re.IGNORECASE),
]


@dataclass
class RawEvent:
    title: str
    url: str
    description: str = ""
    source: str = ""
    location: str = ""
    city: str = ""
    country: str = ""
    province_state: str = ""
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    deadline: Optional[datetime] = None
    prize_pool: Optional[str] = None
    is_online: bool = False
    is_inperson: bool = True
    has_travel_grant: bool = False
    travel_grant_details: Optional[str] = None
    image_url: Optional[str] = None
    raw_tags: list = field(default_factory=list)


def normalize_title(title: str) -> str:
    """Produce a canonical title string used for duplicate detection."""
    normalized = title.lower()
    normalized = re.sub(r"[^a-z0-9\s]", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def is_trusted_source(url: str) -> bool:
    try:
        domain = urlparse(url).netloc.lower().replace("www.", "")
        return any(trusted in domain for trusted in TRUSTED_SOURCES)
    except Exception:
        return False


def _text_passes_keywords(text: str) -> bool:
    lower = text.lower()
    if any(kw in lower for kw in BLOCKCHAIN_KEYWORDS):
        return True
    if any(kw in lower for kw in HACKATHON_KEYWORDS):
        return True
    if any(kw in lower for kw in TECH_KEYWORDS):
        return any(a in lower for a in EVENT_OR_CAMPUS_ANCHORS)
    return False


def is_valid_luma_url(url: str) -> bool:
    """Return False for Luma URLs that are non-event pages (auth, nav, calendars)."""
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower().replace("www.", "")
        # luma.com is canonical; keep matching the historic short host so old links still get slug checks.
        if not netloc.endswith("luma.com") and netloc != "lu.ma":
            return True
        path = parsed.path.strip("/").lower()
        if not path:
            return False
        first_segment = path.split("/")[0]
        if first_segment in LUMA_NON_EVENT_SLUGS:
            return False
        # Block the top-level /community page (hub listing), but allow
        # individual event URLs nested under /community/group-name/event-xxx
        if first_segment == "community" and len(path.split("/")) < 3:
            return False
        return True
    except Exception:
        return True


def is_valid_event_title(title: str) -> bool:
    """Reject titles that are clearly navigation/UI text, not event names."""
    stripped = title.strip()
    if len(stripped) < 4:
        return False
    return not any(p.match(stripped) for p in _NON_EVENT_TITLE_PATTERNS)


def is_relevant_event(event: RawEvent, trusted_source: bool = False) -> bool:
    """
    Return True if the event should proceed past deterministic gating.
    All events must pass basic sanity checks (valid title, valid URL).
    Trusted sources skip keyword checks; all others need blockchain/hackathon
    keywords, or a tech keyword together with campus/student/event context.
    """
    if not is_valid_event_title(event.title):
        return False
    if not is_valid_luma_url(event.url):
        return False
    if trusted_source:
        return True
    combined = f"{event.title} {event.description}"
    return _text_passes_keywords(combined)


def filter_events(events: list[RawEvent]) -> list[RawEvent]:
    """Apply keyword/trust gate; discard listings unlikely to be blockchain events."""
    return [
        e for e in events if is_relevant_event(e, trusted_source=is_trusted_source(e.url))
    ]


_YEAR_IN_TITLE = re.compile(r"\b(20\d{2})\b")

# Sources that hit generic web search: stale URLs are common, so we apply
# extra rules beyond "unknown date = keep".
_STALE_PRONE_SOURCES = frozenset({"search_discovery"})


def filter_future_events(events: list[RawEvent]) -> list[RawEvent]:
    """Keep events whose start/end is still relevant.

    - Known start in the future, or ongoing (end >= now), is kept.
    - Unknown start_date: kept for most scrapers (structured feeds).
    - Search-discovery only: if there is still no start/end after HTML parsing,
      drop when the title's latest explicit 20xx year is before the current
      calendar year (e.g. "Summit 2024" in 2026).
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current_year = now.year
    kept = []
    for event in events:
        if event.source in _STALE_PRONE_SOURCES:
            if event.start_date is None and event.end_date is None:
                years = [int(y) for y in _YEAR_IN_TITLE.findall(event.title)]
                if years and max(years) < current_year:
                    continue

        if event.start_date is None:
            kept.append(event)
        elif event.start_date >= now:
            kept.append(event)
        elif event.end_date and event.end_date >= now:
            kept.append(event)
    return kept


def deduplicate_raw_events(events: list[RawEvent]) -> list[RawEvent]:
    """Remove intra-batch duplicates by URL and normalised title+date+city."""
    seen_urls: set[str] = set()
    seen_fingerprints: set[tuple] = set()
    result: list[RawEvent] = []

    for event in events:
        if event.url in seen_urls:
            continue

        fingerprint = (
            normalize_title(event.title),
            event.start_date.date() if event.start_date else None,
            (event.city or "").lower().strip(),
        )
        if fingerprint in seen_fingerprints and fingerprint != (
            normalize_title(event.title),
            None,
            "",
        ):
            continue

        seen_urls.add(event.url)
        seen_fingerprints.add(fingerprint)
        result.append(event)

    return result
