import { useMemo } from 'react'
import { GlowCard } from '../components/ui/GlowCard'
import { TradeTable } from '../components/trades/TradeTable'
import { useTradeJournal, useTradeExplanations } from '../api/hooks'
import type { Trade } from '../types'

interface StatProps { label: string; value: string; sub?: string; color?: string }

function Stat({ label, value, sub, color = 'text-anchor-text' }: StatProps) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-anchor-muted tracking-wide">{label}</p>
      <p className={`text-2xl sm:text-3xl font-mono font-semibold tabular-nums ${color}`}>{value}</p>
      {sub && <p className="text-xs text-anchor-muted font-mono">{sub}</p>}
    </div>
  )
}

export default function Trades() {
  const { data: apiTrades }     = useTradeJournal()
  const { data: explanationData } = useTradeExplanations(50)
  const trades: Trade[] = apiTrades ?? []

  const explanations = useMemo(() => {
    const map: Record<string, string> = {}
    for (const ex of explanationData ?? []) {
      if (ex.trade_id) map[ex.trade_id] = ex.content
    }
    return map
  }, [explanationData])

  const stats = useMemo(() => {
    if (!trades.length) return { winRate: 0, avgRR: 0, totalPL: 0, avgHoldMins: 0, wins: 0, total: 0 }
    const wins     = trades.filter(t => t.net_pl > 0).length
    const totalPL  = trades.reduce((s, t) => s + t.net_pl, 0)
    const winAmts  = trades.filter(t => t.net_pl > 0).map(t => t.net_pl)
    const lossAmts = trades.filter(t => t.net_pl < 0).map(t => Math.abs(t.net_pl))
    const avgWin   = winAmts.length  ? winAmts.reduce((s, v)  => s + v, 0) / winAmts.length  : 0
    const avgLoss  = lossAmts.length ? lossAmts.reduce((s, v) => s + v, 0) / lossAmts.length : 1
    const avgHoldMins = trades.reduce((s, t) =>
      s + (new Date(t.closed_at).getTime() - new Date(t.opened_at).getTime()) / 60_000, 0
    ) / trades.length

    return { winRate: (wins / trades.length) * 100, avgRR: avgWin / avgLoss, totalPL, avgHoldMins, wins, total: trades.length }
  }, [trades])

  function fmtHold(mins: number): string {
    if (mins < 60) return `${Math.round(mins)}m`
    const h = Math.floor(mins / 60)
    const m = Math.round(mins % 60)
    return m ? `${h}h ${m}m` : `${h}h`
  }

  const plSign = stats.totalPL >= 0 ? '+' : ''

  return (
    <div className="min-h-screen bg-anchor-void p-5 space-y-4">

      <GlowCard padding={false} className="p-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 md:gap-8">
          <Stat
            label="Win Rate"
            value={`${stats.winRate.toFixed(1)}%`}
            sub={`${stats.wins} of ${stats.total} trades`}
            color={stats.winRate >= 45 ? 'text-anchor-green' : stats.winRate >= 38 ? 'text-anchor-text' : 'text-anchor-red'}
          />
          <Stat
            label="Avg R:R"
            value={`${stats.avgRR.toFixed(2)}×`}
            sub="reward / risk"
            color={stats.avgRR >= 1.8 ? 'text-anchor-green' : 'text-anchor-text'}
          />
          <Stat
            label="Total P&L"
            value={`${plSign}$${Math.abs(stats.totalPL).toFixed(2)}`}
            sub="all closed trades"
            color={stats.totalPL >= 0 ? 'text-anchor-green' : 'text-anchor-red'}
          />
          <Stat
            label="Avg Hold"
            value={fmtHold(stats.avgHoldMins)}
            sub="per trade"
          />
        </div>
      </GlowCard>

      <GlowCard padding={false} className="p-5">
        <TradeTable trades={trades} explanations={explanations} />
      </GlowCard>
    </div>
  )
}
