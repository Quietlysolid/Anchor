import { useSystemHealth } from '../api/hooks'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-card border border-border rounded-lg p-5">
      <h3 className="text-sm font-semibold mb-4 text-foreground">{title}</h3>
      {children}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-2 border-b border-border/40 last:border-0 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-mono text-foreground">{value}</span>
    </div>
  )
}

export default function Settings() {
  const { data } = useSystemHealth()

  return (
    <div className="p-4 md:p-6 space-y-4 md:space-y-6 max-w-2xl">
      <h1 className="text-xl font-bold">System Settings</h1>

      <Section title="Risk Parameters (configured via .env)">
        <InfoRow label="Max Risk Per Trade"          value="1.0%" />
        <InfoRow label="Drawdown Reduce Threshold"   value="8%" />
        <InfoRow label="Drawdown Halt Threshold"     value="15%" />
        <InfoRow label="Daily Loss Halt"             value="3%" />
        <InfoRow label="Spread Block (vs median)"    value="3×" />
      </Section>

      <Section title="Strategy Configuration">
        <InfoRow label="London Trend — Session"      value="07:15 – 12:00 UTC" />
        <InfoRow label="London Trend — Threshold"    value="72% confluence" />
        <InfoRow label="London Trend — SL / TP"      value="1.5× ATR / 2.0× ATR" />
        <InfoRow label="LCR (NY) — Session"          value="17:00 – 19:59 UTC" />
        <InfoRow label="LCR (NY) — Threshold"        value="55% confluence" />
        <InfoRow label="LCR (NY) — TP target"        value="London session midpoint" />
      </Section>

      <Section title="System Status">
        <InfoRow label="Account Balance" value={data ? `$${data.account_balance.toFixed(2)}` : '—'} />
        <InfoRow label="Account Equity"  value={data ? `$${data.account_equity.toFixed(2)}`  : '—'} />
        <InfoRow label="Open Positions"  value={String(data?.open_positions ?? '—')} />
        <InfoRow label="Stream Status"   value={data?.stream_connected ? 'Connected' : 'Offline'} />
      </Section>

      <Section title="Active Instruments">
        {[
          { pair: 'EUR_USD', note: 'London trend + LCR' },
          { pair: 'GBP_USD', note: 'London trend + LCR' },
          { pair: 'USD_JPY', note: 'London trend + LCR · highest LCR PF (1.91)' },
        ].map(({ pair, note }) => (
          <div key={pair} className="flex justify-between items-center py-1.5 border-b border-border/40 last:border-0">
            <span className="text-sm font-mono">{pair.replace('_', '/')}</span>
            <span className="text-xs text-muted-foreground">{note}</span>
          </div>
        ))}
      </Section>

      <div className="bg-amber-400/10 border border-amber-400/30 rounded-lg p-4 text-xs text-amber-300">
        ⚠ This is a fully automated system. Risk parameters must be changed in the <code>.env</code> file
        and the engine restarted. There is no manual override by design.
      </div>
    </div>
  )
}