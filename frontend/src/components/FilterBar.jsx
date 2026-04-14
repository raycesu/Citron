import { useState } from "react"

const TAG_PILLS = [
  { label: "All", value: null },
  { label: "Hackathon", value: "hackathon" },
  { label: "Conference", value: "conference" },
  { label: "Workshop", value: "workshop" },
  { label: "Solana", value: "Solana" },
  { label: "Ethereum", value: "Ethereum" },
  { label: "DeFi", value: "DeFi" },
  { label: "AI", value: "AI" },
]

const REGION_OPTIONS = [
  { label: "All Regions", value: null, province: null },
  { label: "Canada", value: "Canada", province: null },
  { label: "USA", value: "USA", province: null },
  { label: "Ontario Only", value: "Canada", province: "Ontario" },
  { label: "Online", value: "Online", province: null },
]

const SORT_OPTIONS = [
  { label: "Priority", value: "priority" },
  { label: "Soonest", value: "soonest" },
  { label: "Latest Added", value: "latest_added" },
  { label: "Highest Relevance", value: "relevance" },
]

function Toggle({ label, checked, onChange }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        cursor: "pointer",
        background: "none",
        border: "none",
        padding: 0,
      }}
    >
      <div
        style={{
          position: "relative",
          display: "inline-flex",
          flexShrink: 0,
          width: "36px",
          height: "20px",
          borderRadius: "999px",
          background: checked
            ? "linear-gradient(90deg, #FF6B00, #EC4899)"
            : "rgba(99,120,255,0.15)",
          border: checked ? "none" : "1px solid rgba(99,120,255,0.3)",
          transition: "background 0.2s ease",
        }}
      >
        <span
          style={{
            position: "absolute",
            top: "2px",
            left: checked ? "calc(100% - 18px)" : "2px",
            width: "16px",
            height: "16px",
            borderRadius: "50%",
            background: "#fff",
            boxShadow: "0 1px 3px rgba(0,0,0,0.3)",
            transition: "left 0.2s ease",
          }}
        />
      </div>
      <span
        style={{
          fontSize: "13px",
          color: checked ? "var(--text-primary)" : "var(--text-secondary)",
          userSelect: "none",
          transition: "color 0.15s ease",
        }}
      >
        {label}
      </span>
    </button>
  )
}

function PillButton({ label, active, onClick }) {
  const [hovered, setHovered] = useState(false)

  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: active
          ? "linear-gradient(135deg, rgba(255,107,0,0.22) 0%, rgba(236,72,153,0.15) 100%)"
          : hovered
            ? "rgba(99,120,255,0.1)"
            : "var(--bg-surface)",
        border: active
          ? "1px solid rgba(255,107,0,0.65)"
          : hovered
            ? "1px solid rgba(99,120,255,0.5)"
            : "1px solid rgba(99,120,255,0.22)",
        color: active
          ? "#FFBC80"
          : hovered
            ? "var(--text-primary)"
            : "var(--text-secondary)",
        borderRadius: "999px",
        padding: "6px 16px",
        fontSize: "13px",
        fontWeight: active ? 600 : 400,
        cursor: "pointer",
        transition: "all 0.15s ease",
        whiteSpace: "nowrap",
        boxShadow: active
          ? "0 0 14px rgba(255,107,0,0.2)"
          : hovered
            ? "0 0 10px rgba(99,120,255,0.15)"
            : "none",
      }}
    >
      {label}
    </button>
  )
}

export default function FilterBar({ filters, onChange }) {
  function setTag(tag) {
    onChange({ ...filters, tag })
  }

  function setRegion(value) {
    const opt = REGION_OPTIONS.find((o) => o.label === value) || REGION_OPTIONS[0]
    onChange({
      ...filters,
      country: opt.value,
      province_state: opt.province,
    })
  }

  function getRegionLabel() {
    const opt = REGION_OPTIONS.find(
      (o) => o.value === filters.country && o.province === filters.province_state
    )
    return opt ? opt.label : "All Regions"
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "14px",
      }}
    >
      {/* Tag pills */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px" }}>
        {TAG_PILLS.map((pill) => (
          <PillButton
            key={pill.label}
            label={pill.label}
            active={filters.tag === pill.value}
            onClick={() => setTag(pill.value)}
          />
        ))}
      </div>

      {/* Toggles + dropdowns */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "24px" }}>
        <Toggle
          label="In Person Only"
          checked={!!filters.inperson}
          onChange={(v) => onChange({ ...filters, inperson: v })}
        />
        <Toggle
          label="Travel Grant Only"
          checked={!!filters.travel_grant}
          onChange={(v) => onChange({ ...filters, travel_grant: v })}
        />

        <div style={{ display: "flex", alignItems: "center", gap: "8px", marginLeft: "auto", flexWrap: "wrap" }}>
          <select
            value={getRegionLabel()}
            onChange={(e) => setRegion(e.target.value)}
            className="select-dark"
          >
            {REGION_OPTIONS.map((o) => (
              <option key={o.label} value={o.label}>
                {o.label}
              </option>
            ))}
          </select>

          <select
            value={filters.sort || "priority"}
            onChange={(e) => onChange({ ...filters, sort: e.target.value })}
            className="select-dark"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  )
}
