import { useSystemHealth } from '../api/hooks'
import { useWeightsStore, useSystemStore } from '../store'
import { Shield, Clock, Activity, BarChart2 } from 'lucide-react'

function utcToET(h: number, m: number): string {
  const d = new Date()
  d.setUTCHours(h, m, 0, 0)
  return d.toLocaleTimeString('en-US', {
    timeZone: 'America/New_York',
    hour: 'numeric', minute: '2-digit', hour12: true,
  })
}

function Section({ title, sub, icon: Icon, children }: {
  title: string; sub?: string; icon: React.ElementType; children: React.ReactNode
}) {
  return (
    <div className="bg-card border border-border rounded-lg p-5">
      <div className="flex items-start gap-3 mb-4">
        <div className="w-8 h-8 rounded-md bg-primary/10 flex items-center justify-center shrink-0 mt-0.5">
          <Icon size={15} className="text-primary" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          {sub && <p className="text-[11px] text-muted-foreground/60 mt-0.5">{sub}</p>}
        </div>
      </div>
      {children}
    </div>
  )
}

function InfoRow({ label, value, note, badge }: {
  label: string; value: string; note?: string; badge?: 'amber' | 'red' | 'green' | 'neutral'
}) {
  const badgeClass = badge === 'red'     ? 'bg-red-500/15 text-red-400 border border-red-500/20'
    : badge === 'amber'   ? 'bg-amber-400/15 text-amber-400 border border-amber-400/20'
    : badge === 'green'   ? 'bg-green-500/15 text-green-400 border border-green-500/20'
    : 'bg-muted text-muted-foreground'
  return (
    <div className="flex justify-between items-start py-2.5 border-b border-border/40 last:border-0 gap-4">
      <div className="min-w-0">
        <div className="text-sm text-foreground">{label}</div>
        {note && <div className="text-[11px] text-muted-foreground/60 mt-0.5 leading-relaxed">{note}</div>}
      </div>
      <span className={`font-mono text-xs px-2 py-1 rounded-md shrink-0 ${badgeClass}`}>{value}</span>
    </div>
  )
}

function SkeletonRow() {
  return (
    <div className="flex justify-between items-center py-2.5 border-b border-border/40 last:border-0">
      <div className="skeleton h-3.5 w-40" />
      <div className="skeleton h-3.5 w-20" />
    </div>
  )
}

const PAIR_NOTES: Record<string, string> = {
  EUR_USD: 'Most liquid pair — tight spreads, strong trends',
  GBP_USD: 'High volatility, clear daily ranges',
  NZD_USD: 'Clean reversals at London close',
  USD_CAD: 'Trades at 75% position size',
  EUR_JPY: 'Trades at 75% position size',
  AUD_USD: 'Good diversification from EUR/GBP',
  USD_JPY: 'Trades at half position size — wider swings',
}

const PAIR_COLOR: Record<string, string> = {
  EUR_USD: 'text-blue-400',
  GBP_USD: 'text-purple-400',
  NZD_USD: 'text-cyan-400',
  USD_CAD: 'text-red-400',
  EUR_JPY: 'text-amber-400',
  AUD_USD: 'text-green-400',
}

