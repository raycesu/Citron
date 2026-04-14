const BASE = import.meta.env.VITE_API_BASE || "/api"

export class ApiRequestError extends Error {
  constructor(message, { status, path, cause } = {}) {
    super(message)
    this.name = "ApiRequestError"
    this.status = status
    this.path = path
    if (cause) this.cause = cause
  }
}

function buildNetworkHint() {
  if (!import.meta.env.DEV) {
    return ""
  }
  return " From the Citron repo root, run: uvicorn backend.main:app --reload --port 8000"
}

async function apiFetch(path, options = {}) {
  let res
  try {
    res = await fetch(`${BASE}${path}`, options)
  } catch (err) {
    throw new ApiRequestError(`Could not reach the API.${buildNetworkHint()}`, {
      path,
      cause: err,
    })
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    const snippet = text ? `: ${text.slice(0, 200)}` : ""
    throw new ApiRequestError(`API ${path} → HTTP ${res.status}${snippet}`, {
      status: res.status,
      path,
    })
  }
  return res.json()
}

export function fetchEvents(filters = {}) {
  const params = new URLSearchParams()
  if (filters.tag) params.set("tag", filters.tag)
  if (filters.travel_grant) params.set("travel_grant", "true")
  if (filters.country) params.set("country", filters.country)
  if (filters.inperson) params.set("inperson", "true")
  if (filters.province_state) params.set("province_state", filters.province_state)
  if (filters.sort) params.set("sort", filters.sort)
  if (filters.limit) params.set("limit", String(filters.limit))
  if (filters.offset) params.set("offset", String(filters.offset))
  const qs = params.toString()
  return apiFetch(`/events${qs ? `?${qs}` : ""}`)
}

export function fetchEvent(id) {
  return apiFetch(`/events/${id}`)
}

export function fetchStats() {
  return apiFetch("/stats")
}

export function fetchTags() {
  return apiFetch("/tags")
}

export function triggerScrape({ layers = null, forceFullRefresh = false } = {}) {
  const body = {}
  if (layers) body.layers = layers
  if (forceFullRefresh) body.force_full_refresh = true
  return apiFetch("/scrape", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
}
