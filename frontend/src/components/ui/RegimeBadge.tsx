import { useEffect, useRef, useState } from 'react'
import type { Regime } from '../../types'

interface Props { regime: Regime | string | null; className?: string }

const CONFIG: Record<string, { label: string; text: string; bg: string; ring: string }> = {
  TRENDING:  { label: 'TRENDING',  text: 'text-anchor-green', bg: 'bg-anchor-green/10', ring: 'border-anchor-green/30' },
  RANGING:   { label: 'RANGING',   text: 'text-[#6B8CFF]',   bg: 'bg-[#6B8CFF]/10',   ring: 'border-[#6B8CFF]/30'   },
  VOLATILE:  { label: 'VOLATILE',  text: 'text-amber-400',   bg: 'bg-amber-400/10',   ring: 'border-amber-400/30'   },
}

export function RegimeBadge({ regime, className = '' }: Props) {
  const key    = (regime ?? 'RANGING').toString().toUpperCase()
  const cfg    = CONFIG[key] ?? CONFIG['RANGING']
  const [prev, setPrev] = useState(key)
  const [fade, setFade] = useState(false)
  const timer  = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    if (key !== prev) {
      setFade(true)
      timer.current = setTimeout(() => { setPrev(key); setFade(false) }, 200)
    }
    return () => clearTimeout(timer.current)
  }, [key, prev])

  return (
    <span className={`
      inline-flex items-center gap-1.5
      text-xs font-mono font-medium
      px-2 py-0.5 rounded-full
      border ${cfg.ring} ${cfg.bg} ${cfg.text}
      transition-opacity duration-200
      ${fade ? 'opacity-0' : 'opacity-100'}
      ${className}
    `}>
      <span className={`w-1.5 h-1.5 rounded-full ${cfg.text.replace('text-', 'bg-')} animate-glow-pulse`} />
      {cfg.label}
    </span>
  )
}
