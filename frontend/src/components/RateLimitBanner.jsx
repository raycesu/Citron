export default function RateLimitBanner() {
  return (
    <div
      role="status"
      className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-2.5 text-center text-sm text-amber-200/95"
    >
      <span className="font-medium text-amber-100">Gemini rate limit reached</span>
      <span className="text-amber-200/80">
        {" "}
        — AI scoring is paused until the daily quota resets (midnight{" "}
        <abbr title="Google AI Studio / Gemini API quota day" className="cursor-help no-underline">
          Pacific
        </abbr>
        ). Events may appear without scores or tags from the model.
      </span>
    </div>
  )
}
