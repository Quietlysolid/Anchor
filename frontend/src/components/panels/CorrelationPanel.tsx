import { useMarketContext } from '../../api/hooks'

function colLabel(pair: string): string {
  const [base, quote] = pair.split('_')
  if (quote === 'USD') return base
  if (base === 'USD') return quote
  return `${base.slice(0, 2)}/${quote.slice(0, 2)}`
}

interface PairRel { a: string; b: string; corr: number }

function getRelationships(corr: Record<string, Record<string, number>>): PairRel[] {
  const pairs = Object.keys(corr)
  const out: PairRel[] = []
  for (let i = 0; i < pairs.length; i++) {
    for (let j = i + 1; j < pairs.length; j++) {
      const v = corr[pairs[i]]?.[pairs[j]]
      if (v !== undefined && !isNaN(v)) out.push({ a: pairs[i], b: pairs[j], corr: v })
    }
  }
  return out.sort((x, y) => Math.abs(y.corr) - Math.abs(x.corr))
}

function corrColor(v: number): string {
  const abs = Math.abs(v)
  if (abs < 0.3)  return 'bg-muted/30 text-muted-foreground'
  if (v > 0)      return abs > 0.7 ? 'bg-red-500/25 text-red-300' : 'bg-red-500/10 text-red-400'
  return abs > 0.7 ? 'bg-blue-500/25 text-blue-300' : 'bg-blue-500/10 text-blue-400'
}

export function CorrelationPanel() {
  const { data: ctx, isLoading } = useMarketContext()
  const corr  = ctx?.correlation ?? {}
  const pairs = Object.keys(corr)
  const rels  = getRelationships(corr)
  const blocked = rels.filter(r => r.corr > 0.70)
  const notable = rels.filter(r => Math.abs(r.corr) >= 0.50)

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold">Correlation Guard</h3>
        <p className="text-[11px] text-muted-foreground/60 mt-0.5 leading-relaxed">
          Stops the system from opening two positions that are essentially the same bet. Based on 30 days of price data.
        </p>
      </div>

      {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}
      {!isLoading && pairs.length === 0 && (
        <p className="text-xs text-muted-foreground">Insufficient data</p>
      )}

      {!isLoading && pairs.length > 0 && (
        <>
          {/* ── Pairs the system won't hold simultaneously ── */}
          <div className="space-y-1.5">
            <p className="text-[10px] text-muted-foreground/50 uppercase tracking-wider">
              Too similar — won't hold both at once
            </p>
            {blocked.length === 0 ? (
              <p className="text-xs text-green-400/70">✓ No pairs above the 70% threshold right now</p>
            ) : (
              blocked.map(r => (
                <div key={`${r.a}-${r.b}`} className="flex items-center gap-2 bg-red-500/8 border border-red-500/15 rounded-lg px-3 py-2">
                  <span className="text-xs font-mono text-red-300/80">
                    {r.a.replace('_', '/')} ↔ {r.b.replace('_', '/')}
                  </span>
                  <span className="ml-auto text-[11px] text-red-400 font-mono shrink-0">
                    {Math.round(r.corr * 100)}% similar
                  </span>
                </div>
              ))
            )}
          </div>

          {/* ── Plain-English explanation ── */}
          <div className="bg-muted/30 rounded-lg px-3 py-2.5 text-[11px] text-muted-foreground/70 leading-relaxed">
            If EUR/USD and GBP/USD both move in the same direction 80% of the time, buying both is really just one big USD bet — not two independent trades. If the USD moves against you, both lose at once.
          </div>

          {/* ── Notable relationships ── */}
          {notable.length > 0 && (
            <div className="space-y-2">
              <p className="text-[10px] text-muted-foreground/50 uppercase tracking-wider">
                Strongest relationships
              </p>
              {notable.map(r => {
                const pct = Math.round(r.corr * 100)
                const isPos = r.corr > 0
                return (
                  <div key={`${r.a}-${r.b}`} className="flex items-center gap-2">
                    <span className="text-[11px] font-mono text-muted-foreground w-32 shrink-0">
                      {r.a.replace('_', '/')} ↔ {r.b.replace('_', '/')}
                    </span>
                    <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full ${isPos ? 'bg-red-400/60' : 'bg-blue-400/60'}`}
                        style={{ width: `${Math.abs(pct)}%` }}
                      />
                    </div>
                    <span className={`text-[11px] font-mono w-9 text-right shrink-0 ${isPos ? 'text-red-400' : 'text-blue-400'}`}>
                      {pct > 0 ? '+' : ''}{pct}%
                    </span>
                    <span className="text-[10px] text-muted-foreground/40 w-24 text-right shrink-0">
                      {isPos ? 'move together' : 'move opposite'}
                    </span>
                  </div>
                )
              })}
            </div>
          )}

          {/* ── Full matrix (collapsed by default) ── */}
          <details className="group">
            <summary className="text-[10px] text-muted-foreground/40 cursor-pointer hover:text-muted-foreground/60 transition-colors list-none flex items-center gap-1 select-none">
              <span className="group-open:hidden">▶</span>
              <span className="hidden group-open:inline">▼</span>
              &nbsp;Full matrix
            </summary>
            <div className="overflow-x-auto mt-2">
              <table className="text-xs w-full border-collapse">
                <thead>
                  <tr>
                    <th className="w-16" />
                    {pairs.map(p => (
                      <th key={p} className="text-center text-muted-foreground font-mono font-normal pb-1 px-1 min-w-[44px]">
                        {colLabel(p)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pairs.map(row => (
                    <tr key={row}>
                      <td className="text-muted-foreground font-mono pr-2 py-1 text-right whitespace-nowrap text-[11px]">
                        {row.replace('_', '/')}
                      </td>
                      {pairs.map(col => {
                        const v = corr[row]?.[col] ?? 0
                        const isDiag = row === col
                        return (
                          <td
                            key={col}
                            title={`${row.replace('_','/')} vs ${col.replace('_','/')}: ${v.toFixed(2)}`}
                            className={`text-center py-1 px-1 rounded font-mono text-xs ${isDiag ? 'text-muted-foreground/40' : corrColor(v)}`}
                          >
                            {isDiag ? '—' : v.toFixed(2)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  )
}
