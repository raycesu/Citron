const DEFAULT_BASE = import.meta.env.DEV ? "/api" : "/_/backend/api"
const BASE = import.meta.env.VITE_API_BASE || DEFAULT_BASE

async function apiFetch(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, options);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${path} → ${res.status}: ${text}`);
  }
  return res.json();
}

export function fetchEvents(filters = {}) {
  const params = new URLSearchParams();
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.travel_grant) params.set("travel_grant", "true");
  if (filters.country) params.set("country", filters.country);
  if (filters.inperson) params.set("inperson", "true");
  if (filters.province_state) params.set("province_state", filters.province_state);
  if (filters.sort) params.set("sort", filters.sort);
  if (filters.limit) params.set("limit", String(filters.limit));
  if (filters.offset) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return apiFetch(`/events${qs ? `?${qs}` : ""}`);
}

export function fetchEvent(id) {
  return apiFetch(`/events/${id}`);
}

export function fetchStats() {
  return apiFetch("/stats");
}

export function fetchTags() {
  return apiFetch("/tags");
}

export function triggerScrape(layers = null) {
  return apiFetch("/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: layers ? JSON.stringify(layers) : "null",
  });
}
