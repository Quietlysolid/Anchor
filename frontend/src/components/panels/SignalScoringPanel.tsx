import { useSignalStore, useWeightsStore } from '../../store'
import { suppressionText } from '../../utils/session'
import type { Signal } from '../../types'

const LONDON_FACTORS: { key: keyof Signal; label: string; desc: string }[] = [
  { key: 'rsi_score',             label: 'Momentum',          desc: 'Price moving in the right direction with force' },
  { key: 'bb_kc_score',           label: 'Price Compression', desc: 'Market coiling before a breakout' },
  { key: 'adx_score',             label: 'Trend Strength',    desc: 'A clear, strong trend to trade with' },
  { key: 'sr_score',              label: 'Key Level',         desc: 'Price near a meaningful support or resistance' },
  { key: 'mtf_score',             label: 'Chart Alignment',   desc: '1h, 4h, and daily charts all agree' },
  { key: 'csi_score',             label: 'Trader Sentiment',  desc: 'Retail traders are on the wrong side (contrarian edge)' },
  { key: 'cot_score',             label: 'Big Money',         desc: 'Institutional funds positioned with this trade (CFTC weekly report)' },
  { key: 'rate_divergence_score', label: 'Rate Edge',         desc: 'Interest rate difference favours this direction' },
  { key: 'order_book_score',      label: 'Order Clusters',    desc: 'Pending stop-losses and limit orders clustered above vs below price — price gets pulled toward the heavier side' },
  { key: 'cme_flow_score',        label: 'Futures Flow',      desc: 'Volume trend on CME FX futures (6E/6B/6J) — rising volume in a direction means institutional participation' },
  { key: 'fx_options_score',      label: 'Options Skew',      desc: 'Whether FX options traders are paying more for calls (upside bets) or puts (downside hedges)' },
  { key: 'econ_surprise_score',   label: 'Macro Surprise',    desc: 'Whether recent data releases (CPI, NFP, PMI) beat or missed consensus — a beat in the trade direction confirms the move' },
  { key: 'cross_asset_score',    label: 'Risk Regime',       desc: 'Whether global equity (SPY) and gold (GLD) flows confirm this direction — risk-on lifts AUD/NZD/CAD and sells JPY; risk-off reverses' },
]

const LCR_FACTORS: { key: keyof Signal; label: string; desc: string }[] = [
  { key: 'adx_score',   label: 'At Range Extreme',  desc: 'Price pushed to the top or bottom of today\'s London range' },
  { key: 'rsi_score',   label: 'Momentum Fading',   desc: 'The move is running out of steam' },
  { key: 'bb_kc_score', label: 'Price Rejection',   desc: 'Candle showed a sharp rejection — price tried to extend but got pushed back' },
  { key: 'sr_score',    label: 'Clean Range',        desc: 'Today\'s London range was well-defined with clear boundaries' },
]

