import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        anchor: {
          parchment: '#eeead8',   // chart paper background
          card:      '#faf9f3',   // slightly warm white for cards / insets
          navy:      '#1a2744',   // primary ink — headings, borders, key text
          chartblue: '#2b5ea7',   // accent — active states, links
          rule:      '#c4bca8',   // chart grid lines / dividers
          slate:     '#526070',   // secondary body text
          fog:       '#8a8578',   // muted / placeholder
          win:       '#2d6a4f',   // profit / positive
          loss:      '#8b2020',   // loss / negative
          warn:      '#7a5c1e',   // warning / caution
          spine:     '#0f1822',   // sidebar dark background
          spine2:    '#172030',   // sidebar hover
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
