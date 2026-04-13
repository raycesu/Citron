"""
Deterministic event filtering applied before any AI classification.
Only events passing this stage are forwarded to Gemini, keeping API costs low.

Gates for blockchain/crypto hackathons and conferences (any chain). Every
listing must show an explicit crypto or chain signal in title + description
(generic “conference”, “summit”, or “fintech” alone is not enough). Canada/US
fit is handled downstream (Gemini + API filters), not here.
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
    "solidity", "vyper", "foundry", "hardhat", "cosmwasm",
    "erc-20", "erc20", "erc-721", "erc721", "eip-",
    "devcon", "devconnect",
}

# Abbreviations / brands not covered by substring keyword checks (word boundaries).
_BLOCKCHAIN_SIGNAL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\beth\b", re.IGNORECASE),
    re.compile(r"\bethglobal\b", re.IGNORECASE),
    re.compile(r"\bbtc\b", re.IGNORECASE),
    re.compile(r"\bxrp\b", re.IGNORECASE),
)

TRUSTED_SOURCES: set[str] = {
    # Hackathon / blockchain-native platforms (domain allowlist for helpers/tests).
    # All listings still pass the same keyword gate as other sources.
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


def _has_blockchain_signal(text: str) -> bool:
    """True if title + description clearly reference crypto, chains, or major ecosystem brands."""
    if not text or not text.strip():
        return False
    lower = text.lower()
    if any(kw in lower for kw in BLOCKCHAIN_KEYWORDS):
        return True
    return any(p.search(text) for p in _BLOCKCHAIN_SIGNAL_PATTERNS)


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


def is_relevant_event(event: RawEvent) -> bool:
    """
    Return True if the event should proceed past deterministic gating.
    Every source must pass the same rules after title/URL sanity checks: the
    combined title + description must include an explicit blockchain/web3 signal
    (see BLOCKCHAIN_KEYWORDS and _BLOCKCHAIN_SIGNAL_PATTERNS).
    """
    if not is_valid_event_title(event.title):
        return False
    if not is_valid_luma_url(event.url):
        return False
    combined = f"{event.title} {event.description}"
    return _has_blockchain_signal(combined)


def filter_events(events: list[RawEvent]) -> list[RawEvent]:
    """Apply keyword gate; discard listings unlikely to be blockchain events."""
    return [e for e in events if is_relevant_event(e)]


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
