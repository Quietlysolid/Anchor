import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        anchor: {
          void:    '#000000',
          surface: '#1C1C1E',
          border:  '#3A3A3C',
          muted:   '#8E8E93',
          text:    '#FFFFFF',
          green:   '#30D158',
          red:     '#FF453A',
        },
        border:     'hsl(var(--border))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        card: { DEFAULT: 'hsl(var(--card))', foreground: 'hsl(var(--card-foreground))' },
        primary: { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        muted: { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        destructive: { DEFAULT: 'hsl(var(--destructive))' },
        green: { 400: '#4ade80', 500: '#22c55e' },
        red:   { 400: '#f87171', 500: '#ef4444' },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      keyframes: {
        'glow-pulse': {
          '0%, 100%': { opacity: '1' },
          '50%':       { opacity: '0.4' },
        },
        'slide-in-top': {
          from: { transform: 'translateY(-12px)', opacity: '0' },
          to:   { transform: 'translateY(0)',     opacity: '1' },
        },
        'reveal-right': {
          from: { clipPath: 'inset(0 100% 0 0)' },
          to:   { clipPath: 'inset(0 0% 0 0)' },
        },
        'fade-up': {
          from: { transform: 'translateY(8px)', opacity: '0' },
          to:   { transform: 'translateY(0)',   opacity: '1' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
      },
      animation: {
        'glow-pulse':   'glow-pulse 2s ease-in-out infinite',
        'slide-in-top': 'slide-in-top 0.3s ease forwards',
        'reveal-right': 'reveal-right 1.2s ease forwards',
        'fade-up':      'fade-up 0.4s ease forwards',
        'fade-in':      'fade-in 0.2s ease-out both',
      },
      boxShadow: {
        'glow-green': '0 0 24px rgba(48, 209, 88, 0.20)',
        'glow-red':   '0 0 24px rgba(255, 69, 58, 0.20)',
      },
    },
  },
  plugins: [],
} satisfies Config