export default function Settings() {
  const { data } = useSystemHealth()
  const { balance: wsBalance, equity: wsEquity } = useSystemStore()
  const { thresholds, risk, instruments } = useWeightsStore()

  const pct = (v: number) => `${Math.round(v * 100)}%`

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6 max-w-2xl">
      <div>
        <h1 className="text-xl font-bold">Settings</h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Read-only — changes require editing the config file and restarting.
        </p>
      </div>

      {/* Safety limits */}
      <Section icon={Shield} title="Safety Limits" sub="Automatic circuit breakers that protect the account from large losses">
        <InfoRow
          label="Max risk per trade"
          note="Never risk more than this percentage of the account on a single trade"
          value={`${pct(risk.max_risk_per_trade)} of account`}
          badge="neutral"
        />
        <InfoRow
          label={`Reduce size at ${pct(risk.drawdown_reduce_pct)} drawdown`}
          note="Position sizes are automatically halved when the account drops this far from its peak"
          value={`${pct(risk.drawdown_reduce_pct)} → half size`}
          badge="amber"
        />
        <InfoRow
          label={`Halt trading at ${pct(risk.drawdown_halt_pct)} drawdown`}
          note="All trading stops until manually reviewed if the account drops this far"
          value={`${pct(risk.drawdown_halt_pct)} → full stop`}
          badge="red"
        />
        <InfoRow
          label="Monthly loss limit"
          note={`Trading pauses for the rest of the month after a ${pct(risk.monthly_halt_pct)} loss`}
          value={`${pct(risk.monthly_halt_pct)} / month`}
          badge="red"
        />
        <InfoRow
          label="Daily loss limit"
          note={`Stops trading for the rest of the day after a ${pct(risk.daily_loss_limit_pct)} loss`}
          value={`${pct(risk.daily_loss_limit_pct)} / day`}
          badge="amber"
        />
        <InfoRow
          label="Wide spread protection"
          note="Skips trades where the bid/ask spread is more than this multiple of the session median"
          value={`${risk.spread_spike_multiplier}× normal`}
          badge="neutral"
        />
      </Section>

      {/* When it trades */}
      <Section icon={Clock} title="When It Trades" sub="Two separate strategies run at different times of day">
        <InfoRow
          label="Morning session"
          note="Trend-following trades during the busiest part of the European session"
          value={`${utcToET(7, 15)} – ${utcToET(12, 0)} ET`}
          badge="green"
        />
        <InfoRow
          label="Morning — minimum confidence"
          note={`The setup score must reach ${pct(thresholds.london)} before a morning trade is placed`}
          value={`${pct(thresholds.london)} confidence`}
          badge="neutral"
        />
        <InfoRow
          label="Morning — exits"
          note="Stop loss at ~1.5× the typical hourly range, take profit at ~2× the range"
          value="1.5× stop / 2× target"
          badge="neutral"
        />
        <InfoRow
          label="Evening reversal window"
          note="London Close Reversal — fades the day's move as New York heads into close"
          value={`${utcToET(17, 0)} – ${utcToET(20, 0)} ET`}
          badge="amber"
        />
        <InfoRow
          label="Evening — minimum confidence"
          note={`Reversal setups need a lower score (${pct(thresholds.lcr)}) because the edge comes from exhaustion, not trend`}
          value={`${pct(thresholds.lcr)} confidence`}
          badge="neutral"
        />
        <InfoRow
          label="Evening — profit target"
          note="Takes profit at the midpoint of today's London price range"
          value="London range midpoint"
          badge="neutral"
        />
      </Section>

      {/* System status */}
      <Section icon={Activity} title="System Status" sub="Live connection and account state">
        {(data || wsBalance > 0) ? (
          <>
            <InfoRow
              label="Account balance"
              note="Cash deposited — doesn't include open trade P&L"
              value={`$${(wsBalance || data?.account_balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              badge="neutral"
            />
            <InfoRow
              label="Account equity"
              note="Balance including unrealised profit or loss on open trades"
              value={`$${(wsEquity || data?.account_equity || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              badge={(wsEquity || data?.account_equity || 0) >= (wsBalance || data?.account_balance || 0) ? 'green' : 'red'}
            />
            <InfoRow
              label="Open trades"
              value={String(data?.open_positions ?? '—')}
              badge="neutral"
            />
            <InfoRow
              label="Live price feed"
              note="Streaming real-time prices from the broker"
              value={data?.stream_connected ? 'Connected' : 'Offline'}
              badge={data?.stream_connected ? 'green' : 'red'}
            />
          </>
        ) : (
          <><SkeletonRow /><SkeletonRow /><SkeletonRow /><SkeletonRow /></>
        )}
      </Section>

      {/* Pairs */}
      <Section icon={BarChart2} title="Pairs Being Traded" sub="Selected for liquidity, low spreads, and confirmed edge in historical tests">
        <div className="grid grid-cols-2 gap-2">
          {instruments.map(instr => (
            <div key={instr} className="bg-muted/40 rounded-lg p-3 flex items-start gap-2.5">
              <span className={`font-mono font-bold text-sm mt-0.5 ${PAIR_COLOR[instr] ?? 'text-foreground'}`}>
                {instr.replace('_', '/')}
              </span>
              <span className="text-[11px] text-muted-foreground/70 leading-relaxed">
                {PAIR_NOTES[instr] ?? 'Active pair'}
              </span>
            </div>
          ))}
        </div>
      </Section>

      <div className="bg-amber-400/10 border border-amber-400/30 rounded-lg p-4 text-xs text-amber-300 leading-relaxed">
        <span className="font-semibold">This system trades automatically.</span>{' '}
        To change any of these settings, edit the <code className="bg-amber-400/10 px-1 py-0.5 rounded text-amber-200">.env</code> config file and restart the engine.
        There is no manual override by design — once running, it manages itself.
      </div>
    </div>
  )
}
