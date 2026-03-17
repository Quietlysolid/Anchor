import { useState } from 'react'
import type { Regime } from '../../types'

interface Block {
  regime:    Regime
  startTime: number
  endTime:   number
}

interface Props {
  blocks:       Block[]
  onFilter?:    (range: { start: number; end: number } | null) => void
  activeRange?: { start: number; end: number } | null
}

const COLORS: Record<Regime, { bg: string; text: string; border: string }> = {
  TRENDING: { bg: 'bg-anchor-green/20',  text: 'text-anchor-green', border: 'border-anchor-green/40' },
  RANGING:  { bg: 'bg-[#6B8CFF]/20',    text: 'text-[#6B8CFF]',   border: 'border-[#6B8CFF]/40'   },
  VOLATILE: { bg: 'bg-amber-400/20',    text: 'text-amber-400',   border: 'border-amber-400/40'   },
}

export function RegimeTimeline({ blocks, onFilter, activeRange }: Props) {
  const [hover, setHover] = useState<number | null>(null)

  if (!blocks.length) return null

  const t0        = blocks[0].startTime
  const totalSpan = blocks[blocks.length - 1].endTime - t0

  return (
    <div className="flex h-10 gap-0.5 items-stretch rounded-lg overflow-hidden">
      {blocks.map((block, i) => {
        const width = ((block.endTime - block.startTime) / totalSpan) * 100
        const cfg   = COLORS[block.regime]
        const active = activeRange?.start === block.startTime
        const isHover = hover === i

        return (
          <button
            key={i}
            type="button"
            style={{ width: `${width}%` }}
            className={`
              relative h-full transition-all duration-150
              border ${cfg.border} ${cfg.bg}
              ${active || isHover ? 'opacity-100 scale-y-105' : 'opacity-60 hover:opacity-90'}
              rounded-sm cursor-pointer
            `}
            onClick={() => {
              if (onFilter) {
                if (active) onFilter(null)
                else onFilter({ start: block.startTime, end: block.endTime })
              }
            }}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
            title={`${block.regime} · ${new Date(block.startTime).toLocaleDateString()}`}
          >
            {width > 8 && (
              <span className={`absolute inset-0 flex items-center justify-center text-[9px] font-mono font-medium ${cfg.text} uppercase`}>
                {block.regime.slice(0, 4)}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// Generate mock blocks for demo
export function genMockRegimeBlocks(): Block[] {
  const now   = Date.now()
  const day   = 86400_000
  const regimes: Regime[] = ['TRENDING', 'RANGING', 'TRENDING', 'VOLATILE', 'TRENDING', 'RANGING', 'TRENDING']
  const blocks: Block[] = []
  let t = now - 7 * day
  for (const regime of regimes) {
    const dur = day * (0.5 + Math.random())
    blocks.push({ regime, startTime: t, endTime: t + dur })
    t += dur
  }
  blocks[blocks.length - 1].endTime = now
  return blocks
}
