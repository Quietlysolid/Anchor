import { usePerformance } from '../../api/hooks'
import { useWeightsStore } from '../../store'

type Status = 'good' | 'warn' | 'bad'

function Metric({ label, value, desc, status }: {
  label: string; value: string; desc: string; status?: Status
}) {
  const valueColor = status === 'good' ? 'text-green-400'
    : status === 'warn' ? 'text-amber-400'
    : status === 'bad'  ? 'text-red-400'
    : 'text-foreground'
  return (
    <div title={desc}>
      <div className={`text-lg font-bold font-mono ${valueColor}`}>{value}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
      <div className="text-[10px] text-muted-foreground/40 mt-0.5 leading-tight">{desc}</div>
    </div>
  )
}

function skeleton(w = 'w-12') {
  return <div className={`skeleton h-5 ${w}`} />
}

export function RiskMetricsPanel() {
  const { data, isLoading } = usePerformance()
  const t = useWeightsStore(s => s.targets)

  const sharpe = data?.sharpe_ratio  ?? 0
  const wr     = data?.win_rate      ?? 0
  const pf     = data?.profit_factor ?? 0
  const dd     = data?.max_drawdown  ?? 0
  const calmar = data?.calmar_ratio  ?? 0
  const pnl    = data?.net_pnl       ?? 0

  const statuses: Status[] = data ? [
    sharpe >= t.sharpe_good        ? 'good' : sharpe >= t.sharpe_warn       ? 'warn' : 'bad',
    wr     >= t.win_rate_good      ? 'good' : wr     >= t.win_rate_warn     ? 'warn' : 'bad',
    pf     >= t.profit_factor_good ? 'good' : pf     >= t.profit_factor_warn? 'warn' : 'bad',
    dd     <= t.drawdown_good      ? 'good' : dd     <= t.drawdown_halt     ? 'warn' : 'bad',
    calmar >= 1.0                  ? 'good' : calmar >= 0.5                 ? 'warn' : 'bad',
    pnl    >  0                    ? 'good' : pnl    <  0                   ? 'bad'  : 'warn',
  ] : []

  const goodCount = statuses.filter(s => s === 'good').length
  const badCount  = statuses.filter(s => s === 'bad').length

  const summaryText = !data ? null
    : badCount  >= 2 ? { label: 'Performance below targets', color: 'text-red-400 bg-red-400/8 border-red-400/20' }
    : badCount  === 1 ? { label: 'One metric needs attention', color: 'text-amber-400 bg-amber-400/8 border-amber-400/20' }
    : goodCount >= 4 ? { label: 'Performing well', color: 'text-green-400 bg-green-400/8 border-green-400/20' }
    : { label: 'Results building', color: 'text-muted-foreground bg-muted/30 border-border' }

  return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold">Performance Scorecard</h3>
      <p className="text-[11px] text-muted-foreground/60 mt-0.5 mb-3">
        How well the system is trading since launch
      </p>

      {/* Health summary */}
      {summaryText && (
        <div className={`text-xs font-medium px-2.5 py-1.5 rounded-lg border mb-3 ${summaryText.color}`}>
          {summaryText.label}
        </div>
      )}

      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        {isLoading ? (
          <>
            {[...Array(6)].map((_, i) => (
              <div key={i} className="space-y-1">
                {skeleton('w-16')}
                <div className="skeleton h-2.5 w-20" />
              </div>
            ))}
          </>
        ) : (
          <>
            <Metric
              label="Trades won"
              value={data ? `${(wr * 100).toFixed(1)}%` : '—'}
              desc={`What % of trades closed in profit · target ${Math.round(t.win_rate_good * 100)}%+`}
              status={statuses[1]}
            />
            <Metric
              label="Wins vs losses"
              value={data ? `${pf.toFixed(2)}×` : '—'}
              desc={`For every $1 lost, the system won $${data ? pf.toFixed(2) : '?'} · target 1.3×+`}
              status={statuses[2]}
            />
            <Metric
              label="Biggest dip"
              value={data ? `${(dd * 100).toFixed(1)}%` : '—'}
              desc={`Largest account drop from a peak before recovering · halt at ${Math.round(t.drawdown_halt * 100)}%`}
              status={statuses[3]}
            />
            <Metric
              label="Net profit"
              value={data ? `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}` : '—'}
              desc="Total dollars made or lost since the system started"
              status={statuses[5]}
            />
            <Metric
              label="Return quality"
              value={data ? sharpe.toFixed(2) : '—'}
              desc={`How smooth the gains are vs how wild the swings · target ${t.sharpe_good}+`}
              status={statuses[0]}
            />
            <Metric
              label="Consistency"
              value={data ? calmar.toFixed(2) : '—'}
              desc="Annual return divided by the worst drawdown — higher means steadier gains"
              status={statuses[4]}
            />
          </>
        )}
      </div>

      {/* Bottom row */}
      <div className="mt-4 pt-3 border-t border-border grid grid-cols-2 gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Avg winning trade</span>
          <span className="text-green-400 font-mono">{data ? `+$${(data.avg_win_pips ?? 0).toFixed(2)}` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Avg losing trade</span>
          <span className="text-red-400 font-mono">{data ? `-$${Math.abs(data.avg_loss_pips ?? 0).toFixed(2)}` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Total trades</span>
          <span className="font-mono">{data?.total_trades ?? '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Fees paid</span>
          <span className="font-mono text-muted-foreground">{data ? `$${(data.total_commission ?? 0).toFixed(2)}` : '—'}</span>
        </div>
      </div>
    </div>
  )
}
