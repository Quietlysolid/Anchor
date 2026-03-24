import { useEffect, useState } from 'react'
import { Pill } from '../ui/Pill'
import type { Position } from '../../types'
import { AnimatedNumber } from '../ui/AnimatedNumber'

interface Props { positions: Position[] }

function holdsFor(openedAt: string): string {
  const secs = Math.floor((Date.now() - new Date(openedAt).getTime()) / 1000)
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

interface FlashState { id: string; dir: 'up' | 'down' }

export function PositionsTable({ positions }: Props) {
  const [flashes, setFlashes] = useState<FlashState[]>([])
  const prevRef = useState<Record<string, number>>(() => ({}))[0]

  useEffect(() => {
    const newFlashes: FlashState[] = []
    for (const pos of positions) {
      if (pos.unrealized_pl == null) continue
      const prev = prevRef[pos.id]
      if (prev !== undefined && prev !== pos.unrealized_pl) {
        newFlashes.push({ id: pos.id, dir: pos.unrealized_pl > prev ? 'up' : 'down' })
      }
      prevRef[pos.id] = pos.unrealized_pl
    }
    if (newFlashes.length) {
      setFlashes(newFlashes)
      const t = setTimeout(() => setFlashes([]), 600)
      return () => clearTimeout(t)
    }
  }, [positions, prevRef])

  if (!positions.length) {
    return (
      <div className="py-10 text-center text-anchor-muted text-sm font-mono italic">
        No open positions. The system is watching.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[420px] text-sm">
        <thead>
          <tr className="text-[11px] font-mono text-anchor-muted uppercase tracking-wide border-b border-anchor-border">
            <th className="pb-2 text-left font-medium">Instrument</th>
            <th className="pb-2 text-left font-medium">Direction</th>
            <th className="pb-2 text-right font-medium">Entry</th>
            <th className="pb-2 text-right font-medium">Live P&L</th>
            <th className="pb-2 text-right font-medium hidden md:table-cell">Duration</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-anchor-border/40">
          {positions.map(pos => {
            const flash = flashes.find(f => f.id === pos.id)
            const flashClass = flash?.dir === 'up' ? 'bg-anchor-green/10' : flash?.dir === 'down' ? 'bg-anchor-red/10' : ''
            const pl = pos.unrealized_pl
            const plKnown = pl != null
            const plColor = !plKnown ? 'text-anchor-muted' : pl >= 0 ? 'text-anchor-green' : 'text-anchor-red'

            return (
              <tr key={pos.id} className={`transition-colors duration-300 ${flashClass}`}>
                <td className="py-2.5 font-mono font-medium text-anchor-text">
                  {pos.instrument.replace('_', '/')}
                </td>
                <td className="py-2.5">
                  <Pill label={pos.direction} variant={pos.direction === 'LONG' ? 'buy' : 'sell'} />
                </td>
                <td className="py-2.5 text-right font-mono text-anchor-text/80 text-xs">
                  {pos.avg_entry_price.toFixed(pos.avg_entry_price > 10 ? 3 : 5)}
                </td>
                <td className={`py-2.5 text-right font-mono font-medium text-xs ${plColor}`}>
                  {plKnown ? (
                    <AnimatedNumber
                      value={pl}
                      prefix={pl >= 0 ? '+$' : '-$'}
                      decimals={2}
                    />
                  ) : '—'}
                </td>
                <td className="py-2.5 text-right font-mono text-xs text-anchor-muted hidden md:table-cell">
                  {holdsFor(pos.opened_at)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
