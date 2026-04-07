import { useEffect, useState } from 'react'
import { SectionCard, TradeHistoryRow, cn, humanTime, useDashboardSnapshot } from './dashboardShared'

export default function ActivityPage() {
  const snapshot = useDashboardSnapshot()
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  const decisions = snapshot?.decisions ?? []
  const recentTrades = snapshot?.recent_results ?? []
  const historyNotice = snapshot?.history_notice

  return (
    <div className="mx-auto max-w-2xl px-4 pb-24 pt-6 sm:px-6">
      <SectionCard title="Activity">
        {decisions.length > 0 ? (
          <div className="divide-y divide-white/7">
            {decisions.map((item) => (
              <div key={item.id} className="flex gap-3 px-5 py-4 sm:px-6">
                <div className={cn(
                  'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                  item.tone === 'good' ? 'bg-anchor-win' :
                  item.tone === 'bad' ? 'bg-anchor-loss' :
                  item.tone === 'warn' ? 'bg-anchor-warn' :
                  'bg-anchor-rule/60',
                )} />
                <div>
                  <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                    {eventCategory(item.reason_code)}
                  </p>
                  <p className="text-[13px] leading-relaxed text-anchor-navy/95">{item.title}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/90">{item.detail}</p>
                  <p className="mt-1 font-mono text-[10px] tracking-[0.05em] text-anchor-fog/84">{humanTime(item.occurred_at, nowMs)}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="px-5 py-6 sm:px-6">
            <p className="text-sm text-anchor-fog">No meaningful activity yet.</p>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Closed Trades">
        {historyNotice ? (
          <div className="border-b border-white/8 px-5 py-4 sm:px-6">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">History source</p>
            <p className="mt-1 text-[11px] leading-relaxed text-anchor-fog/90">{historyNotice}</p>
          </div>
        ) : null}
        <div className="px-5 sm:px-6">
          {recentTrades.length > 0 ? (
            recentTrades.slice(0, 8).map((trade) => <TradeHistoryRow key={trade.id} trade={trade} nowMs={nowMs} />)
          ) : (
            <div className="py-6">
              <p className="text-sm text-anchor-fog">No closed trades yet.</p>
            </div>
          )}
        </div>
      </SectionCard>
    </div>
  )
}

function eventCategory(reasonCode: string | null) {
  const code = (reasonCode ?? '').toUpperCase()
  if (code.includes('RECONCILIATION')) return 'Health'
  if (code.includes('REBALANCE')) return 'Rebalance'
  if (code.includes('GUARD')) return 'Risk'
  if (code.includes('ORDER') || code.includes('FILL')) return 'Execution'
  if (code.includes('TRADE')) return 'Trade'
  return 'System'
}
