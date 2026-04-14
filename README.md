# Citron — US & Canada blockchain events

Citron is a full-stack discovery platform for **blockchain-related conferences and hackathons** in **Canada and the United States**, with emphasis on **university and campus ecosystems**, **student audiences**, and events that offer **travel subsidies, grants, or stipends**. Events may focus on **any chain** (Ethereum, Solana, Bitcoin, Cosmos, etc.); the product is chain-agnostic. Deterministic filtering plus Google Gemini classification surface the most relevant in-person and hybrid listings.

---

## Architecture

```
Scrapers (3 layers, run concurrently)
  └─ Normalise raw events into RawEvent DTOs
       └─ Deterministic keyword filter (blockchain / hackathon / tech keywords)
            └─ Future-only filter (drop past events)
                 └─ Intra-batch deduplication (URL + title+date+city fingerprint)
                      └─ DB URL check (skip already-stored events)
                           └─ AI classification (Gemini, batched 20 events, cached by URL)
                                └─ Persist to SQLite (Event + Tag rows)
                                     └─ FastAPI REST API
                                          └─ React dashboard
```

---

## How the Scraper Pipeline Works

### Overview

Every scrape run triggered manually via `POST /api/scrape` or the **Scan Now** button calls `run_pipeline()` in `backend/scraper.py`. This function:

1. Runs all requested scrapers **concurrently** via `asyncio.gather`
2. Passes all collected events through deterministic filters
3. Deduplicates within the batch and against the database
4. Sends only new, unseen events to Gemini for AI classification (in batches of 20)
5. Persists everything to SQLite

Each scraper inherits from `BaseScraper`, which provides a throttled `_get()` method (2-second delay between requests by default) and a `safe_scrape()` wrapper that prevents any single source failure from breaking the pipeline.

---

### Layer 1 — Major Sources

These are the highest-signal, most reliable sources. All results pass the keyword filter automatically because the entire platform is dedicated to blockchain hackathons and related events.

| Scraper | API / Method | What it fetches |
|---------|-------------|-----------------|
| **Devpost** | `GET https://devpost.com/api/hackathons` (public JSON API) | Paginated hackathon listings — up to 10 pages of `upcoming` + `open` hackathons |
| **ETHGlobal** | HTML scrape of `https://ethglobal.com/events` | Parses event cards from React Server Component HTML; tries `__NEXT_DATA__` JSON first, falls back to CSS selector extraction of date widgets and badge spans |

---

### Layer 2 — Luma

| Scraper | API / Method | What it fetches |
|---------|-------------|-----------------|
| **Luma** | `GET https://api.luma.com/discover/get-paginated-events` (paginated JSON API) + HTML `__NEXT_DATA__` scraping | 5 categories via API (`crypto`, `web3`, `ethereum`, `ai`, `startups`), up to 3 pages each; also scrapes 24 HTML discovery pages on `luma.com` covering Web3/AI categories and Canadian/US city hubs (e.g. Toronto, Calgary, Vancouver, Montreal, NYC, SF, Boston, Seattle, Austin, Chicago, Los Angeles, Denver, Miami, Atlanta, Philadelphia, DC) |

---

### Layer 3 — Search Discovery

| Scraper | API / Method | What it fetches |
|---------|-------------|-----------------|
| **SearchDiscovery** | **Brave Search API** (`https://api.search.brave.com/res/v1/web/search`) if `BRAVE_SEARCH_API_KEY` is set, otherwise **DuckDuckGo HTML** fallback | Runs up to **17** structured queries (`SEARCH_QUERIES[:MAX_QUERIES]` in `search_discovery.py`, e.g. `"site:luma.com blockchain OR web3 hackathon North America 2026"`) capped at 10 results per query; deduplicates discovered URLs and fetches the top **30** (`MAX_EXTRACT_URLS`) concurrently — up to **8** in flight at a time (`EXTRACT_CONCURRENCY`) — extracting title/description/location from OG tags and H1 elements. This scraper uses a **0.3 s** per-request delay (vs. the 2 s global default) since concurrency replaces serial pacing as the politeness mechanism. **Without a Brave key**, DuckDuckGo often serves a bot challenge and returns no URLs — set `BRAVE_SEARCH_API_KEY` for reliable Layer 3. |

---

### Deterministic Filter

Before any event reaches Gemini, it must pass `filter_events()` in `backend/filtering.py`:

- **Every source** — combined **title + description** must match **word- or phrase-level** crypto signals (regex with boundaries), not loose substrings. That avoids false positives like **cryptography** (contains “crypto”), generic **protocol** / **optimism** / **scroll** English, **token** in “love token”, etc.
  - **Examples of matches**: `blockchain`, `web3`, `cryptocurrency`, whole-word `crypto`, `ethereum`, `smart contract`, `defi`, `nft`, `arbitrum`, `solidity`, `on-chain`, `layer 2`, `ERC-20`, `EIP-1559`, and the full list in `backend/filtering.py`
  - **Abbreviations**: standalone `ETH`, `ETHGlobal`, `BTC`, `XRP`
  - **Luma** — extra title patterns drop common social / founder / wellness noise (e.g. love workshops, generic women’s conferences, founder brunches) **unless** strong chain terms still appear in the full copy (so “Women’s Web3 Conference” stays).
- **Title sanity check** — rejects UI artefacts like "Sign In", "Explore Events", "Load More", "My Calendar", etc.
- **Luma URL check** — rejects Luma navigation/auth/category pages (e.g. `luma.com/explore`, `luma.com/crypto` as top-level slugs)
- **Future-only filter** — drops events whose `start_date` is in the past (kept if `end_date` is still in the future, or if date is unknown)

---

### Deduplication

Two layers of deduplication prevent the same event from being stored twice:

1. **Intra-batch** (in memory): checks both URL equality and a `(normalised_title, start_date, city)` fingerprint
2. **Against the DB**: queries existing `Event.url` values before sending anything to Gemini; skips events already in the database
3. **On persist**: a final DB-level check on URL and on `normalised_title + city + start_date`

---

### AI Classification (Gemini)

Only events that are brand new (not in the DB and not cached) are sent to Gemini. The model receives batches of up to 20 events and returns a JSON array with these fields per event:

| Field | Type | Description |
|-------|------|-------------|
| `relevance_score` | int 1–10 | How relevant to blockchain/crypto events for students (any chain; conferences and hackathons) |
| `priority_score` | int 1–10 | Starts at 3; +2 in-person, +2 Canada/USA, -2 online-only, +3 travel grant |
| `has_travel_grant` | bool | Whether travel funding is available |
| `travel_grant_details` | string \| null | Description of grant if present |
| `tags` | array | Subset of: Solana, Ethereum, DeFi, NFT, AI, Web3, hackathon, conference, workshop, grant, beginner-friendly, in-person, online |
| `summary` | string | One-sentence description |
| `is_inperson` | bool | Whether the event is in-person |
| `country` | string | One of: Canada, USA, Online, Other |
| `province_state` | string \| null | e.g. Ontario, California |

Results are **cached in the `AICache` SQLite table** by `event_url` — re-scraped events that are already in the cache never consume API quota.

The current model is `gemini-2.5-flash-lite` (configured via `GEMINI_MODEL` in `.env`).

---

## Project Structure

```
Citron/
├── backend/
│   ├── main.py                  # FastAPI app, all routes
│   ├── server.py                # Vercel entrypoint shim (re-exports app)
│   ├── scraper.py               # Pipeline orchestrator
│   ├── filtering.py             # Deterministic filter + RawEvent DTO
│   ├── ai_filter.py             # Gemini batch classification
│   ├── models.py                # SQLAlchemy ORM models
│   ├── database.py              # DB engine + session management
│   ├── requirements.txt
│   ├── scrapers/
│   │   ├── base.py              # Abstract BaseScraper
│   │   ├── devpost.py           # Devpost JSON API (Layer 1)
│   │   ├── ethglobal.py         # ETHGlobal HTML/JSON (Layer 1)
│   │   ├── luma.py              # Luma API + HTML (Layer 2)
│   │   └── search_discovery.py  # Brave Search / DuckDuckGo (Layer 3)
│   └── tests/
│       ├── test_filtering.py
│       ├── test_api.py
│       └── test_scrapers_fixtures.py
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── api/events.js
│   │   ├── index.css
│   │   └── components/
│   │       ├── Navbar.jsx
│   │       ├── StatsRow.jsx
│   │       ├── FilterBar.jsx
│   │       └── EventCard.jsx
│   ├── package.json
│   ├── vite.config.js
│   └── tailwind.config.js
├── .env
└── README.md
```

---

## Quick Start

### 1. Prerequisites

