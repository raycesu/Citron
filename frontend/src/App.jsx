import { useCallback, useEffect, useRef, useState } from "react"
import { ApiRequestError, fetchEvents, fetchStats } from "./api/events"
import EventCard from "./components/EventCard"
import FilterBar from "./components/FilterBar"
import Navbar from "./components/Navbar"
import RateLimitBanner from "./components/RateLimitBanner"
import StatsRow from "./components/StatsRow"

const DEFAULT_FILTERS = {
  tag: null,
  inperson: false,
  travel_grant: false,
  country: null,
  province_state: null,
  sort: "priority",
}

function useDebounce(value, delay) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(t)
  }, [value, delay])
  return debounced
}

function EmptyState({ filtered }) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "96px 0",
        textAlign: "center",
      }}
    >
      <div
        style={{
          width: "56px",
          height: "56px",
          borderRadius: "16px",
          background: "rgba(255,107,0,0.1)",
          border: "1px solid rgba(255,107,0,0.3)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: "16px",
        }}
      >
        <svg
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="rgba(255,140,0,0.8)"
          strokeWidth="1.5"
          aria-hidden="true"
        >
          <circle cx="11" cy="11" r="8" />
          <path d="m21 21-4.35-4.35" />
        </svg>
      </div>
      <h3
        style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary)", marginBottom: "6px" }}
      >
        No events found
      </h3>
      <p
        style={{
          fontSize: "13px",
          color: "var(--text-secondary)",
          maxWidth: "280px",
          lineHeight: 1.6,
        }}
      >
        {filtered
          ? "Try adjusting your filters or click Scan Now to fetch the latest events."
          : "No events in the database yet. Click Scan Now to start discovering."}
      </p>
    </div>
  )
}

function LoadingGrid() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: 9 }, (_, i) => (
        <div
          key={i}
          style={{
            background: "linear-gradient(160deg, #1E2130 0%, #181C2C 100%)",
            border: "1px solid rgba(99,120,255,0.12)",
            borderRadius: "20px",
            padding: "22px 24px",
            animation: "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite",
          }}
        >
          <div
            style={{
              height: "16px",
              background: "rgba(99,120,255,0.08)",
              borderRadius: "6px",
              width: "75%",
              marginBottom: "12px",
            }}
          />
          <div
            style={{
              height: "12px",
              background: "rgba(99,120,255,0.06)",
              borderRadius: "6px",
              width: "50%",
              marginBottom: "8px",
            }}
          />
          <div
            style={{
              height: "12px",
              background: "rgba(99,120,255,0.06)",
              borderRadius: "6px",
              width: "33%",
              marginBottom: "16px",
            }}
          />
          <div
            style={{
              height: "12px",
              background: "rgba(99,120,255,0.06)",
              borderRadius: "6px",
              width: "100%",
              marginBottom: "6px",
            }}
          />
          <div
            style={{
              height: "12px",
              background: "rgba(99,120,255,0.06)",
              borderRadius: "6px",
              width: "80%",
              marginBottom: "16px",
            }}
          />
          <div style={{ display: "flex", gap: "8px", marginBottom: "16px" }}>
            <div
              style={{
                height: "20px",
                background: "rgba(99,120,255,0.06)",
                borderRadius: "999px",
                width: "64px",
              }}
            />
            <div
              style={{
                height: "20px",
                background: "rgba(99,120,255,0.06)",
                borderRadius: "999px",
                width: "80px",
              }}
            />
          </div>
          <div
            style={{
              borderTop: "1px solid rgba(99,120,255,0.08)",
              paddingTop: "14px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div
              style={{
                height: "12px",
                background: "rgba(99,120,255,0.06)",
                borderRadius: "6px",
                width: "96px",
              }}
            />
            <div
              style={{
                height: "28px",
                background: "rgba(99,120,255,0.06)",
                borderRadius: "10px",
                width: "80px",
              }}
            />
          </div>
        </div>
      ))}
    </div>
  )
}

const PAGE_SIZE = 50

