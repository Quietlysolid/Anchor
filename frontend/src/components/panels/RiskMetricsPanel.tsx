import { usePerformance } from '../../api/hooks'

function Metric({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="text-center">
      <div className={`text-lg font-bold font-mono ${warn ? 'text-red-400' : 'text-foreground'}`}>{value}</div>
      <div className="text-xs text-muted-foreground mt-0.5">{label}</div>
    </div>
  )
}

export function RiskMetricsPanel() {
  const { data } = usePerformance()

  return (
    <div className="bg-card rounded-lg p-4 border border-border">
      <h3 className="text-sm font-semibold mb-4">Risk Metrics</h3>
      <div className="grid grid-cols-3 gap-4">
        <Metric label="Sharpe" value={data ? (data.sharpe_ratio ?? 0).toFixed(2) : '—'} warn={data ? (data.sharpe_ratio ?? 0) < 0 : false} />
        <Metric label="Win Rate" value={data ? `${((data.win_rate ?? 0) * 100).toFixed(1)}%` : '—'} />
        <Metric label="Profit Factor" value={data ? (data.profit_factor ?? 0).toFixed(2) : '—'} warn={data ? (data.profit_factor ?? 0) < 1 : false} />
        <Metric label="Max DD" value={data ? `${((data.max_drawdown ?? 0) * 100).toFixed(1)}%` : '—'} warn={data ? (data.max_drawdown ?? 0) > 0.08 : false} />
        <Metric label="Calmar" value={data ? (data.calmar_ratio ?? 0).toFixed(2) : '—'} />
        <Metric label="Net P&L" value={data ? `$${(data.net_pnl ?? 0).toFixed(2)}` : '—'} warn={data ? (data.net_pnl ?? 0) < 0 : false} />
      </div>
      <div className="mt-4 pt-3 border-t border-border grid grid-cols-2 gap-2 text-xs">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Avg Win</span>
          <span className="text-green-400 font-mono">{data ? `${(data.avg_win_pips ?? 0).toFixed(1)} pips` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Avg Loss</span>
          <span className="text-red-400 font-mono">{data ? `${(data.avg_loss_pips ?? 0).toFixed(1)} pips` : '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Total Trades</span>
          <span className="font-mono">{data?.total_trades ?? '—'}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Commission</span>
          <span className="font-mono text-muted-foreground">{data ? `$${(data.total_commission ?? 0).toFixed(2)}` : '—'}</span>
        </div>
      </div>
    </div>
  )
}
