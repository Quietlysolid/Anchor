import { useMarketContext } from '../../api/hooks'

const TREND_ARROW: Record<string, string> = { UP: '↑', DOWN: '↓', NEUTRAL: '→' }
const TREND_COLOR: Record<string, string> = {
  UP: 'text-green-400', DOWN: 'text-red-400', NEUTRAL: 'text-muted-foreground',
}

export function MarketContextPanel() {
  const { data: ctx, isLoading } = useMarketContext()

  const vix        = ctx?.vix?.vix ?? null
  const dxy        = ctx?.dxy ?? null
  const atr        = ctx?.atr_profile ?? {}

  const vixReducing  = vix !== null && vix >= 25
  const dxyReducing  = dxy !== null && Math.abs(dxy.change_5d_pct) > 1.5
  const anythingActive = vixReducing || dxyReducing

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-4">
      <div>
        <h3 className="text-sm font-semibold">Market Conditions</h3>
        <p className="text-[11px] text-muted-foreground/60 mt-0.5">
          Filters that can reduce position sizes or block trades
        </p>
      </div>

      {isLoading && <p className="text-xs text-muted-foreground">Loading...</p>}

      {!isLoading && ctx && (
        <>
          {/* ── Summary banner ── */}
          {anythingActive ? (
            <div className="bg-amber-400/8 border border-amber-400/20 rounded-lg px-3 py-2.5 text-xs text-amber-300 space-y-0.5">
              <p className="font-semibold">⚠ Trading at reduced position size</p>
              {vixReducing  && <p className="opacity-80">Market fear is elevated (VIX {vix?.toFixed(1)}) — system is being cautious</p>}
              {dxyReducing  && <p className="opacity-80">Dollar moved sharply this week — evening trade sizes reduced</p>}
            </div>
          ) : (
            <div className="bg-green-400/8 border border-green-400/20 rounded-lg px-3 py-2 text-xs text-green-400">
              ✓ No filters active — trading at normal position size
            </div>
          )}

          {/* ── Dollar strength (DXY) ── */}
          <div className="space-y-1">
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs font-medium">Dollar strength</p>
                <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
                  A strong or weak USD affects all currency pairs. If it moves more than 1.5% in a week, evening trade sizes are reduced.
                </p>
              </div>
            </div>
            {dxy ? (
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-base font-mono font-semibold">{dxy.value.toFixed(2)}</span>
                <span className={`text-sm font-medium ${TREND_COLOR[dxy.trend]}`}>
                  {TREND_ARROW[dxy.trend]} {dxy.change_5d_pct > 0 ? '+' : ''}{dxy.change_5d_pct.toFixed(2)}% this week
                </span>
                {!dxyReducing && (
                  <span className="text-[11px] text-muted-foreground/50 ml-1">— no effect</span>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground/50 mt-1">Unavailable</p>
            )}
          </div>

          {/* ── Market fear (VIX) ── */}
          <div className="space-y-1">
            <div>
              <p className="text-xs font-medium">Market fear (VIX)</p>
              <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
                Measures how nervous global markets are. Above 25 = system trades smaller. Above 30 = markets are in stress.
              </p>
            </div>
            {vix !== null ? (
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-base font-mono font-semibold">{vix.toFixed(1)}</span>
                <span className={`text-sm font-medium ${vix >= 30 ? 'text-red-400' : vix >= 25 ? 'text-amber-400' : 'text-green-400'}`}>
                  {vix >= 30 ? 'High stress' : vix >= 25 ? 'Elevated — size reduced' : vix >= 20 ? 'Slightly elevated' : 'Normal'}
                </span>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground/50 mt-1">Unavailable</p>
            )}
          </div>

          {/* ── How active each pair is ── */}
          {Object.keys(atr).length > 0 && (
            <div className="space-y-1.5">
              <div>
                <p className="text-xs font-medium">How active each pair is</p>
                <p className="text-[10px] text-muted-foreground/60 leading-relaxed">
                  Compares today's movement to the 60-day average. Quiet = smaller moves than usual. Volatile = larger moves, higher risk.
                </p>
              </div>
              <div className="space-y-2 mt-2">
                {Object.entries(atr).map(([instr, a]) => (
                  <div key={instr} className="flex items-center gap-2">
                    <span className="text-xs font-mono text-muted-foreground w-16 shrink-0">{instr.replace('_', '/')}</span>
                    <div className="flex-1 bg-muted rounded-full h-1.5 overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          a.status === 'VOLATILE' ? 'bg-amber-400' :
                          a.status === 'QUIET'    ? 'bg-blue-400'  : 'bg-green-400'
                        }`}
                        style={{ width: `${Math.min(100, a.ratio * 70)}%` }}
                      />
                    </div>
                    <span className={`text-[11px] font-medium w-20 text-right shrink-0 ${
                      a.status === 'VOLATILE' ? 'text-amber-400' :
                      a.status === 'QUIET'    ? 'text-blue-400'  : 'text-green-400'
                    }`}>
                      {a.status === 'VOLATILE' ? 'More active' : a.status === 'QUIET' ? 'Quieter' : 'Normal'}
                    </span>
                    <span className="text-[10px] text-muted-foreground/40 w-20 text-right shrink-0" title={`Average: ${a.avg_atr_pips} pips/hr`}>
                      {a.current_atr_pips} pip/hr
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
