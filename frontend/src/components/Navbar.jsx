import { useEffect, useRef, useState } from "react"
import { triggerScrape } from "../api/events"

let fallbackChimeDataUrl = null

function getAudioContext() {
  const Ctx = window.AudioContext || window.webkitAudioContext
  return Ctx ? new Ctx() : null
}

function encodeWavDataUrl(samples, sampleRate) {
  const bytesPerSample = 2
  const blockAlign = bytesPerSample
  const dataSize = samples.length * bytesPerSample
  const buffer = new ArrayBuffer(44 + dataSize)
  const view = new DataView(buffer)

  function writeString(offset, value) {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index))
    }
  }

  writeString(0, "RIFF")
  view.setUint32(4, 36 + dataSize, true)
  writeString(8, "WAVE")
  writeString(12, "fmt ")
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * blockAlign, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, 16, true)
  writeString(36, "data")
  view.setUint32(40, dataSize, true)

  let offset = 44
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]))
    view.setInt16(offset, Math.round(sample * 32767), true)
    offset += 2
  }

  const bytes = new Uint8Array(buffer)
  let binary = ""
  for (let index = 0; index < bytes.byteLength; index += 1) {
    binary += String.fromCharCode(bytes[index])
  }
  return `data:audio/wav;base64,${window.btoa(binary)}`
}

function getFallbackChimeDataUrl() {
  if (fallbackChimeDataUrl) return fallbackChimeDataUrl
  const sampleRate = 22050
  const durationSec = 0.3
  const sampleCount = Math.floor(sampleRate * durationSec)
  const samples = new Float32Array(sampleCount)

  for (let i = 0; i < sampleCount; i += 1) {
    const time = i / sampleRate
    const firstTone = time <= 0.13 ? Math.sin(2 * Math.PI * 784 * time) : 0
    const secondTone =
      time >= 0.08 && time <= 0.28 ? Math.sin(2 * Math.PI * 1046.5 * (time - 0.08)) : 0
    const env = Math.exp(-8 * time)
    samples[i] = (firstTone * 0.45 + secondTone * 0.35) * env * 0.7
  }

  fallbackChimeDataUrl = encodeWavDataUrl(samples, sampleRate)
  return fallbackChimeDataUrl
}

function playScanCompleteChime(ctx) {
  if (!ctx || ctx.state !== "running") return
  const t0 = ctx.currentTime
  const master = ctx.createGain()
  master.gain.value = 0.22
  master.connect(ctx.destination)

  function tone(freqHz, start, durationSec) {
    const osc = ctx.createOscillator()
    const env = ctx.createGain()
    osc.type = "sine"
    osc.frequency.value = freqHz
    env.gain.setValueAtTime(0.0001, start)
    env.gain.exponentialRampToValueAtTime(0.35, start + 0.025)
    env.gain.exponentialRampToValueAtTime(0.0001, start + durationSec)
    osc.connect(env)
    env.connect(master)
    osc.start(start)
    osc.stop(start + durationSec + 0.04)
  }

  tone(784, t0, 0.11)
  tone(1046.5, t0 + 0.09, 0.16)
}

async function primeAudioContext(audioCtxRef, isAudioPrimedRef) {
  if (!audioCtxRef.current) {
    audioCtxRef.current = getAudioContext()
  }

  const ctx = audioCtxRef.current
  if (!ctx) return null

  try {
    if (ctx.state !== "running") {
      await ctx.resume()
    }

    // Tiny silent buffer playback helps "unlock" some browsers after the click gesture
    if (!isAudioPrimedRef.current && ctx.state === "running") {
      const silentBuffer = ctx.createBuffer(1, 1, ctx.sampleRate || 22050)
      const source = ctx.createBufferSource()
      source.buffer = silentBuffer
      source.connect(ctx.destination)
      source.start()
      isAudioPrimedRef.current = true
    }
  } catch (error) {
    console.warn("[Citron] Unable to prime Web Audio context", error)
  }

  return ctx
}

async function playCompletionSound(audioCtxRef, isAudioPrimedRef, fallbackAudioRef) {
  const ctx = await primeAudioContext(audioCtxRef, isAudioPrimedRef)

  if (ctx?.state === "running") {
    playScanCompleteChime(ctx)
    return
  }

  try {
    if (!fallbackAudioRef.current) {
      fallbackAudioRef.current = new Audio(getFallbackChimeDataUrl())
      fallbackAudioRef.current.preload = "auto"
    }
    fallbackAudioRef.current.currentTime = 0
    await fallbackAudioRef.current.play()
  } catch (error) {
    console.warn("[Citron] Unable to play fallback completion sound", error)
  }
}

