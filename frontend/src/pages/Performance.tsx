import { RadarChart, Radar, PolarGrid, PolarAngleAxis, ResponsiveContainer } from 'recharts'
import { usePerformance, useMonteCarlo, useEquityCurve } from '../api/hooks'
import { EquityCurveChart } from '../components/charts/EquityCurveChart'

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="text-2xl font-bold font-mono">{value}</div>
      <div className="text-sm text-muted-foreground mt-1">{label}</div>
      {sub && <div className="text-xs text-muted-foreground/60 mt-0.5">{sub}</div>}
    </div>
  )
}

export default function Performance() {
  const { data: perf } = usePerformance()
  const { data: mc }   = useMonteCarlo()
  const { data: eq }   = useEquityCurve()

  const radarData = perf ? [
    { metric: 'Win Rate',     value: (perf.win_rate ?? 0) * 100 },
    { metric: 'Sharpe',       value: Math.min((perf.sharpe_ratio ?? 0) * 33, 100) },
    { metric: 'Profit Factor',value: Math.min((perf.profit_factor ?? 0) * 33, 100) },
    { metric: 'Calmar',       value: Math.min((perf.calmar_ratio ?? 0) * 33, 100) },
    { metric: 'Low Drawdown', value: Math.max(0, 100 - (perf.max_drawdown ?? 0) * 500) },
  ] : []

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6">
      <h1 className="text-xl font-bold">Performance Analytics</h1>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Sharpe Ratio"   value={perf ? (perf.sharpe_ratio ?? 0).toFixed(2) : '—'} sub="Target > 1.0" />
        <StatCard label="Win Rate"       value={perf ? `${((perf.win_rate ?? 0)*100).toFixed(1)}%` : '—'} sub="Target > 47% blended" />
        <StatCard label="Profit Factor"  value={perf ? (perf.profit_factor ?? 0).toFixed(2) : '—'} sub="Target > 1.3" />
        <StatCard label="Max Drawdown"   value={perf ? `${((perf.max_drawdown ?? 0)*100).toFixed(1)}%` : '—'} sub="Alert > 8%" />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Equity Curve */}
        <div className="bg-card border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold mb-3">Equity Curve</h3>
          <EquityCurveChart data={eq ?? []} />
        </div>

        {/* Radar */}
        <div className="bg-card border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold mb-3">Strategy Profile</h3>
          <ResponsiveContainer width="100%" height={200}>
            <RadarChart data={radarData}>
              <PolarGrid stroke="hsl(217,33%,17%)" />
              <PolarAngleAxis dataKey="metric" tick={{ fill: '#64748b', fontSize: 10 }} />
              <Radar dataKey="value" stroke="#22c55e" fill="#22c55e" fillOpacity={0.25} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Monte Carlo */}
      {mc && (
        <div className="bg-card border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold mb-4">Monte Carlo Simulation ({(mc.n_simulations ?? 0).toLocaleString()} runs)</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center">
            <div>
              <div className="text-xl font-bold font-mono text-green-400">{((mc.median_return ?? 0) * 100).toFixed(1)}%</div>
              <div className="text-xs text-muted-foreground mt-1">Median Return</div>
            </div>
            <div>
              <div className="text-xl font-bold font-mono">{((mc.p5_return ?? 0) * 100).toFixed(1)}% – {((mc.p95_return ?? 0) * 100).toFixed(1)}%</div>
              <div className="text-xs text-muted-foreground mt-1">5th – 95th Percentile</div>
            </div>
            <div>
              <div className="text-xl font-bold font-mono text-amber-400">{((mc.median_max_drawdown ?? 0) * 100).toFixed(1)}%</div>
              <div className="text-xs text-muted-foreground mt-1">Median Max DD</div>
            </div>
            <div>
              <div className={`text-xl font-bold font-mono ${(mc.risk_of_ruin ?? 0) > 0.05 ? 'text-red-400' : 'text-green-400'}`}>
                {((mc.risk_of_ruin ?? 0) * 100).toFixed(1)}%
              </div>
              <div className="text-xs text-muted-foreground mt-1">Risk of Ruin</div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
