/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx,ts,tsx}"],
  theme: {
    extend: {
      colors: {
        c: {
          bg:          '#080808',
          surface:     '#0f0f0f',
          card:        '#121212',
          hover:       '#181818',
          border:      'rgba(255,255,255,0.06)',
          borderHover: 'rgba(255,120,30,0.35)',
          orange:      '#FF6200',
          orangeL:     '#FF8C00',
          orangeD:     '#CC3300',
          muted:       'rgba(255,255,255,0.45)',
          faint:       'rgba(255,255,255,0.2)',
        },
        surface: {
          0: "#0a0a0a",
          1: "#111111",
          2: "#181818",
          3: "#222222",
          4: "#2e2e2e",
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