/** Returns a short human-readable label + colour for the scan outcome. */
function resolveScanBadge(detail) {
  if (!detail) return { label: "Scan failed", color: "rgba(220,80,80,0.9)" }
  const {
    publish_status,
    inserted = 0,
    stale_deleted = 0,
    force_full_refresh_accepted = false,
    force_full_refresh_rejected_reason = null,
    delete_blocked_reason = null,
  } = detail

  if (publish_status === "full_refresh") {
    const parts = []
    if (inserted > 0) parts.push(`+${inserted}`)
    if (stale_deleted > 0) parts.push(`−${stale_deleted}`)
    const suffix = parts.length ? ` · ${parts.join(" ")}` : ""
    const forceTag = force_full_refresh_accepted ? " (forced)" : ""
    return { label: `Refreshed${suffix}${forceTag}`, color: "rgba(80,200,120,0.9)" }
  }

  if (publish_status === "additive_only") {
    const suffix = inserted > 0 ? ` · +${inserted}` : ""
    const reason = force_full_refresh_rejected_reason || delete_blocked_reason || null
    if (reason) {
      console.info("[Citron] Scan blocked:", reason)
    }
    return { label: `Partial scan${suffix}`, color: "rgba(255,190,50,0.9)" }
  }

  return { label: "Scan error", color: "rgba(220,80,80,0.9)" }
}

export default function Navbar({ lastScrapedAt, onScrapeComplete }) {
  const [scanning, setScanning] = useState(false)
  const [btnHovered, setBtnHovered] = useState(false)
  const [scanBadge, setScanBadge] = useState(null)
  const audioCtxRef = useRef(null)
  const isAudioPrimedRef = useRef(false)
  const fallbackAudioRef = useRef(null)
  const badgeTimerRef = useRef(null)

  // Clear badge when a new scan starts
  useEffect(() => {
    if (scanning) setScanBadge(null)
  }, [scanning])

  useEffect(() => {
    return () => {
      if (badgeTimerRef.current) clearTimeout(badgeTimerRef.current)
      if (audioCtxRef.current?.state !== "closed") {
        audioCtxRef.current?.close?.().catch(() => {})
      }
      fallbackAudioRef.current?.pause?.()
    }
  }, [])

  const formattedLastScanned = (() => {
    if (!lastScrapedAt) return null
    const scanDate = new Date(lastScrapedAt)
    if (Number.isNaN(scanDate.getTime())) return null
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      hour12: true,
      timeZoneName: "short",
    }).format(scanDate)
  })()

  async function handleScan() {
    if (scanning) return
    await primeAudioContext(audioCtxRef, isAudioPrimedRef)

    setScanning(true)
    try {
      const result = await triggerScrape({})
      await playCompletionSound(audioCtxRef, isAudioPrimedRef, fallbackAudioRef)

      const badge = resolveScanBadge(result?.detail)
      setScanBadge(badge)
      if (badgeTimerRef.current) clearTimeout(badgeTimerRef.current)
      badgeTimerRef.current = setTimeout(() => setScanBadge(null), 7000)

      if (onScrapeComplete) onScrapeComplete(result?.detail)
    } catch (err) {
      console.error("Scan failed:", err)
      await playCompletionSound(audioCtxRef, isAudioPrimedRef, fallbackAudioRef)
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
        background: "rgba(15,17,23,0.82)",
        backdropFilter: "blur(24px) saturate(180%)",
        WebkitBackdropFilter: "blur(24px) saturate(180%)",
        borderBottom: "1px solid rgba(99,120,255,0.12)",
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
            color: "var(--text-secondary)",
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
                color: "var(--text-secondary)",
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
              background: "linear-gradient(135deg, #FF8C00 0%, #FF3D00 100%)",
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
                  ? "0 0 30px rgba(255,107,0,0.65)"
                  : "0 0 20px rgba(255,107,0,0.4)",
              transform: btnHovered && !scanning ? "translateY(-1px) scale(1.02)" : "none",
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
