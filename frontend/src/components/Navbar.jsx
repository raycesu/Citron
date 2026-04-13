import { useEffect, useRef, useState } from "react"
import { triggerScrape } from "../api/events"

function getAudioContext() {
  const Ctx = window.AudioContext || window.webkitAudioContext
  return Ctx ? new Ctx() : null
}

function playScanCompleteChime(ctx) {
  if (!ctx || ctx.state !== "running") return
  const t0 = ctx.currentTime
  const master = ctx.createGain()
  master.gain.value = 0.09
  master.connect(ctx.destination)

  function tone(freqHz, start, durationSec) {
    const osc = ctx.createOscillator()
    const env = ctx.createGain()
    osc.type = "sine"
    osc.frequency.value = freqHz
    env.gain.setValueAtTime(0.0001, start)
    env.gain.exponentialRampToValueAtTime(0.2, start + 0.025)
    env.gain.exponentialRampToValueAtTime(0.0001, start + durationSec)
    osc.connect(env)
    env.connect(master)
    osc.start(start)
    osc.stop(start + durationSec + 0.04)
  }

  tone(784, t0, 0.11)
  tone(1046.5, t0 + 0.09, 0.16)
}

/** Returns a short human-readable label + colour for the scan outcome. */
function resolveScanBadge(detail) {
  if (!detail) return { label: "Scan failed", color: "rgba(220,80,80,0.9)" }
  const { publish_status, inserted = 0, updated = 0, stale_deleted = 0 } = detail
  if (publish_status === "full_refresh") {
    const parts = []
    if (inserted > 0) parts.push(`+${inserted}`)
    if (stale_deleted > 0) parts.push(`−${stale_deleted}`)
    const suffix = parts.length ? ` · ${parts.join(" ")}` : ""
    return { label: `Refreshed${suffix}`, color: "rgba(80,200,120,0.9)" }
  }
  if (publish_status === "additive_only") {
    const suffix = inserted > 0 ? ` · +${inserted}` : ""
    return { label: `Partial scan${suffix}`, color: "rgba(255,190,50,0.9)" }
  }
  return { label: "Scan error", color: "rgba(220,80,80,0.9)" }
}

export default function Navbar({ lastScrapedAt, onScrapeComplete }) {
  const [scanning, setScanning] = useState(false)
  const [btnHovered, setBtnHovered] = useState(false)
  const [scanBadge, setScanBadge] = useState(null)
  const audioCtxRef = useRef(null)
  const badgeTimerRef = useRef(null)

  // Clear badge when a new scan starts
  useEffect(() => {
    if (scanning) setScanBadge(null)
  }, [scanning])

  const formattedLastScanned = lastScrapedAt
    ? new Date(lastScrapedAt).toLocaleTimeString("en-CA", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      })
    : null

  async function handleScan() {
    if (scanning) return
    if (!audioCtxRef.current) {
      audioCtxRef.current = getAudioContext()
    }
    const ctx = audioCtxRef.current
    if (ctx?.state === "suspended") {
      await ctx.resume()
    }

    setScanning(true)
    try {
      const result = await triggerScrape()
      playScanCompleteChime(ctx)

      const badge = resolveScanBadge(result?.detail)
      setScanBadge(badge)
      if (badgeTimerRef.current) clearTimeout(badgeTimerRef.current)
      badgeTimerRef.current = setTimeout(() => setScanBadge(null), 7000)

      if (onScrapeComplete) onScrapeComplete(result?.detail)
    } catch (err) {
      console.error("Scan failed:", err)
      setScanBadge(resolveScanBadge(null))
      if (badgeTimerRef.current) clearTimeout(badgeTimerRef.current)
      badgeTimerRef.current = setTimeout(() => setScanBadge(null), 6000)
    } finally {
      setScanning(false)
    }
  }

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 50,
        height: "52px",
        background: "rgba(8,8,8,0.75)",
        backdropFilter: "blur(24px) saturate(180%)",
        WebkitBackdropFilter: "blur(24px) saturate(180%)",
        borderBottom: "1px solid rgba(255,255,255,0.06)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          height: "52px",
          paddingLeft: "24px",
          paddingRight: "24px",
          maxWidth: "1320px",
          margin: "0 auto",
          gap: "12px",
        }}
      >
        {/* LEFT — Logo */}
        <img
          src="/citron-logo.png"
          alt="Citron"
          style={{ height: "38px", width: "auto", flexShrink: 0 }}
        />

        {/* CENTER */}
        <span
          className="hidden md:block"
          style={{
            fontSize: "12px",
            color: "rgba(255,255,255,0.25)",
            letterSpacing: "0.12em",
            textTransform: "uppercase",
          }}
        >
          US &amp; CANADA · CAMPUS · TRAVEL SUBSIDIES
        </span>

        {/* RIGHT — scan status + last-scanned + button */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "10px",
            flexShrink: 0,
          }}
        >
          {/* Scan outcome badge (auto-hides after 7 s) */}
          {scanBadge && (
            <span
              style={{
                fontSize: "11px",
                fontWeight: 500,
                color: scanBadge.color,
                background: "rgba(255,255,255,0.04)",
                border: `1px solid ${scanBadge.color.replace("0.9", "0.3")}`,
                borderRadius: "8px",
                padding: "3px 9px",
                letterSpacing: "0.02em",
                whiteSpace: "nowrap",
                transition: "opacity 0.3s ease",
              }}
            >
              {scanBadge.label}
            </span>
          )}

          {/* Last-scanned timestamp (hidden while badge is showing) */}
          {!scanBadge && formattedLastScanned && (
            <span
              className="hidden sm:block"
              style={{
                fontSize: "11px",
                color: "rgba(255,255,255,0.22)",
                whiteSpace: "nowrap",
              }}
            >
              Last scan {formattedLastScanned}
            </span>
          )}

          {/* Scan Now button */}
          <button
            onClick={handleScan}
            disabled={scanning}
            onMouseEnter={() => setBtnHovered(true)}
            onMouseLeave={() => setBtnHovered(false)}
            aria-label="Scan for new events"
            style={{
              background: "linear-gradient(135deg, #FF7A00 0%, #FF3D00 100%)",
              color: "#fff",
              fontWeight: 600,
              fontSize: "13px",
              padding: "8px 18px",
              borderRadius: "10px",
              border: "none",
              cursor: scanning ? "not-allowed" : "pointer",
              letterSpacing: "0.01em",
              boxShadow:
                btnHovered && !scanning
                  ? "0 0 28px rgba(255,90,0,0.55)"
                  : "0 0 20px rgba(255,90,0,0.35)",
              transform: btnHovered && !scanning ? "translateY(-1px)" : "none",
              transition: "all 0.15s ease",
              opacity: scanning ? 0.6 : 1,
              display: "flex",
              alignItems: "center",
              gap: "7px",
            }}
          >
            {scanning ? (
              <>
                <svg
                  style={{ width: "14px", height: "14px", animation: "spin 1s linear infinite" }}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
                </svg>
                Scanning…
              </>
            ) : (
              <>
                <svg
                  style={{ width: "14px", height: "14px" }}
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  aria-hidden="true"
                >
                  <circle cx="11" cy="11" r="8" />
                  <path d="m21 21-4.35-4.35" />
                </svg>
                Scan Now
              </>
            )}
          </button>
        </div>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to   { transform: rotate(360deg); }
        }
      `}</style>
    </header>
  )
}
