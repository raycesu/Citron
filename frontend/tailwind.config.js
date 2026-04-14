/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        c: {
          // backgrounds
          bg:          '#0F1117',
          surface:     '#12141C',
          card:        '#1E2130',
          hover:       '#242840',
          // borders
          border:      'rgba(99,120,255,0.12)',
          borderHover: 'rgba(99,120,255,0.35)',
          borderFeat:  'rgba(255,107,0,0.45)',
          // primary accent
          orange:      '#FF6B00',
          orangeL:     '#FF8C00',
          orangeD:     '#E05000',
          // secondary accent
          blue:        '#3B82F6',
          blueD:       '#2563EB',
          purple:      '#8B5CF6',
          teal:        '#0EA5E9',
          // text
          text:        '#F0F0FF',
          muted:       '#A0A8C0',
          faint:       '#6B7280',
          // score colors
          scoreHigh:   '#22C55E',
          scoreMid:    '#F59E0B',
          scoreLow:    '#EF4444',
        },
        surface: {
          0: "#0F1117",
          1: "#12141C",
          2: "#1E2130",
          3: "#242840",
          4: "#2D3250",
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'monospace'],
      },
      animation: {
        "fade-in": "fadeIn 0.2s ease-in-out",
        "slide-up": "slideUp 0.3s ease-out",
        pulse: "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite",
      },
      keyframes: {
        fadeIn: { "0%": { opacity: 0 }, "100%": { opacity: 1 } },
        slideUp: {
          "0%": { opacity: 0, transform: "translateY(8px)" },
          "100%": { opacity: 1, transform: "translateY(0)" },
        },
      },
    },
  },
  plugins: [],
};