export default function App() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [events, setEvents] = useState([])
  const [stats, setStats] = useState(null)
  const [eventsLoading, setEventsLoading] = useState(true)
  const [softRefreshing, setSoftRefreshing] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [statsLoading, setStatsLoading] = useState(true)
  const [statsError, setStatsError] = useState(null)
  const [error, setError] = useState(null)
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)

  const debouncedFilters = useDebounce(filters, 250)
  const abortRef = useRef(null)
  // Tracks whether we have events already rendered so we can use a softer
  // refresh indicator instead of the full skeleton on post-scan reloads.
  const hasEventsRef = useRef(false)

  useEffect(() => {
    hasEventsRef.current = events.length > 0
  }, [events])

  const loadStats = useCallback(async () => {
    setStatsLoading(true)
    setStatsError(null)
    try {
      const data = await fetchStats()
      setStats(data)
    } catch (err) {
      console.error("Stats load failed:", err)
      const message =
        err instanceof ApiRequestError
          ? err.message
          : "Could not load stats. Make sure the backend is running on port 8000."
      setStatsError(message)
    } finally {
      setStatsLoading(false)
    }
  }, [])

  const loadEvents = useCallback(
    async (f, currentOffset = 0, append = false, softRefresh = false) => {
      if (!append) {
        if (abortRef.current) abortRef.current.abort()
        const controller = new AbortController()
        abortRef.current = controller
      }

      if (append) {
        setLoadingMore(true)
      } else {
        setEventsLoading(true)
        setSoftRefreshing(softRefresh)
      }

      setError(null)
      try {
        const data = await fetchEvents({ ...f, limit: PAGE_SIZE, offset: currentOffset })
        setEvents((prev) => (append ? [...prev, ...data] : data))
        setHasMore(data.length === PAGE_SIZE)
        setOffset(currentOffset + data.length)
      } catch (err) {
        if (err.name !== "AbortError") {
          const message =
            err instanceof ApiRequestError
              ? err.message
              : "Failed to load events. Make sure the backend is running on port 8000."
          setError(message)
          console.error(err)
        }
      } finally {
        if (append) {
          setLoadingMore(false)
        } else {
          setEventsLoading(false)
          setSoftRefreshing(false)
        }
      }
    },
    []
  )

  useEffect(() => {
    loadStats()
  }, [loadStats])

  useEffect(() => {
    setOffset(0)
    loadEvents(debouncedFilters, 0, false)
  }, [debouncedFilters, loadEvents])

  const handleLoadMore = useCallback(() => {
    loadEvents(filters, offset, true)
  }, [loadEvents, filters, offset])

  // Called by Navbar after a scan completes (receives the scan detail object).
  // Uses softRefresh so the existing grid stays visible during the reload.
  // On Vercel, each lambda invocation has its own ephemeral SQLite DB, so the
  // stats API call may hit a cold instance with an empty DB and return zeros.
  // To avoid this, the pipeline now embeds a current_stats snapshot taken from
  // the same instance that ran the scan, and we apply it directly here.
  const handleScrapeComplete = useCallback((scanDetail) => {
    if (scanDetail?.current_stats) {
      setStats({
        total_events: scanDetail.current_stats.total_events,
        travel_grants: scanDetail.current_stats.travel_grants,
        in_person_events: scanDetail.current_stats.in_person_events,
        canada_us_events: scanDetail.current_stats.canada_us_events,
        events_next_7_days: scanDetail.current_stats.events_next_7_days,
        last_scraped_at: scanDetail.scraped_at ?? null,
        gemini_rate_limited_today: scanDetail.gemini_rate_limited ?? false,
      })
    } else {
      loadStats()
    }
    setOffset(0)
    loadEvents(filters, 0, false, true)
  }, [loadStats, loadEvents, filters])

  const isFiltered =
    filters.tag !== null ||
    filters.inperson ||
    filters.travel_grant ||
    filters.country !== null ||
    filters.province_state !== null

  // When we have events and a soft-refresh is in progress, show a subtle
  // overlay instead of dropping to the skeleton.
  const isSoftRefreshing = eventsLoading && softRefreshing && hasEventsRef.current

  return (
    <div style={{ minHeight: "100vh" }}>
      <Navbar
        lastScrapedAt={stats?.last_scraped_at}
        onScrapeComplete={handleScrapeComplete}
      />

      {!statsLoading && stats?.gemini_rate_limited_today && <RateLimitBanner />}

      {/* Stats fetch error */}
      {statsError && (
        <div
          style={{
            maxWidth: "1320px",
            margin: "0 auto",
            padding: "10px 24px 0",
          }}
        >
          <div
            style={{
              background: "rgba(220,50,50,0.08)",
              border: "1px solid rgba(220,80,80,0.3)",
              borderRadius: "10px",
              padding: "10px 16px",
              fontSize: "12px",
              color: "rgba(255,150,150,0.95)",
            }}
          >
            {statsError}
          </div>
        </div>
      )}

      <main style={{ maxWidth: "1320px", margin: "0 auto", padding: "0 24px 80px" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "32px", paddingTop: "24px" }}>

          {/* Stats */}
          <StatsRow stats={stats} loading={statsLoading} />

          {/* Filters */}
          <FilterBar filters={filters} onChange={setFilters} />

          {/* Events section */}
          <div>
            {/* Section header */}
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                gap: "10px",
                marginBottom: "16px",
              }}
            >
              <div style={{ display: "flex", alignItems: "baseline", gap: "10px" }}>
                <h2
                  style={{
                    fontSize: "18px",
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    letterSpacing: "-0.02em",
                  }}
                >
                  Events
                </h2>
                {!eventsLoading && (
                  <span style={{ fontSize: "13px", color: "var(--text-secondary)" }}>
                    {events.length} result{events.length !== 1 ? "s" : ""}
                    {filters.inperson ? " · In Person" : ""}
                    {filters.country ? ` · ${filters.country}` : ""}
                  </span>
                )}
                {isSoftRefreshing && (
                  <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>
                    refreshing…
                  </span>
                )}
              </div>

              {isFiltered && (
                <button
                  onClick={() => setFilters(DEFAULT_FILTERS)}
                  style={{
                    fontSize: "12px",
                    color: "var(--text-muted)",
                    background: "none",
                    border: "none",
                    cursor: "pointer",
                    transition: "color 0.15s ease",
                    padding: 0,
                  }}
                  onMouseEnter={(e) => (e.target.style.color = "var(--accent-orange-l)")}
                  onMouseLeave={(e) => (e.target.style.color = "var(--text-muted)")}
                >
                  Reset filters
                </button>
              )}
            </div>

            {/* Error */}
            {error && (
              <div
                style={{
                  background: "rgba(220,50,50,0.08)",
                  border: "1px solid rgba(220,80,80,0.3)",
                  borderRadius: "12px",
                  padding: "14px 18px",
                  fontSize: "13px",
                  color: "rgba(255,150,150,0.95)",
                  marginBottom: "16px",
                }}
              >
                {error}
              </div>
            )}

            {/* Grid */}
            {eventsLoading && !isSoftRefreshing ? (
              <LoadingGrid />
            ) : events.length === 0 && !eventsLoading ? (
              error ? null : <EmptyState filtered={isFiltered} />
            ) : (
              <div
                style={{
                  opacity: isSoftRefreshing ? 0.55 : 1,
                  transition: "opacity 0.2s ease",
                  pointerEvents: isSoftRefreshing ? "none" : "auto",
                }}
              >
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                  {events.map((event) => (
                    <EventCard key={event.id} event={event} />
                  ))}
                </div>

                {hasMore && (
                  <div style={{ display: "flex", justifyContent: "center", paddingTop: "24px" }}>
                    <button
                      onClick={handleLoadMore}
                      disabled={loadingMore}
                      style={{
                        padding: "10px 28px",
                        borderRadius: "12px",
                        background: "var(--bg-surface)",
                        border: "1px solid rgba(99,120,255,0.22)",
                        fontSize: "13px",
                        color: "var(--text-secondary)",
                        cursor: loadingMore ? "not-allowed" : "pointer",
                        opacity: loadingMore ? 0.5 : 1,
                        transition: "all 0.15s ease",
                      }}
                      onMouseEnter={(e) => {
                        if (!loadingMore) {
                          e.target.style.borderColor = "rgba(99,120,255,0.5)"
                          e.target.style.color = "var(--text-primary)"
                          e.target.style.boxShadow = "0 0 12px rgba(99,120,255,0.15)"
                        }
                      }}
                      onMouseLeave={(e) => {
                        e.target.style.borderColor = "rgba(99,120,255,0.22)"
                        e.target.style.color = "var(--text-secondary)"
                        e.target.style.boxShadow = "none"
                      }}
                    >
                      {loadingMore ? "Loading…" : "Load more"}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer
        style={{
          borderTop: "1px solid rgba(99,120,255,0.1)",
          marginTop: "48px",
          padding: "24px",
        }}
      >
        <div
          style={{
            maxWidth: "1320px",
            margin: "0 auto",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            Citron — blockchain conferences &amp; hackathons in the US &amp; Canada, with a lens on
            universities and travel subsidies
          </span>
          <span
            style={{ fontSize: "12px", color: "var(--text-muted)", fontFamily: "monospace" }}
          >
            v1.0.0
          </span>
        </div>
      </footer>
    </div>
  )
}