function FactorBar({ label, desc, score }: { label: string; desc: string; score: number | null }) {
  const pct      = score !== null ? Math.round(score * 100) : 0
  const barColor = pct >= 70 ? 'bg-green-400' : pct >= 45 ? 'bg-amber-400' : 'bg-red-400/60'
  const txtColor = pct >= 70 ? 'text-green-400' : pct >= 45 ? 'text-amber-400' : 'text-red-400/80'
  return (
    <div className="group" title={desc}>
      <div className="flex justify-between items-center mb-0.5">
        <span className="text-[11px] text-muted-foreground group-hover:text-foreground transition-colors">{label}</span>
        <span className={`text-[11px] font-mono tabular-nums ${txtColor}`}>{score !== null ? `${pct}%` : '—'}</span>
      </div>
      <div className="h-1 bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-300 ${barColor}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// Compact one-line summary for a single pair in the overview grid
function PairRow({ sig, isSelected, threshold }: { sig: Signal | undefined; isSelected: boolean; threshold: number }) {
  if (!sig) {
    return (
      <div className={`flex items-center justify-between px-2 py-1.5 rounded-md ${isSelected ? 'bg-muted/60' : ''}`}>
        <span className="text-xs font-mono text-muted-foreground/50">—</span>
        <span className="text-[11px] text-muted-foreground/30">no data</span>
      </div>
    )
  }

  const score   = sig.confluence_score ?? 0
  const pct     = Math.round(score * 100)
  const passes  = score >= threshold && !sig.suppressed
  const blocked = sig.suppressed

  const dirBadge = blocked
    ? <span className="text-[10px] text-muted-foreground/40 italic">blocked</span>
    : sig.direction === 'LONG'
    ? <span className="text-[11px] font-bold text-green-400">↑ Buy</span>
    : <span className="text-[11px] font-bold text-red-400">↓ Sell</span>

  const scoreColor = passes ? 'text-green-400' : blocked ? 'text-muted-foreground/30' : pct >= Math.round((threshold - 0.10) * 100) ? 'text-amber-400' : 'text-muted-foreground/50'

  return (
    <div className={`flex items-center gap-2 px-2 py-1.5 rounded-md transition-colors ${isSelected ? 'bg-primary/10 ring-1 ring-primary/20' : 'hover:bg-muted/40'}`}>
      <span className={`text-xs font-mono w-14 shrink-0 ${isSelected ? 'text-foreground font-medium' : 'text-muted-foreground'}`}>
        {sig.instrument.replace('_', '/')}
      </span>
      <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${passes ? 'bg-green-400' : blocked ? 'bg-muted-foreground/20' : 'bg-amber-400/60'}`}
          style={{ width: `${Math.min(100, pct)}%` }}
        />
      </div>
      <span className={`text-[11px] font-mono tabular-nums w-8 text-right shrink-0 ${scoreColor}`}>
        {blocked ? '—' : `${pct}%`}
      </span>
      <span className="w-14 text-right shrink-0">{dirBadge}</span>
    </div>
  )
}

interface Props { instrument?: string }

export function SignalScoringPanel({ instrument }: Props) {
  const { signals }   = useSignalStore()
  const instruments   = useWeightsStore(s => s.instruments)
  const thresholds    = useWeightsStore(s => s.thresholds)

  // Latest signal per pair
  const byPair: Record<string, Signal | undefined> = {}
  for (const instr of instruments) {
    byPair[instr] = signals.find(s => s.instrument === instr)
  }

  const selected = instrument ?? instruments[0]
  const latest   = byPair[selected]

  const noSignalsAtAll = instruments.every(i => !byPair[i])

  // ── Detail section for selected pair ─────────────────────────
  const isLCR       = latest?.session === 'NY_LCR'
  const threshold   = isLCR ? thresholds.lcr : thresholds.london
  const score       = latest?.confluence_score ?? 0
  const passes      = score >= threshold
  const suppressed  = latest?.suppressed ?? false
  const scorePct    = Math.round(score * 100)
  const threshPct   = Math.round(threshold * 100)
  const needed      = Math.max(0, threshPct - scorePct)
  const factors     = isLCR ? LCR_FACTORS : LONDON_FACTORS

  return (
    <div className="bg-card rounded-lg p-4 border border-border h-full flex flex-col gap-3">

      {/* ── Header ── */}
      <div>
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold">All Signals</h3>
          <span className="text-xs text-primary font-mono">{selected.replace('_', '/')}</span>
        </div>
        <p className="text-[11px] text-muted-foreground/60 mt-0.5">
          Live setup scores — breakdown updates when you switch pairs above
        </p>
      </div>

      {/* ── Overview: all pairs ── */}
      <div className="space-y-0.5">
        {instruments.map(instr => (
          <PairRow
            key={instr}
            sig={byPair[instr]}
            isSelected={instr === selected}
            threshold={byPair[instr]?.session === 'NY_LCR' ? thresholds.lcr : thresholds.london}
          />
        ))}
        {noSignalsAtAll && (
          <p className="text-[10px] text-muted-foreground/40 text-center pt-1.5">
            Signals generate during London (3:15–8 AM ET) and LCR (1–4 PM ET)
          </p>
        )}
      </div>

      {/* ── Detail for selected pair ── */}
      {!latest && (
        <div className="pt-3 border-t border-border text-center py-4">
          <p className="text-xs text-muted-foreground/40">
            No active signal for {selected.replace('_', '/')}
          </p>
          <p className="text-[10px] text-muted-foreground/30 mt-1">
            Select a pair above the chart to check its last reading
          </p>
        </div>
      )}
      {latest && (
        <div className="flex flex-col gap-2.5 pt-3 border-t border-border">

          {/* Status + direction */}
          {suppressed ? (
            <div className="rounded-lg bg-muted/40 px-3 py-2 text-xs text-muted-foreground leading-relaxed">
              <span className="font-medium text-foreground/60">{selected.replace('_', '/')} — not trading: </span>
              {suppressionText(latest.suppression_reason)}
            </div>
          ) : (
            <div className={`rounded-lg px-3 py-2.5 flex items-center gap-2.5 ${
              latest.direction === 'LONG'
                ? 'bg-green-400/10 border border-green-400/20'
                : 'bg-red-400/10 border border-red-400/20'
            }`}>
              <span className={`text-xl font-bold ${latest.direction === 'LONG' ? 'text-green-400' : 'text-red-400'}`}>
                {latest.direction === 'LONG' ? '↑' : '↓'}
              </span>
              <div className="flex-1">
                <div className="text-sm font-bold">
                  {latest.direction === 'LONG' ? 'Buy' : 'Sell'} {selected.replace('_', '/')}
                </div>
                <div className="text-[10px] text-muted-foreground/60">
                  {passes
                    ? 'Score is high enough — system may place this trade'
                    : `Needs ${needed} more points to trigger (${scorePct}% of ${threshPct}% required)`
                  }
                </div>
              </div>
              <div className="text-right shrink-0">
                <div className={`text-lg font-bold font-mono ${passes ? 'text-green-400' : 'text-amber-400'}`}>
                  {scorePct}%
                </div>
                <div className="text-[10px] text-muted-foreground/40">of {threshPct}%</div>
              </div>
            </div>
          )}

          {/* Progress bar */}
          {!suppressed && (
            <div>
              <div className="relative h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all duration-500 ${passes ? 'bg-green-400' : 'bg-amber-400'}`}
                  style={{ width: `${Math.min(100, scorePct)}%` }}
                />
                <div className="absolute top-0 bottom-0 w-0.5 bg-white/20" style={{ left: `${threshPct}%` }} />
              </div>
              <div className="flex justify-between mt-1 text-[10px] text-muted-foreground/30">
                <span>0</span>
                <span>{threshPct}% to trade</span>
                <span>100</span>
              </div>
            </div>
          )}

          {/* Factor breakdown */}
          <div className="space-y-1.5">
            <p className="text-[10px] text-muted-foreground/40 uppercase tracking-wider">What the system checked</p>
            {factors.map(({ key, label, desc }) => (
              <FactorBar key={String(key)} label={label} desc={desc} score={(latest as any)[key] ?? null} />
            ))}
          </div>

          {/* LCR range */}
          {isLCR && latest.london_high != null && latest.london_low != null && (
            <div className="pt-2 border-t border-border space-y-1.5 text-xs">
              <p className="text-[10px] text-muted-foreground/40 uppercase tracking-wider">Today's London range</p>
              <div className="flex justify-between">
                <span className="text-muted-foreground">High / Low</span>
                <span className="font-mono">
                  {latest.london_high.toFixed(latest.london_high > 10 ? 3 : 5)} / {latest.london_low.toFixed(latest.london_low > 10 ? 3 : 5)}
                </span>
              </div>
              {latest.london_mid != null && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Profit target</span>
                  <span className="font-mono text-purple-400">{latest.london_mid.toFixed(latest.london_mid > 10 ? 3 : 5)}</span>
                </div>
              )}
            </div>
          )}

          {/* Footer */}
          <div className="text-[10px] text-muted-foreground/30 flex gap-3 flex-wrap">
            <span>{isLCR ? 'Evening reversal' : latest.session === 'LONDON' ? 'Morning trend' : 'Outside session'}</span>
            {!isLCR && latest.regime_state && (
              <span>Market: {latest.regime_state.toLowerCase()}</span>
            )}
            {latest.ml_confidence != null && (
              <span>AI: {Math.round(latest.ml_confidence * 100)}%</span>
            )}
            {latest.news_multiplier != null && latest.news_multiplier < 1 && (
              <span className="text-amber-400/60">Half-size — medium event nearby</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
