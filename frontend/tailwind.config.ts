import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        anchor: {
          // ── Surfaces ──────────────────────────────────────────────
          bg:        '#0b1628',   // page background — deep maritime navy
          surface:   '#0d1e35',   // card / panel surface
          border:    '#1c2f4a',   // card borders and section dividers
          // ── Text ──────────────────────────────────────────────────
          navy:      '#d4cfc0',   // primary text — warm cream (was dark ink)
          brass:     '#c9a84c',   // display headings — brass / amber
          chartblue: '#60a5fa',   // accent — links, active states
          rule:      '#1c2f4a',   // chart grid lines / dividers
          slate:     '#8899ae',   // secondary body text
          fog:       '#5e6e82',   // muted / placeholder
          // ── Signal ────────────────────────────────────────────────
          win:       '#4ade80',   // profit / positive — bright green
          loss:      '#f87171',   // loss / negative — coral red
          warn:      '#fbbf24',   // warning / caution — amber
          // ── Legacy surfaces (repurposed as dark tones) ────────────
          parchment: '#0d1e35',   // formerly cream card bg, now dark surface
          card:      '#0f2040',   // slightly raised dark surface
          // ── Sidebar ───────────────────────────────────────────────
          spine:     '#060f1e',   // deepest navy
          spine2:    '#0b1628',   // hover state
        },
        border:      'hsl(var(--border))',
        background:  'hsl(var(--background))',
        foreground:  'hsl(var(--foreground))',
        card:        { DEFAULT: 'hsl(var(--card))', foreground: 'hsl(var(--card-foreground))' },
        primary:     { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        muted:       { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        destructive: { DEFAULT: 'hsl(var(--destructive))' },
      },
      fontFamily: {
        mono:    ['"JetBrains Mono"', '"Courier New"', 'monospace'],
        sans:    ['"Manrope"', '"Avenir Next"', '"Segoe UI"', 'sans-serif'],
        display: ['"DM Serif Display"', 'Georgia', 'serif'],
        serif:   ['"DM Serif Display"', 'Georgia', 'serif'],
      },
      keyframes: {
        'fade-up': {
          from: { transform: 'translateY(8px)', opacity: '0' },
          to:   { transform: 'translateY(0)',   opacity: '1' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.4s ease forwards',
      },
    },
  },
  plugins: [],
} satisfies Config
