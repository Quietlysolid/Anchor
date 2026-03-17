import { usePerformance, useMonteCarlo, useEquityCurve, useLatestPerfCheck } from '../api/hooks'
import { EquityCurveChart } from '../components/charts/EquityCurveChart'
import { RefreshCw } from 'lucide-react'
import { useWeightsStore } from '../store'
import type { PerfCheckSummary } from '../types'

type Status = 'good' | 'warn' | 'bad' | 'neutral'

function statStatus(value: number, good: number, warn: number, reversed = false): Status {
  if (reversed) return value <= good ? 'good' : value <= warn ? 'warn' : 'bad'
  return value >= good ? 'good' : value >= warn ? 'warn' : 'bad'
}

function StatCard({ label, value, sub, status }: {
  label: string; value: string; sub?: string; status?: Status
}) {
  const valueColor = status === 'good' ? 'text-green-400'
    : status === 'warn' ? 'text-amber-400'
    : status === 'bad'  ? 'text-red-400'
    : ''
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className={`text-2xl font-bold font-mono ${valueColor}`}>{value}</div>
      <div className="text-sm font-medium mt-1">{label}</div>
      {sub && <div className="text-[11px] text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  )
}

function GoalBar({ label, valuePct, targetPct, value, target, status }: {
  label: string
  valuePct: number   // 0–100 fill
  targetPct: number  // 0–100 marker position
  value: string
  target: string
  status: Status
}) {
  const barColor = status === 'good' ? 'bg-green-400' : status === 'warn' ? 'bg-amber-400' : 'bg-red-400'
  const textColor = status === 'good' ? 'text-green-400' : status === 'warn' ? 'text-amber-400' : 'text-red-400'
  const clampedFill = Math.min(100, Math.max(0, valuePct))
  return (
    <div>
      <div className="flex justify-between items-baseline mb-1.5">
        <span className="text-xs text-muted-foreground">{label}</span>
        <div className="flex items-baseline gap-2">
          <span className={`text-sm font-bold font-mono ${textColor}`}>{value}</span>
          <span className="text-[10px] text-muted-foreground/40">target {target}</span>
        </div>
      </div>
      <div className="relative h-1.5 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${barColor}`}
          style={{ width: `${clampedFill}%` }} />
        {/* target marker */}
        <div className="absolute top-0 bottom-0 w-px bg-white/30"
          style={{ left: `${Math.min(99, targetPct)}%` }} />
      </div>
    </div>
  )
}

function MCCard({ label, value, sub, color }: { label: string; value: string; sub: string; color?: string }) {
  return (
    <div className="bg-muted/40 rounded-lg p-4 text-center">
      <div className={`text-xl font-bold font-mono ${color ?? ''}`}>{value}</div>
      <div className="text-xs font-medium mt-1">{label}</div>
      <div className="text-[10px] text-muted-foreground/60 mt-0.5 leading-relaxed">{sub}</div>
    </div>
  )
}

function BenchmarkRow({ s }: { s: PerfCheckSummary }) {
  const statusColor = s.status === 'ok'
    ? 'text-green-400' : s.status === 'insufficient_trades'
    ? 'text-muted-foreground' : 'text-red-400'
  const statusLabel: Record<string, string> = {
    ok: 'On track', DEGRADED_WR: 'WR degraded', UNPROFITABLE: 'Net negative',
    WIN_DROUGHT: 'Win drought', insufficient_trades: 'Not enough data',
  }
  const wrDelta = s.rolling_wr_pct - s.bench_wr_pct
  const wrDeltaColor = wrDelta >= 0 ? 'text-green-400' : 'text-red-400'

  return (
    <div className="grid grid-cols-[6rem_1fr_1fr_1fr_5rem] gap-x-4 items-center py-2 border-b border-border/40 last:border-0 text-xs">
      <span className="font-semibold">{s.strategy}</span>
      <span>
        <span className={wrDeltaColor + ' font-mono font-bold'}>{s.rolling_wr_pct.toFixed(1)}%</span>
        <span className="text-muted-foreground"> WR</span>
        <span className="text-muted-foreground/50 ml-1">(bench {s.bench_wr_pct}%)</span>
      </span>
      <span>
        <span className={s.rolling_pf >= 1 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>{s.rolling_pf.toFixed(2)}×</span>
        <span className="text-muted-foreground"> PF</span>
        <span className="text-muted-foreground/50 ml-1">(bench {s.bench_pf}×)</span>
      </span>
      <span className="text-muted-foreground">{s.n_trades} trades</span>
      <span className={statusColor + ' font-medium'}>{statusLabel[s.status] ?? s.status}</span>
    </div>
  )
}

export default function Performance() {
  const { data: perf } = usePerformance()
  const { data: mc, refetch: refreshMC, isFetching: mcFetching } = useMonteCarlo()
  const { data: eq } = useEquityCurve()
  const { data: perfCheck } = useLatestPerfCheck()
  const t = useWeightsStore(s => s.targets)

  const wr  = perf?.win_rate      ?? 0
  const pf  = perf?.profit_factor ?? 0
  const dd  = perf?.max_drawdown  ?? 0
  const pnl = perf?.net_pnl       ?? 0

  const statuses = perf ? {
    wr:  statStatus(wr,  t.win_rate_good,      t.win_rate_warn),
    pf:  statStatus(pf,  t.profit_factor_good, t.profit_factor_warn),
    dd:  statStatus(dd,  t.drawdown_good,       t.drawdown_halt, true),
    pnl: (pnl > 0 ? 'good' : pnl < 0 ? 'bad' : 'warn') as Status,
  } : { wr: 'neutral' as Status, pf: 'neutral' as Status, dd: 'neutral' as Status, pnl: 'neutral' as Status }

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6">
      <div>
        <h1 className="text-xl font-bold">Performance</h1>
        <p className="text-sm text-muted-foreground mt-0.5">Live results since the system started trading.</p>
      </div>

      {/* ── 4 headline numbers ── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatCard
          label="Net profit"
          value={perf ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}` : '—'}
          sub="Total dollars made or lost"
          status={statuses.pnl}
        />
        <StatCard
          label="Trades won"
          value={perf ? `${(wr * 100).toFixed(1)}%` : '—'}
          sub={`of ${perf?.total_trades ?? '—'} closed trades`}
          status={statuses.wr}
        />
        <StatCard
          label="Wins vs losses"
          value={perf ? `${pf.toFixed(2)}×` : '—'}
          sub={perf && pf > 1 ? `Won $${pf.toFixed(2)} per $1 lost` : `Target ${t.profit_factor_good}×+`}
          status={statuses.pf}
        />
        <StatCard
          label="Biggest dip"
          value={perf ? `${(dd * 100).toFixed(1)}%` : '—'}
          sub={`${dd <= t.drawdown_good ? '✓ Within safe zone' : dd <= t.drawdown_halt ? '⚠ Getting close to halt' : '✗ Halt limit hit'}`}
          status={statuses.dd}
        />
      </div>

      {/* ── Account growth ── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold">Account Growth</h3>
        <p className="text-[11px] text-muted-foreground/60 mt-0.5 mb-3">Your account balance over time</p>
        <EquityCurveChart data={eq ?? []} />
      </div>

      {/* ── Goal tracking ── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold">Goal Tracking</h3>
        <p className="text-[11px] text-muted-foreground/60 mt-0.5 mb-4">
          How each metric compares to the target. Bar fills toward the target marker.
        </p>
        {perf ? (
          <div className="space-y-4">
            <GoalBar
              label="Trades won"
              value={`${(wr * 100).toFixed(1)}%`}
              target={`${Math.round(t.win_rate_good * 100)}%`}
              valuePct={wr * 100 / t.win_rate_good * 80}
              targetPct={80}
              status={statuses.wr}
            />
            <GoalBar
              label="Wins vs losses"
              value={`${pf.toFixed(2)}×`}
              target={`${t.profit_factor_good}×`}
              valuePct={pf / t.profit_factor_good * 75}
              targetPct={75}
              status={statuses.pf}
            />
            <GoalBar
              label="Biggest account dip — lower is better"
              value={`${(dd * 100).toFixed(1)}%`}
              target={`under ${Math.round(t.drawdown_good * 100)}%`}
              valuePct={dd / t.drawdown_halt * 100}
              targetPct={t.drawdown_good / t.drawdown_halt * 100}
              status={statuses.dd}
            />
            <GoalBar
              label="Smoothness of returns"
              value={(perf.sharpe_ratio ?? 0).toFixed(2)}
              target={`${t.sharpe_good}+`}
              valuePct={(perf.sharpe_ratio ?? 0) / t.sharpe_good * 80}
              targetPct={80}
              status={statStatus(perf.sharpe_ratio ?? 0, t.sharpe_good, t.sharpe_warn)}
            />
          </div>
        ) : (
          <div className="py-6 text-center">
            <p className="text-xs text-muted-foreground">No data yet</p>
            <p className="text-[11px] text-muted-foreground/50 mt-1">This fills in once the system has closed trades.</p>
          </div>
        )}

        {/* Trade detail footer */}
        {perf && (
          <div className="mt-4 pt-3 border-t border-border grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Total trades</span>
              <span className="font-mono">{perf.total_trades ?? '—'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Avg win</span>
              <span className="font-mono text-green-400">+${(perf.avg_win_pips ?? 0).toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Avg loss</span>
              <span className="font-mono text-red-400">−${Math.abs(perf.avg_loss_pips ?? 0).toFixed(2)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Fees paid</span>
              <span className="font-mono text-muted-foreground">${(perf.total_commission ?? 0).toFixed(2)}</span>
            </div>
          </div>
        )}
      </div>

      {/* ── Live vs Benchmark ── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="flex items-start justify-between mb-1">
          <div>
            <h3 className="text-sm font-semibold">Live vs Backtest Benchmark</h3>
            <p className="text-[11px] text-muted-foreground/60 mt-0.5 leading-relaxed max-w-lg">
              Rolling performance of the last 50 closed trades per strategy, compared to 8-year OOS backtest results.
              {perfCheck && <span className="ml-1 text-muted-foreground/40">Checked {new Date(perfCheck.event_at).toLocaleDateString('en-US', { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</span>}
            </p>
          </div>
          {perfCheck?.severity === 'WARNING' && (
            <span className="shrink-0 ml-4 mt-0.5 text-[11px] font-semibold text-red-400 bg-red-400/10 px-2 py-0.5 rounded">
              ALERT
            </span>
          )}
        </div>

        {perfCheck && perfCheck.summaries.length > 0 ? (
          <div className="mt-3">
            {perfCheck.alerts.length > 0 && (
              <div className="mb-3 space-y-1">
                {perfCheck.alerts.map((a, i) => (
                  <div key={i} className="text-[11px] text-red-400 bg-red-400/5 border border-red-400/20 rounded px-3 py-1.5">
                    {a}
                  </div>
                ))}
              </div>
            )}
            {perfCheck.summaries.map(s => <BenchmarkRow key={s.strategy} s={s} />)}
          </div>
        ) : (
          <div className="mt-4 text-center py-6 border border-dashed border-border rounded-lg">
            <p className="text-muted-foreground text-sm">No check data yet</p>
            <p className="text-muted-foreground/50 text-xs mt-1">
              Runs daily once 20+ trades are closed. First check after tomorrow's Celery beat.
            </p>
          </div>
        )}
      </div>

      {/* ── What to expect (Monte Carlo) ── */}
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="flex items-start justify-between mb-1">
          <div>
            <h3 className="text-sm font-semibold">What to Expect</h3>
            <p className="text-[11px] text-muted-foreground/60 mt-0.5 leading-relaxed max-w-lg">
              Replays your trade history {mc ? (mc.n_simulations ?? 0).toLocaleString() : '10,000'} times in random order to show
              the realistic range of outcomes going forward.
            </p>
          </div>
          <button
            onClick={() => refreshMC()}
            disabled={mcFetching}
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors disabled:opacity-40 shrink-0 ml-4 mt-0.5"
          >
            <RefreshCw size={12} className={mcFetching ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>

        {mc ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
            <MCCard
              label="Most likely return"
              value={`${((mc.median_return ?? 0) * 100).toFixed(1)}%`}
              sub="The middle outcome across all simulations"
              color="text-green-400"
            />
            <MCCard
              label="Realistic range"
              value={`${((mc.p5_return ?? 0) * 100).toFixed(1)}% – ${((mc.p95_return ?? 0) * 100).toFixed(1)}%`}
              sub="9 out of 10 simulations land here"
            />
            <MCCard
              label="Typical worst dip"
              value={`${((mc.median_max_drawdown ?? 0) * 100).toFixed(1)}%`}
              sub="Expected worst drawdown you'll likely see"
              color="text-amber-400"
            />
            <MCCard
              label="Blowup risk"
              value={`${((mc.risk_of_ruin ?? 0) * 100).toFixed(1)}%`}
              sub="Chance of losing 50%+ of account"
              color={(mc.risk_of_ruin ?? 0) > 0.05 ? 'text-red-400' : 'text-green-400'}
            />
          </div>
        ) : (
          <div className="mt-4 text-center py-6 border border-dashed border-border rounded-lg">
            <p className="text-muted-foreground text-sm">No simulation data yet</p>
            <p className="text-muted-foreground/50 text-xs mt-1">Needs at least a few closed trades to run.</p>
          </div>
        )}
      </div>
    </div>
  )
}
