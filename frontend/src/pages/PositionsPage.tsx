import { useEffect, useState } from 'react'
import { usePositionStore } from '../store'
import {
  SectionCard,
  cn,
  directionLabel,
  dollars,
  formatScheduledTime,
  holdTime,
  humanTime,
  marketInfo,
  pctStr,
  priceStr,
  signedDollars,
  useDashboardSnapshot,
} from './dashboardShared'

export default function PositionsPage() {
  const snapshot = useDashboardSnapshot()
  const { positions } = usePositionStore()
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  const account = snapshot?.account
  const nextRebalance = snapshot?.operator?.next_window?.starts_at
  const totalPaperPL = positions.reduce((sum, pos) => sum + (pos.unrealized_pl ?? 0), 0)

  return (
    <div className="mx-auto max-w-2xl px-4 pb-24 pt-6 sm:px-6">
      <SectionCard title="Positions">
        <div className="grid grid-cols-2 gap-4 px-5 py-5 sm:grid-cols-3 sm:px-6">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Account value</p>
            <p className="mt-1 font-mono text-[1.15rem] font-bold text-anchor-navy">{dollars(account?.equity)}</p>
          </div>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Today P&L</p>
            <p className={cn(
              'mt-1 font-mono text-[1.15rem] font-bold',
              (account?.broker_day_pl ?? 0) > 0 ? 'text-anchor-win' : (account?.broker_day_pl ?? 0) < 0 ? 'text-anchor-loss' : 'text-anchor-navy',
            )}>
              {signedDollars(account?.broker_day_pl)}
            </p>
          </div>
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Margin usage</p>
            <p className="mt-1 font-mono text-[1.15rem] font-bold text-anchor-navy">{pctStr(account?.margin_usage_pct)}</p>
          </div>
        </div>

        {positions.length > 0 ? (
          <div className="border-t border-white/8">
            <div className="px-5 py-4 sm:px-6">
              <p className={cn('text-sm font-semibold', totalPaperPL > 0 ? 'text-anchor-win' : totalPaperPL < 0 ? 'text-anchor-loss' : 'text-anchor-navy')}>
                {signedDollars(totalPaperPL)} total unrealized
              </p>
            </div>
            <div className="divide-y divide-white/8">
              {positions.map((pos) => {
                const mkt = marketInfo(pos.instrument)
                const pl = pos.unrealized_pl ?? 0
                const exitHint = pos.stop_loss != null
                  ? pos.direction === 'LONG'
                    ? `Exit protection at ${priceStr(pos.stop_loss)}`
                    : `Buy-back protection at ${priceStr(pos.stop_loss)}`
                  : null
                return (
                  <div key={pos.id} className="px-5 py-5 sm:px-6">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <p className="text-[1.05rem] font-semibold text-anchor-navy">{mkt.name}</p>
                        <p className="mt-1 text-[12px] text-anchor-slate/95">
                          <span className={cn('font-semibold', pos.direction === 'LONG' ? 'text-anchor-win' : 'text-anchor-loss')}>
                            {directionLabel(pos.direction)}
                          </span>
                          {' · '}{Math.abs(pos.units)} contract{Math.abs(pos.units) !== 1 ? 's' : ''}
                          {' · '}{holdTime(pos.opened_at, nowMs)}
                        </p>
                        <p className="mt-1 text-[12px] text-anchor-slate/95">
                          Entry <span className="font-mono text-anchor-navy">{priceStr(pos.avg_entry_price)}</span>
                          {' · '}Now <span className="font-mono text-anchor-navy">{priceStr(pos.current_price)}</span>
                        </p>
                        {exitHint && <p className="mt-2 text-[11px] text-anchor-fog/88">{exitHint}</p>}
                      </div>
                      <div className="shrink-0 text-right">
                        <p className={cn('whitespace-nowrap font-mono text-[1.8rem] font-bold leading-none', pl > 0 ? 'text-anchor-win' : pl < 0 ? 'text-anchor-loss' : 'text-anchor-navy')}>
                          {signedDollars(pl)}
                        </p>
                        <p className="mt-1 font-mono text-[10px] uppercase tracking-[0.14em] text-anchor-fog/84">Unrealized</p>
                        <p className="mt-2 font-mono text-[10px] tracking-[0.05em] text-anchor-fog/84">{humanTime(pos.opened_at, nowMs)}</p>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        ) : (
          <div className="px-5 py-10 text-center sm:px-6">
            <p className="text-sm text-anchor-fog">No positions open right now.</p>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Open Orders">
        {(snapshot?.execution?.open_orders?.length ?? 0) > 0 ? (
          <div className="divide-y divide-white/8">
            {snapshot?.execution?.open_orders?.map((order) => (
              <div key={order.id} className="flex items-start justify-between gap-3 px-5 py-4 sm:px-6">
                <div>
                  <p className="text-[13px] font-semibold text-anchor-navy">{order.instrument}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/90">
                    {directionLabel(order.direction)} · {order.order_type} · {Math.abs(order.units)} contract{Math.abs(order.units) !== 1 ? 's' : ''}
                  </p>
                </div>
                <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-anchor-warn">{order.state}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 py-6 sm:px-6">
            <p className="text-sm text-anchor-fog">No open broker orders.</p>
            {nextRebalance && <p className="mt-1 text-xs text-anchor-fog/78">Next rebalance: {formatScheduledTime(nextRebalance, nowMs)}</p>}
          </div>
        )}
      </SectionCard>
    </div>
  )
}
