import { useState } from "react"

const stripHtml = (str) => {
  if (!str) return str
  return str.replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim()
}

function formatDate(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr)
  return d.toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" })
}

function formatDateRange(start, end) {
  if (!start) return "Date TBD"
  const s = formatDate(start)
  if (!end) return s
  const e = formatDate(end)
  return s === e ? s : `${s} – ${e}`
}

function MapPinIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
      <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  )
}

function CalendarIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  )
}

function ClockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" style={{ flexShrink: 0 }}>
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  )
}

function ArrowIcon() {
  return (
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" style={{ flexShrink: 0 }}>
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  )
}

function scoreColor(score) {
  if (score >= 8) return "var(--score-high)"
  if (score >= 5) return "var(--score-mid)"
  return "var(--score-low)"
}

function ScoreBar({ score }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
      <div style={{
        width: "64px",
        height: "3px",
        background: "rgba(255,255,255,0.08)",
        borderRadius: "999px",
        overflow: "hidden",
      }}>
        <div style={{
          width: `${(score / 10) * 100}%`,
          height: "100%",
          background: "var(--score-grad)",
          borderRadius: "999px",
        }} />
      </div>
      <span style={{
        fontSize: "12px",
        color: scoreColor(score),
        fontWeight: 700,
      }}>
        {score.toFixed(0)}/10
      </span>
    </div>
  )
}

