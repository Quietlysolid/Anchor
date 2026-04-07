import { SectionCard, pctStr, signedDollars, useDashboardSnapshot } from './dashboardShared'
import { EquityCurve } from '../components/charts/EquityCurve'

export default function PerformancePage() {
  const snapshot = useDashboardSnapshot()
  const performance = snapshot?.performance
  const readiness = snapshot?.readiness
  const failingChecks = readiness?.criteria?.filter((criterion) => !criterion.passed) ?? []

  return (
    <div className="mx-auto max-w-2xl px-4 pb-24 pt-6 sm:px-6">
      <SectionCard title="Performance">
        <div className="px-5 py-5 sm:px-6">
          <EquityCurve data={performance?.equity_curve ?? []} />
          <div className="mt-5 grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Metric label="1W return" value={pctStr(performance?.returns['1w_pct'])} />
            <Metric label="MTD return" value={pctStr(performance?.returns.mtd_pct)} />
            <Metric label="Since start" value={pctStr(performance?.returns.since_start_pct)} />
            <Metric label="Track record" value={`${performance?.track_record_days ?? 0} days`} />
            <Metric label="Current drawdown" value={pctStr(performance?.drawdown.current_pct)} />
            <Metric label="Max drawdown" value={pctStr(performance?.drawdown.max_pct)} />
            <Metric label="Realized MTD" value={signedDollars(performance?.realized_pl.mtd)} />
            <Metric label="Realized total" value={signedDollars(performance?.realized_pl.since_start)} />
          </div>
        </div>
      </SectionCard>

      <SectionCard title="Readiness">
        <div className="grid grid-cols-2 gap-4 px-5 py-5 sm:grid-cols-4 sm:px-6">
          <div className="col-span-2">
            <p className="text-[11px] text-anchor-fog/88">{readiness?.passed_checks ?? 0}/{readiness?.total_checks ?? 0} checks passed</p>
          </div>
          <Metric label="Automated cycles" value={String(readiness?.rebalance_count ?? 0)} />
          <Metric label="Ops incidents" value={String(readiness?.operational_incidents ?? 0)} />
        </div>
        {failingChecks.length > 0 && (
          <div className="border-t border-white/8 px-5 py-4 sm:px-6">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">What still blocks live</p>
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
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Live blockers</p>
            <p className="mt-2 text-sm text-anchor-win">No current blockers in the readiness criteria.</p>
          </div>
        )}
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
      return `Need ${remainingDays(value, target)} more days.`
    case 'Automated rebalance cycles':
      return `Need ${remainingCount(value, target)} verified cycles.`
    case 'Since-start return':
      return 'Needs positive performance before live.'
    case 'Max drawdown':
      return `Must stay within ${target.replace('<= ', '')}.`
    case 'Margin incidents (30d)':
      return 'Needs zero margin incidents.'
    case 'Operational incidents (30d)':
      return 'Needs zero operational incidents.'
    default:
      return `Still below target: ${target}.`
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
