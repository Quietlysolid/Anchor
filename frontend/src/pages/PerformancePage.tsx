import { useEffect, useMemo, useState } from 'react'
import { useManualTradeJournal, useManualTradingProfile, useUpsertManualTradingProfile } from '../api/hooks'
import { SectionCard, cn, dollars, pctStr, signedDollars, useDashboardSnapshot } from './dashboardShared'
import { EquityCurve } from '../components/charts/EquityCurve'

export default function PerformancePage() {
  const snapshot = useDashboardSnapshot()
  const { data: manualTrades } = useManualTradeJournal()
  const { data: manualProfile } = useManualTradingProfile()
  const upsertManualProfile = useUpsertManualTradingProfile()
  const [startingBalanceInput, setStartingBalanceInput] = useState('')
  const performance = snapshot?.performance
  const readiness = snapshot?.readiness
  const failingChecks = readiness?.criteria?.filter((criterion) => !criterion.passed) ?? []
  const manualSummary = useMemo(() => buildManualSummary(manualTrades ?? [], manualProfile?.starting_balance ?? null), [manualTrades, manualProfile?.starting_balance])

  useEffect(() => {
    setStartingBalanceInput(manualProfile?.starting_balance != null ? String(manualProfile.starting_balance) : '')
  }, [manualProfile?.starting_balance])

  return (
    <div className="mx-auto max-w-2xl px-4 pb-24 pt-6 sm:px-6">
      <SectionCard title="Results" kicker="Your progress">
        <div className="px-5 py-5 sm:px-6">
          <EquityCurve data={performance?.equity_curve ?? []} />
          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric label="Week" value={pctStr(performance?.returns['1w_pct'])} />
            <Metric label="Month" value={pctStr(performance?.returns.mtd_pct)} />
            <Metric label="All time" value={pctStr(performance?.returns.since_start_pct)} />
            <Metric label="Days" value={`${performance?.track_record_days ?? 0}`} />
            <Metric label="Dip now" value={pctStr(performance?.drawdown.current_pct)} />
            <Metric label="Worst dip" value={pctStr(performance?.drawdown.max_pct)} />
            <Metric label="Closed month" value={signedDollars(performance?.realized_pl.mtd)} />
            <Metric label="Closed all time" value={signedDollars(performance?.realized_pl.since_start)} />
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Go Live?" kicker="Ready check">
        <div className="grid grid-cols-2 gap-4 px-5 py-5 sm:grid-cols-4 sm:px-6">
          <div className="col-span-2">
            <p className="text-[11px] text-anchor-fog/88">{readiness?.passed_checks ?? 0}/{readiness?.total_checks ?? 0} checks passed</p>
          </div>
          <Metric label="Bot runs" value={String(readiness?.rebalance_count ?? 0)} />
          <Metric label="Issues" value={String(readiness?.operational_incidents ?? 0)} />
        </div>
        {failingChecks.length > 0 && (
          <div className="border-t border-white/8 px-5 py-4 sm:px-6">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Need this first</p>
            <div className="mt-3 space-y-2">
              {failingChecks.slice(0, 4).map((criterion) => (
                <div key={criterion.key} className="rounded-[16px] border border-white/7 bg-white/[0.03] px-3 py-2.5">
                  <p className="text-[12px] font-semibold text-anchor-navy">{criterion.label}</p>
                  <p className="mt-0.5 text-[11px] text-anchor-fog/90">{blockerDetail(criterion.label, criterion.value, criterion.target)}</p>
                </div>
              ))}
            </div>
          </div>
        )}
        {failingChecks.length === 0 && (
          <div className="border-t border-white/8 px-5 py-4 sm:px-6">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Status</p>
            <p className="mt-2 text-sm text-anchor-win">No blockers right now.</p>
          </div>
        )}
      </SectionCard>

      <SectionCard title="Manual Robinhood" kicker="Your own trades">
        <div className="px-5 py-5 sm:px-6">
          <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
            <label className="block">
              <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Started with</span>
              <input
                inputMode="decimal"
                placeholder="How much you started with"
                value={startingBalanceInput}
                onChange={(event) => setStartingBalanceInput(event.target.value)}
                className="mt-2 w-full rounded-[12px] border border-white/10 bg-anchor-night/50 px-3 py-2 text-[14px] text-anchor-navy outline-none transition placeholder:text-anchor-fog/55 focus:border-anchor-brass/45"
              />
            </label>
            <button
              type="button"
              onClick={() => upsertManualProfile.mutate({ starting_balance: parseMoneyInput(startingBalanceInput) })}
              disabled={upsertManualProfile.isPending}
              className={cn(
                'rounded-full border px-3 py-2 font-mono text-[10px] uppercase tracking-[0.14em] transition sm:self-auto',
                upsertManualProfile.isPending
                  ? 'border-white/10 bg-white/[0.03] text-anchor-fog/70'
                  : 'border-anchor-brass/35 bg-anchor-brass/10 text-anchor-navy',
              )}
            >
              {upsertManualProfile.isPending ? 'Saving...' : 'Save'}
            </button>
          </div>

          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric label="Started with" value={dollars(manualSummary.startingBalance)} />
            <Metric label="Now" value={dollars(manualSummary.currentBalance)} />
            <Metric label="Profit" value={dollars(manualSummary.totalProfit)} />
            <Metric label="Loss" value={manualSummary.totalLoss > 0 ? `-$${manualSummary.totalLoss.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '$0.00'} />
          </div>

          <div className="mt-4 rounded-[16px] border border-white/8 bg-white/[0.03] px-4 py-3">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Up or down</p>
            <p className={cn('mt-1 font-mono text-[1.2rem] font-bold', manualSummary.net >= 0 ? 'text-anchor-win' : 'text-anchor-loss')}>
              {signedDollars(manualSummary.net)}
            </p>
            <p className="mt-1 text-[11px] text-anchor-fog/84">{manualSummary.closedTrades} finished trade{manualSummary.closedTrades === 1 ? '' : 's'} in Anchor.</p>
          </div>

          <div className="mt-5">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Daily calendar</p>
            {manualSummary.calendarDays.length > 0 ? (
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                {manualSummary.calendarDays.map((day) => (
                  <div
                    key={day.date}
                    className={cn(
                      'rounded-[14px] border px-3 py-2',
                      day.pnl > 0 ? 'border-emerald-500/20 bg-emerald-500/[0.06]' :
                      day.pnl < 0 ? 'border-rose-500/20 bg-rose-500/[0.06]' :
                      'border-white/8 bg-white/[0.03]',
                    )}
                  >
                    <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-anchor-fog/84">{formatCalendarDay(day.date)}</p>
                    <p className={cn('mt-1 font-mono text-[13px] font-semibold', day.pnl > 0 ? 'text-anchor-win' : day.pnl < 0 ? 'text-anchor-loss' : 'text-anchor-navy')}>
                      {signedDollars(day.pnl)}
                    </p>
                    <p className="mt-1 text-[10px] text-anchor-fog/84">{day.trades} trade{day.trades === 1 ? '' : 's'}</p>
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-anchor-fog">No finished manual trades yet.</p>
            )}
          </div>
        </div>
      </SectionCard>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">{label}</p>
      <p className="mt-1 font-mono text-[1rem] font-semibold text-anchor-navy">{value}</p>
    </div>
  )
}

function blockerDetail(label: string, value: string, target: string) {
  switch (label) {
    case 'Track record':
      return `${remainingDays(value, target)} more days needed.`
    case 'Automated rebalance cycles':
      return `${remainingCount(value, target)} more bot runs needed.`
    case 'Since-start return':
      return 'Needs to be above zero.'
    case 'Max drawdown':
      return `Must stay under ${target.replace('<= ', '')}.`
    case 'Margin incidents (30d)':
      return 'Needs zero margin issues.'
    case 'Operational incidents (30d)':
      return 'Needs zero app issues.'
    default:
      return `Target: ${target}.`
  }
}

function remainingDays(value: string, target: string) {
  const current = parseInt(value, 10) || 0
  const needed = parseInt(target.replace(/[^0-9]/g, ''), 10) || 0
  return Math.max(0, needed - current)
}

function remainingCount(value: string, target: string) {
  const current = parseInt(value, 10) || 0
  const needed = parseInt(target.replace(/[^0-9]/g, ''), 10) || 0
  return Math.max(0, needed - current)
}

const MANUAL_TRADE_POINT_VALUE: Record<string, number> = {
  MES: 5,
  MNQ: 2,
  ZN: 1000,
  MGC: 10,
  MCL: 100,
}

function parseMoneyInput(raw: string) {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const value = Number(trimmed)
  if (!Number.isFinite(value) || value < 0) return null
  return value
}

function buildManualSummary(
  entries: Array<{
    market: string | null
    direction: string | null
    contracts: number
    fill_price: number | null
    exit_price: number | null
    closed_on: string | null
  }>,
  startingBalance: number | null,
) {
  const closedTrades = entries
    .map((entry) => {
      const pointValue = entry.market ? MANUAL_TRADE_POINT_VALUE[entry.market] : null
      if (!pointValue || !entry.direction || entry.fill_price == null || entry.exit_price == null || !entry.closed_on) {
        return null
      }
      const delta = entry.direction === 'SHORT' ? entry.fill_price - entry.exit_price : entry.exit_price - entry.fill_price
      const pnl = delta * pointValue * entry.contracts
      return { date: entry.closed_on, pnl }
    })
    .filter((entry): entry is { date: string; pnl: number } => entry != null)

  const totalProfit = closedTrades.reduce((sum, trade) => sum + (trade.pnl > 0 ? trade.pnl : 0), 0)
  const totalLoss = closedTrades.reduce((sum, trade) => sum + (trade.pnl < 0 ? Math.abs(trade.pnl) : 0), 0)
  const net = totalProfit - totalLoss
  const calendarMap = new Map<string, { pnl: number; trades: number }>()

  for (const trade of closedTrades) {
    const current = calendarMap.get(trade.date) ?? { pnl: 0, trades: 0 }
    current.pnl += trade.pnl
    current.trades += 1
    calendarMap.set(trade.date, current)
  }

  const calendarDays = Array.from(calendarMap.entries())
    .sort((a, b) => a[0].localeCompare(b[0]))
    .slice(-28)
    .map(([date, value]) => ({ date, pnl: value.pnl, trades: value.trades }))
    .reverse()

  return {
    startingBalance,
    currentBalance: startingBalance != null ? startingBalance + net : null,
    totalProfit,
    totalLoss,
    net,
    closedTrades: closedTrades.length,
    calendarDays,
  }
}

function formatCalendarDay(value: string) {
  const date = new Date(`${value}T00:00:00Z`)
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
}