export default function EventCard({ event }) {
  const [hovered, setHovered] = useState(false)
  const [btnHovered, setBtnHovered] = useState(false)

  const score = event.priority_score || 0
  const isFeatured = score >= 8
  const isCanada = event.country === "Canada"
  const isOntario = event.province_state === "Ontario"

  const cardStyle = {
    background: hovered
      ? "linear-gradient(160deg, #242840 0%, #1E2130 100%)"
      : "linear-gradient(160deg, #1E2130 0%, #181C2C 100%)",
    border: isFeatured
      ? "1px solid var(--border-feat)"
      : hovered
        ? "1px solid var(--border-hover)"
        : "1px solid var(--border-card)",
    borderRadius: "20px",
    padding: "22px 24px",
    display: "flex",
    flexDirection: "column",
    gap: 0,
    transition: "transform 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease",
    position: "relative",
    overflow: "hidden",
    transform: hovered ? "translateY(-3px)" : "none",
    boxShadow: isFeatured
      ? "0 0 30px rgba(255,107,0,0.1)"
      : hovered
        ? "0 8px 40px rgba(0,0,0,0.5)"
        : "none",
    animationName: "fadeIn",
    animationDuration: "0.2s",
    animationTimingFunction: "ease-in-out",
  }

  const tags = event.tags || []
  const visibleTags = tags.slice(0, 4)
  const extraTags = tags.length > 4 ? tags.length - 4 : 0

  return (
    <article
      style={cardStyle}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* Subtle top-to-bottom depth overlay */}
      <div style={{
        position: "absolute",
        inset: 0,
        background: "linear-gradient(180deg, rgba(99,120,255,0.04) 0%, transparent 60%)",
        pointerEvents: "none",
        borderRadius: "20px",
      }} />

      {/* Ambient glow top-right */}
      <div style={{
        position: "absolute",
        top: "-40px",
        right: "-40px",
        width: "160px",
        height: "160px",
        background: isFeatured
          ? "radial-gradient(circle, rgba(255,107,0,0.1) 0%, transparent 70%)"
          : "radial-gradient(circle, rgba(99,120,255,0.06) 0%, transparent 70%)",
        pointerEvents: "none",
      }} />

      {/* Ontario accent strip */}
      {isOntario && (
        <div style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: "2px",
          background: "linear-gradient(90deg, #FF6B00, #FF8C00)",
          borderRadius: "20px 20px 0 0",
        }} />
      )}

      {/* Title */}
      <h3 style={{
        fontSize: "15px",
        fontWeight: 700,
        color: "var(--text-primary)",
        lineHeight: 1.4,
        display: "-webkit-box",
        WebkitLineClamp: 2,
        WebkitBoxOrient: "vertical",
        overflow: "hidden",
        marginBottom: "10px",
        position: "relative",
      }}>
        {event.title}
      </h3>

      {/* Metadata: location + date */}
      <div style={{ display: "flex", alignItems: "center", gap: "16px", marginBottom: "10px", position: "relative" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "5px", color: "var(--text-secondary)" }}>
          <MapPinIcon />
          <span style={{ fontSize: "12px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
            {event.location || event.city || event.country || "Location TBD"}
            {event.province_state && event.province_state !== event.city ? `, ${event.province_state}` : ""}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "5px", color: "var(--text-secondary)" }}>
          <CalendarIcon />
          <span style={{ fontSize: "12px", color: "var(--text-secondary)", whiteSpace: "nowrap" }}>
            {formatDateRange(event.start_date, event.end_date)}
          </span>
        </div>
      </div>

      {/* Deadline */}
      {event.deadline && (
        <div style={{ display: "flex", alignItems: "center", gap: "5px", color: "var(--text-muted)", marginBottom: "10px", position: "relative" }}>
          <ClockIcon />
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
            Deadline: {formatDate(event.deadline)}
          </span>
        </div>
      )}

      {/* Description */}
      {event.summary && (
        <p style={{
          fontSize: "13px",
          color: "var(--text-secondary)",
          lineHeight: 1.55,
          display: "-webkit-box",
          WebkitLineClamp: 2,
          WebkitBoxOrient: "vertical",
          overflow: "hidden",
          marginBottom: "14px",
          position: "relative",
        }}>
          {event.summary}
        </p>
      )}

      {/* Badges */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "10px", position: "relative" }}>
        {event.is_inperson && (
          <span style={{
            background: "var(--badge-ip-bg)",
            color: "var(--badge-ip-text)",
            border: "1px solid var(--badge-ip-border)",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 600,
            padding: "3px 10px",
          }}>
            In Person
          </span>
        )}
        {event.is_online && !event.is_inperson && (
          <span style={{
            background: "var(--badge-online-bg)",
            color: "var(--badge-online-text)",
            border: "1px solid var(--badge-online-border)",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 500,
            padding: "3px 10px",
          }}>
            Online
          </span>
        )}
        {event.has_travel_grant && (
          <span style={{
            background: "var(--badge-travel-bg)",
            color: "var(--badge-travel-text)",
            border: "1px solid var(--badge-travel-border)",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 600,
            padding: "3px 10px",
          }}>
            Travel Grant
          </span>
        )}
        {isCanada && (
          <span style={{
            background: "var(--badge-region-bg)",
            color: "var(--badge-region-text)",
            border: "1px solid var(--badge-region-border)",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 500,
            padding: "3px 10px",
          }}>
            {event.province_state || "Canada"}
          </span>
        )}
        {!isCanada && event.country && event.country !== "Online" && (
          <span style={{
            background: "var(--badge-region-bg)",
            color: "var(--badge-region-text)",
            border: "1px solid var(--badge-region-border)",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 500,
            padding: "3px 10px",
          }}>
            {event.country}
          </span>
        )}
        {event.prize_pool && stripHtml(event.prize_pool) && (
          <span style={{
            background: "var(--badge-prize-bg)",
            color: "var(--badge-prize-text)",
            border: "none",
            borderRadius: "999px",
            fontSize: "11px",
            fontWeight: 700,
            padding: "3px 10px",
          }}>
            {stripHtml(event.prize_pool)}
          </span>
        )}
      </div>

      {/* Tags */}
      {tags.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: "5px", marginBottom: "16px", position: "relative" }}>
          {visibleTags.map((tag) => (
            <span
              key={tag.id}
              style={{
                background: "var(--tag-bg)",
                color: "var(--tag-text)",
                border: "1px solid var(--tag-border)",
                borderRadius: "6px",
                fontSize: "11px",
                fontWeight: 500,
                padding: "2px 8px",
              }}
            >
              {tag.name}
            </span>
          ))}
          {extraTags > 0 && (
            <span style={{
              background: "var(--tag-bg)",
              color: "var(--tag-text)",
              border: "1px solid var(--tag-border)",
              borderRadius: "6px",
              fontSize: "11px",
              fontWeight: 500,
              padding: "2px 8px",
            }}>
              +{extraTags}
            </span>
          )}
        </div>
      )}

      {/* Footer */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        paddingTop: "14px",
        borderTop: "1px solid rgba(99,120,255,0.1)",
        marginTop: "auto",
        position: "relative",
      }}>
        {score > 0 ? (
          <ScoreBar score={score} />
        ) : (
          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>Unscored</span>
        )}

        <a
          href={event.url}
          target="_blank"
          rel="noopener noreferrer"
          onMouseEnter={() => setBtnHovered(true)}
          onMouseLeave={() => setBtnHovered(false)}
          style={{
            background: btnHovered
              ? "linear-gradient(135deg, #FF8C00 0%, #FF3D00 100%)"
              : "linear-gradient(135deg, #FF6B00 0%, #E05000 100%)",
            color: "#fff",
            fontSize: "12px",
            fontWeight: 600,
            padding: "7px 16px",
            borderRadius: "10px",
            border: "none",
            cursor: "pointer",
            transition: "all 0.15s ease",
            display: "flex",
            alignItems: "center",
            gap: "6px",
            textDecoration: "none",
            boxShadow: btnHovered
              ? "0 0 18px rgba(255,107,0,0.55)"
              : "0 0 10px rgba(255,107,0,0.3)",
            transform: btnHovered ? "scale(1.03)" : "none",
          }}
        >
          View Event
          <ArrowIcon />
        </a>
      </div>
    </article>
  )
}