- Python 3.11+
- Node.js 18+
- A Google Gemini API key (free tier at [aistudio.google.com](https://aistudio.google.com))

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` (this file is **gitignored** — do not commit it):

```env
GEMINI_API_KEY=your_key_here
GEMINI_MODEL=gemini-2.5-flash-lite
BRAVE_SEARCH_API_KEY=your_brave_key_here   # optional; Layer 3 search
DATABASE_URL=sqlite:///./citron.db
```

### 3. Backend setup

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Frontend setup

```bash
cd frontend
npm install
```

### 5. Run in development

**Terminal 1 – Backend:**
```bash
cd Citron
source backend/.venv/bin/activate
uvicorn backend.main:app --reload --port 8000
```

**Terminal 2 – Frontend dev server:**
```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

> **Note:** After changing `.env`, you must restart the backend. `--reload` only watches Python files and will not pick up environment variable changes.

---

## Running Tests

```bash
cd Citron
source backend/.venv/bin/activate
pip install pytest httpx
pytest backend/tests/ -v
```

---

## Deployment (Single Server)

Build the React frontend and serve everything from FastAPI on port 8000:

```bash
# Build frontend
cd frontend && npm run build && cd ..

# Start the backend (serves the built frontend as static files)
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Access at [http://your-server:8000](http://your-server:8000).

---

## Deployment (Vercel)

Citron deploys to Vercel using a standard setup: the Vite frontend is built as a static site and the FastAPI backend runs as a Vercel Python serverless function at `/api`.

### How it works

| Layer | Route | Source |
|-------|-------|--------|
| Frontend (Vite SPA) | `/*` (static files + SPA fallback) | `frontend/` → built to `frontend/dist` |
| Backend (FastAPI) | `/api/*` | `api/index.py` → re-exports `backend/main.py:app` |

The build command and output directory are declared in `vercel.json`; Vercel rewrites `/api/*` to the Python function and falls back to `index.html` for all other routes (SPA routing).

### Required Vercel project settings

1. In your Vercel project → **Settings → General**, set **Framework Preset** to **Other** (the build command in `vercel.json` takes over).
2. Add the following **Environment Variables** in Vercel → **Settings → Environment Variables**:

| Variable | Required | Notes |
|----------|----------|-------|
| `GEMINI_API_KEY` | Yes | Google Gemini key for AI classification |
| `BRAVE_SEARCH_API_KEY` | Recommended | Layer 3 scraping; DuckDuckGo fallback is bot-blocked |
| `DATABASE_URL` | Strongly recommended | Postgres URL (e.g. Neon, Supabase). Without this, Vercel falls back to ephemeral SQLite at `/tmp` — **data is lost on every cold start** |
| `GEMINI_MODEL` | No | Defaults to `gemini-2.5-flash-lite` |
| `USER_REGION` | No | Defaults to `Ontario, Canada` |

### Test locally before deploying

Install the [Vercel CLI](https://vercel.com/docs/cli) (`npm i -g vercel`) then run everything locally:

```bash
vercel dev
```

Verify the backend is reachable:

```bash
curl http://localhost:3000/api/stats
curl http://localhost:3000/api/events
```

Both should return JSON (empty arrays / zero counts on a fresh DB).

### Deploy

```bash
vercel deploy --prod
```

Or push to your connected Git branch — Vercel will build and deploy automatically.

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/events` | List events with filters |
| GET | `/api/events/{id}` | Get a single event |
| GET | `/api/stats` | Dashboard stats |
| GET | `/api/tags` | All tags |
| POST | `/api/scrape` | Trigger manual scrape |

### Query parameters for `/api/events`

| Param | Type | Example |
|-------|------|---------|
| `tag` | string | `Solana` |
| `travel_grant` | bool | `true` |
| `country` | string | `Canada` |
| `inperson` | bool | `true` |
| `province_state` | string | `Ontario` |
| `sort` | string | `priority` \| `soonest` \| `latest_added` \| `relevance` |
| `limit` | int | `50` |
| `offset` | int | `0` |

---

## Manual Scraping

Citron refreshes data only when a scrape is triggered manually.

| Layer | Sources | How it runs |
|-------|---------|-------------|
| Major | Devpost, ETHGlobal | Included when you click **Scan Now** or call `POST /api/scrape` |
| Minor | Luma | Included when you click **Scan Now** or call `POST /api/scrape` |
| Search | Brave Search / DuckDuckGo | Included when you click **Scan Now** or call `POST /api/scrape` |

You can trigger a manual scrape at any time with the **Scan Now** button or via `POST /api/scrape`.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_API_KEY` | — | **Required.** Google Gemini API key |
| `GEMINI_MODEL` | `gemini-2.5-flash-lite` | Gemini model to use |
| `DATABASE_URL` | `sqlite:///./citron.db` | SQLAlchemy database URL |
| `BRAVE_SEARCH_API_KEY` | _(empty)_ | Optional but recommended for Layer 3. Brave bills web search at **$5 per 1,000 requests** and applies **$5 in free credits each month** (~1,000 web searches). Citron issues **17** Brave requests per discovery run (`MAX_QUERIES` in `search_discovery.py`). Total Brave usage now depends entirely on how often you manually trigger `POST /api/scrape` or use **Scan Now**. Without a key, DuckDuckGo fallback is unreliable for bots. |
| `ANOMALOUS_DROP_THRESHOLD` | `0.50` | Safety gate: fraction of existing DB rows the new candidate set must reach before destructive deletes are allowed. Lower only for deliberate migration passes; restore to `0.50` afterwards. |
| `STALE_MISS_THRESHOLD` | `1` | Consecutive healthy full-scan misses before a missing event is deleted. |
| `FULL_REFRESH_MIN_CANDIDATES` | `1` | Absolute floor for `force_full_refresh`: deletes are blocked if the candidate count is below this number regardless of other flags (prevents wiping on an empty scrape). |

---

## Recovering from Additive-Only Lock (Full-Refresh Migration Runbook)

If the deterministic filter (`backend/filtering.py`) was made stricter and the database already contains many events from previous looser scans, the safety gate in `run_pipeline` will keep every subsequent scan in `additive_only` mode because the new candidate set is smaller than 50% of the existing rows.

### Why this happens

```
publish_status = "additive_only"
delete_blocked_reason = "candidate count (N) is below 50% of existing (M)"
```

### Recommended approach: one-time forced scan

This approach preserves the DB until the deliberate migration pass and then cleans it up in a single controlled run.

**Step 1 — Deploy the current code** (if you haven't already).

**Step 2 — On Vercel, add a temporary environment variable:**

In Vercel → Project → Settings → Environment Variables, add for the **Production** environment only:

```
FULL_REFRESH_MIN_CANDIDATES = 1   ← already the default, leave unless you changed it
```

No `ANOMALOUS_DROP_THRESHOLD` change is needed — the `force_full_refresh` API flag bypasses the threshold in code.

**Step 3 — Trigger one forced scan** via curl (from your terminal):

```bash
curl -X POST https://your-vercel-app.vercel.app/api/scrape \
  -H "Content-Type: application/json" \
  -d '{"force_full_refresh": true}'
```

Check the JSON response:

```json
{
  "status": "ok",
  "detail": {
    "publish_status": "full_refresh",
    "force_full_refresh_accepted": true,
    "stale_deleted": 42,
    "inserted": 5,
    ...
  }
}
```

- `publish_status: "full_refresh"` — deletes were unlocked.
- `force_full_refresh_accepted: true` — the override was used.
- `stale_deleted` — events that no longer pass the filter and were cleaned up.

If you see `force_full_refresh_rejected_reason` in the response, the safety floor was hit (empty scrape). Re-run — it may be a transient scraper issue.

**Step 4 — Verify the result** in the Citron UI. Click Scan Now (normal mode, no force) and confirm the badge shows "Refreshed" or "Partial scan" depending on your new threshold. The forced run is a one-off; subsequent normal scans use the standard 50% gate.

**Step 5 — No cleanup needed.** There is nothing to revert: the `force_full_refresh` flag must be explicitly sent in each API call body and defaults to `false`. Normal Scan Now clicks in the UI never set it.

### Tuning the threshold permanently (alternative)

If your filter will permanently yield fewer results than before, lower the threshold to match the new steady-state rather than using force mode every time:

In Vercel → Environment Variables:
```
ANOMALOUS_DROP_THRESHOLD = 0.20   ← example: require only 20% overlap
```

Redeploy (or let Vercel pick it up on the next serverless cold start). Now normal scans will pass the gate without needing `force_full_refresh`.

### Before you push to GitHub

- **Commit `.env.example`**, never `.env`. The repo root `.gitignore` ignores `.env` and `*.db`.
- **Rotate any API key** that has ever been committed, pasted in a ticket, or shared in chat — treat it as exposed and create a new key in each provider’s dashboard, then update your local `.env` only.
- **Do not upload `citron.db`** if it contains data you consider private.
- For **GitHub Actions** later, store keys as [encrypted secrets](https://docs.github.com/en/actions/security-guides/using-secrets-in-github-actions), not in the workflow file.

---

## Cost Controls

- Only events passing the deterministic keyword filter reach Gemini
- Gemini outputs are cached by `event_url` — re-scraped events are never re-classified. Changing the Gemini system prompt does **not** retroactively update existing rows; clear the AI cache or re-ingest URLs if you need new classification rules applied.
- Batching 20 events per request minimises API round-trips
- Brave Search: see `BRAVE_SEARCH_API_KEY` above — your monthly usage depends on how often you run manual discovery scrapes (verify current terms on [Brave Search API pricing](https://api-dashboard.search.brave.com/documentation/pricing))
- Browser automation (Playwright) is isolated behind explicit fallback interfaces and currently unused by default

---

## Future Features

The codebase is structured to support:

- Email / Telegram / Discord event alerts
- User accounts and bookmarking
- Calendar export (ICS)
- Personalised ranking based on user location, optional per-user chain emphasis (the catalog stays chain-agnostic), and student status
- PostgreSQL migration (swap `DATABASE_URL`)
