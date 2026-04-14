import { useState } from "react"

// Each stat card can have a distinct left-border accent color
const CARD_ACCENTS = [
  "#3B82F6", // Total Events   — blue
  "#F59E0B", // Travel Grants  — amber
  "#FF6B00", // In Person      — orange
  "#8B5CF6", // Canada / US    — purple
  "#22C55E", // This Week      — green (also the "featured" card)
]

function StatCard({ label, value, thisWeek, accentColor }) {
  const [hovered, setHovered] = useState(false)

  const baseStyle = {
    background: thisWeek
      ? "linear-gradient(145deg, #1E2A1A 0%, #1E2130 100%)"
      : "linear-gradient(145deg, #1E2130 0%, #181C2C 100%)",
    border: thisWeek
      ? `1px solid rgba(34,197,94,0.5)`
      : hovered
        ? "1px solid rgba(99,120,255,0.4)"
        : "1px solid rgba(99,120,255,0.15)",
    borderRadius: "16px",
    padding: "20px 22px",
    paddingLeft: "18px",
    transition: "border-color 0.2s ease, transform 0.2s ease, box-shadow 0.2s ease",
    cursor: "default",
    transform: hovered ? "translateY(-2px)" : "none",
    boxShadow: thisWeek
      ? "0 0 24px rgba(34,197,94,0.15)"
      : hovered
        ? "0 4px 24px rgba(0,0,0,0.35)"
        : "none",
    position: "relative",
    overflow: "hidden",
  }

  return (
    <div
      style={baseStyle}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Colored left accent bar */}
      <div style={{
        position: "absolute",
        top: "12px",
        bottom: "12px",
        left: 0,
        width: "3px",
        borderRadius: "0 3px 3px 0",
        background: accentColor,
        opacity: thisWeek || hovered ? 1 : 0.6,
        transition: "opacity 0.2s ease",
      }} />

      <div
        style={{
          fontFamily: "'JetBrains Mono', 'SF Mono', monospace",
          fontSize: "28px",
          fontWeight: 700,
          color: "#F0F0FF",
          letterSpacing: "-0.03em",
          lineHeight: 1,
        }}
      >
        {value ?? "—"}
      </div>
      <div
        style={{
          fontSize: "11px",
          textTransform: "uppercase",
          letterSpacing: "0.1em",
          color: "var(--text-secondary)",
          marginTop: "6px",
        }}
      >
        {label}
      </div>
    </div>
  )
}

export default function StatsRow({ stats, loading }) {
  const items = [
    {
      label: "Total Events",
      value: loading ? "…" : stats?.total_events,
    },
    {
      label: "Travel Grants",
      value: loading ? "…" : stats?.travel_grants,
    },
    {
      label: "In Person",
      value: loading ? "…" : stats?.in_person_events,
    },
    {
      label: "Canada / US",
      value: loading ? "…" : stats?.canada_us_events,
    },
    {
      label: "This Week",
      value: loading ? "…" : stats?.events_next_7_days,
      thisWeek: true,
    },
  ]

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3 w-full">
      {items.map((item, i) => (
        <StatCard key={item.label} {...item} accentColor={CARD_ACCENTS[i]} />
      ))}
    </div>
  )
}
