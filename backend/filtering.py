"""
Deterministic event filtering applied before any AI classification.
Only events passing this stage are forwarded to Gemini, keeping API costs low.

Gates for blockchain/crypto hackathons and conferences (any chain). Every
listing must show an explicit crypto or chain signal in title + description
(generic "conference", "summit", or "fintech" alone is not enough). Canada/US
fit is handled downstream (Gemini + API filters), not here.

Two admission paths exist:
  1. Primary: title + description matches a strong blockchain/crypto signal.
  2. Secondary (university leeway): event-type anchor + university/student
     affiliation + at least one chain-adjacent term. Lets campus blockchain
     club events through even when the scraped description is sparse.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

# Word-boundary and phrase patterns only. Plain substring checks are unsafe:
# e.g. "crypto" matches "cryptography", "protocol" matches social/business copy,
# "optimism" / "scroll" are common English, "token" matches "love token", etc.
_BLOCKCHAIN_REGEX_CHUNKS: tuple[str, ...] = (
    r"\bblockchain\b",
    r"\bweb3\b",
    r"\bweb\s*3\b",
    r"\bcryptocurrency\b",
    r"\bcrypto\b",  # whole word only — not "cryptography"
    r"\bethereum\b",
    r"\bsolana\b",
    r"\bbitcoin\b",
    r"\bdefi\b",
    r"\bnft\b",
    r"\bnfts\b",
    r"\bdao\b",
    r"\bdapp\b",
    r"\bdex\b",
    r"\bsmart\s+contracts?\b",
    r"\bzero[\s\-]?knowledge\b",
    r"\bchainlink\b",
    r"\barbitrum\b",
    r"\bstarknet\b",
    r"\bzksync\b",
    r"\bzk\s*sync\b",
    r"\bmetamask\b",
    r"\bavalanche\b",
    r"\bcardano\b",
    r"\bpolkadot\b",
    r"\bpolygon\b",
    r"\bnear\s+protocol\b",
    r"\bfilecoin\b",
    r"\bipfs\b",
    r"\bcosmos\b",
    r"\bibc\s+protocol\b",
    r"\bsui\b",
    r"\baptos\b",
    r"\bsubstrate\b",
    r"\bcosmwasm\b",
    r"\bsolidity\b",
    r"\bvyper\b",
    r"\bfoundry\b",
    r"\bhardhat\b",
    r"\bdevcon\b",
    r"\bdevconnect\b",
    r"\blightning\s+network\b",
    r"\byield\s+farming\b",
    r"\bbase\s+chain\b",
    r"\berc[\s\-]?20\b",
    r"\berc20\b",
    r"\berc[\s\-]?721\b",
    r"\berc721\b",
    r"\beip[\s\-]?\d+",
    r"\bzkevm\b",
    r"\bzk[\s\-]?evm\b",
    r"\brollup\b",
    r"\bvalidium\b",
    r"\blayer[\s\-]?2\b",
    r"\blayer\s*2\b",
    r"\bl2\b",
    r"\bstaking\b",
    r"\btokenomics\b",
    r"\bairdrop\b",
    r"\bon[\s\-]?chain\b",
    r"\bcross[\s\-]?chain\b",
    # --- Expanded ecosystem vocabulary ---
    # "digital asset(s)" is the standard industry term for crypto/blockchain assets
    r"\bdigital\s+assets?\b",
    # DLT / distributed ledger — precise technical term for blockchain infrastructure
    r"\bdistributed\s+ledger\b",
    r"\bdlt\b",
    # "decentralized" in an event context strongly implies blockchain tech
    r"\bdecentralized\b",
    # Canton Network is an enterprise blockchain platform; "canton network" is specific enough
    r"\bcanton\s+network\b",
    # "permissionless" is blockchain-native vocabulary with no common off-chain use
    r"\bpermissionless\b",
    # Consensus mechanisms — unambiguously blockchain context
    r"\bproof[\s\-]of[\s\-](work|stake|authority|history)\b",
    # Asset tokenization is a blockchain-specific use case
    r"\btokenization\b",
)

_BLOCKCHAIN_RES: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE) for p in _BLOCKCHAIN_REGEX_CHUNKS
)

# Strong chain / product terms — used to override Luma social-lifestyle title matches.
# Keep in sync with _BLOCKCHAIN_REGEX_CHUNKS for the most specific signals.
_STRONG_CHAIN_RES: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bblockchain\b",
        r"\bweb3\b",
        r"\bweb\s*3\b",
        r"\bethereum\b",
        r"\bsolana\b",
        r"\bbitcoin\b",
        r"\bdefi\b",
        r"\bnft\b",
        r"\bnfts\b",
        r"\bchainlink\b",
        r"\barbitrum\b",
        r"\bstarknet\b",
        r"\bzksync\b",
        r"\bzk\s*sync\b",
        r"\bmetamask\b",
        r"\bpolkadot\b",
        r"\bavalanche\b",
        r"\bcardano\b",
        r"\bcryptocurrency\b",
        r"\bsmart\s+contracts?\b",
        r"\bzero[\s\-]?knowledge\b",
        r"\bnear\s+protocol\b",
        r"\bbase\s+chain\b",
        r"\blightning\s+network\b",
        r"\byield\s+farming\b",
        r"\berc[\s\-]?\d+",
        r"\beip[\s\-]?\d+",
        r"\bzkevm\b",
        r"\bzk[\s\-]?evm\b",
        r"\bdapp\b",
        r"\bdex\b",
        r"\bdao\b",
        r"\bfilecoin\b",
        r"\bipfs\b",
        r"\bcosmos\b",
        r"\bsolidity\b",
        r"\bfoundry\b",
        r"\bhardhat\b",
        r"\bcosmwasm\b",
        r"\bvyper\b",
        r"\bsubstrate\b",
        r"\baptos\b",
        r"\bsui\b",
        r"\bpolygon\b",
        r"\bon[\s\-]?chain\b",
        r"\bcross[\s\-]?chain\b",
        # Expanded strong signals
        r"\bdigital\s+assets?\b",
        r"\bdistributed\s+ledger\b",
        r"\bdlt\b",
        r"\bdecentralized\b",
        r"\bcanton\s+network\b",
        r"\bpermissionless\b",
        r"\btokenization\b",
    )
)

# Luma lists many social / founder / lifestyle events; drop obvious categories
# unless title+description still contain strong chain terms (e.g. "Women in Web3").
_LUMA_SOCIAL_TITLE_RES: tuple[re.Pattern, ...] = (
    re.compile(r"\blove\s+workshop\b", re.IGNORECASE),
    re.compile(r"\bself[\s\-]*love\b", re.IGNORECASE),
    re.compile(r"\brelationship\s+(coach|workshop|therapy)\b", re.IGNORECASE),
    re.compile(r"\bdating\s+(night|event|mixer|workshop)\b", re.IGNORECASE),
    re.compile(r"\bwomen'?s?\s+(conference|summit|festival|gathering|retreat)\b", re.IGNORECASE),
    re.compile(r"\bwomen\s+conference\b", re.IGNORECASE),
    re.compile(
        r"\bfounders?\s+(breakfast|brunch|dinner|mixer|meetup|meet[\s\-]?up|drinks|coffee|"
        r"hangout|hang[\s\-]?out|social|circle|night|hour|break|event|summit|forum)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bfounders?\s+night\b", re.IGNORECASE),
    re.compile(r"\bstartup\s+(dating|mixer)\b", re.IGNORECASE),
    re.compile(r"\byoga\s", re.IGNORECASE),
    re.compile(r"\bmeditation\s", re.IGNORECASE),
    re.compile(r"\bmanifestation\s", re.IGNORECASE),
    re.compile(r"\bspiritual\s+(retreat|workshop|circle)\b", re.IGNORECASE),
    re.compile(r"\bsound\s*bath\b", re.IGNORECASE),
    re.compile(r"\bwellness\s+retreat\b", re.IGNORECASE),
)

# Abbreviations / brands not covered by the chunk regexes.
_BLOCKCHAIN_SIGNAL_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\beth\b", re.IGNORECASE),
    re.compile(r"\bethglobal\b", re.IGNORECASE),
    re.compile(r"\bbtc\b", re.IGNORECASE),
    re.compile(r"\bxrp\b", re.IGNORECASE),
    # University blockchain org shorthands — specific enough to pass on their own
    re.compile(r"\bb@b\b", re.IGNORECASE),      # Blockchain at Berkeley
    re.compile(r"\bcantor8\b", re.IGNORECASE),  # Cantor8 (Canton Network company)
)

# ---------------------------------------------------------------------------
# University / campus blockchain club leeway (secondary admission path)
# ---------------------------------------------------------------------------

# Event must look like an actual event, not a topic page or newsletter.
_EVENT_TYPE_ANCHOR_RE: re.Pattern = re.compile(
    r"\b(hackathon|hacker[\s\-]house|conference|summit|symposium|workshop|"
    r"bootcamp|boot[\s\-]camp|seminar|forum|competition|challenge|sprint|"
    r"demo[\s\-]day|pitch[\s\-]competition|camp)\b",
    re.IGNORECASE,
)

# University / student affiliation signals.
_UNIVERSITY_AFFILIATION_RE: re.Pattern = re.compile(
    r"\b(university|college|campus|student|undergraduate|graduate|academic|"
    r"ubc|stanford|berkeley|cornell|harvard|yale|princeton|mcmaster|"
    r"waterloo|u\s+of\s+t|uoft|columbia|nyu|ucla|usc|mit|carnegie\s+mellon|"
    r"fordham|georgetown|uc\s+[a-z]+|cal\s+poly|georgia\s+tech)\b",
    re.IGNORECASE,
)

# Chain-adjacent terms: broader than the main signal list but, when combined
# with an event anchor AND a university affiliation, reliably indicate a
# blockchain-focused event rather than generic campus fintech/tech.
_CHAIN_ADJACENT_RES: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bdigital\s+assets?\b",      # industry-standard term for crypto holdings
        r"\bdistributed\s+ledger\b",   # DLT generic
        r"\bdlt\b",
        r"\bdecentralized\b",
        r"\bcanton\b",                 # Canton Network; safe in university+event context
        r"\btokenization\b",
        r"\bdigital\s+currency\b",
        r"\bcrypto\b",                 # already in main signals; belt-and-suspenders here
        r"\bblockchain\b",             # shouldn't be needed but keeps the path consistent
        r"\bweb3\b",
    )
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
    if any(p.search(text) for p in _BLOCKCHAIN_RES):
        return True
    return any(p.search(text) for p in _BLOCKCHAIN_SIGNAL_PATTERNS)


def _has_strong_chain_signal(text: str) -> bool:
    """Subset of signals for Luma social-title overrides (avoids lone generic 'crypto')."""
    if not text or not text.strip():
        return False
    if any(p.search(text) for p in _STRONG_CHAIN_RES):
        return True
    return any(p.search(text) for p in _BLOCKCHAIN_SIGNAL_PATTERNS)


def _has_university_blockchain_context(combined: str) -> bool:
    """
    Secondary admission path for campus-affiliated blockchain events.

    Returns True when all three conditions are met:
      1. A recognisable event-type anchor (hackathon, conference, summit, …)
      2. A university / student affiliation signal
      3. At least one chain-adjacent term from a broader (but still targeted) list

    This allows university blockchain clubs and campus conferences to pass the
    deterministic gate even when the scraped description is sparse, while still
    blocking generic campus tech or fintech events that carry none of the
    chain-adjacent vocabulary.
    """
    if not _EVENT_TYPE_ANCHOR_RE.search(combined):
        return False
    if not _UNIVERSITY_AFFILIATION_RE.search(combined):
        return False
    return any(p.search(combined) for p in _CHAIN_ADJACENT_RES)


def _is_luma_event(event: RawEvent) -> bool:
    if (event.source or "").lower() == "luma":
        return True
    try:
        host = urlparse(event.url).netloc.lower().replace("www.", "")
        return host.endswith("luma.com") or host == "lu.ma"
    except Exception:
        return False


def _luma_social_title_blocked(title: str, combined: str) -> bool:
    """
    True if a Luma listing looks like social/lifestyle/founder noise and lacks
    strong on-topic chain terms in the full copy.
    """
    for p in _LUMA_SOCIAL_TITLE_RES:
        if p.search(title):
            return not _has_strong_chain_signal(combined)
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


def is_relevant_event(event: RawEvent) -> bool:
    """
    Return True if the event should proceed past deterministic gating.

    Primary path: combined title + description must match word/phrase-level
    chain signals (not loose substrings).

    Secondary path (university leeway): event-type anchor + university/student
    affiliation + chain-adjacent term. Passes campus blockchain club events
    even when the scraped description is sparse, without opening the gate to
    generic campus tech or fintech events.

    Luma listings also drop common social/founder spam titles unless strong
    chain terms appear anywhere in the copy.
    """
    if not is_valid_event_title(event.title):
        return False
    if not is_valid_luma_url(event.url):
        return False
    combined = f"{event.title} {event.description}"
    passes_signal = _has_blockchain_signal(combined) or _has_university_blockchain_context(combined)
    if not passes_signal:
        return False
    if _is_luma_event(event) and _luma_social_title_blocked(event.title, combined):
        return False
    return True


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
