import { useEffect, useState } from 'react'
import { usePositionStore, useSystemStore } from '../store'
import {
  SectionCard,
  cn,
  dollars,
  humanTime,
  marketInfo,
  operatorState,
  titleCaseWord,
  useDashboardSnapshot,
} from './dashboardShared'

const MANUAL_FILL_STORAGE_KEY = 'anchor-manual-trade-fills'

export default function Home() {
  const snapshot = useDashboardSnapshot()
  const { positions } = usePositionStore()
  const { wsConnected } = useSystemStore()
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [manualFills, setManualFills] = useState<Record<string, string>>({})

  useEffect(() => {
    document.title = 'Anchor'
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const raw = window.localStorage.getItem(MANUAL_FILL_STORAGE_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (parsed && typeof parsed === 'object') {
        setManualFills(parsed as Record<string, string>)
      }
    } catch {
      // Ignore local browser storage failures.
    }
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      window.localStorage.setItem(MANUAL_FILL_STORAGE_KEY, JSON.stringify(manualFills))
    } catch {
      // Ignore local browser storage failures.
    }
  }, [manualFills])

  const account = snapshot?.account
  const status = snapshot?.status
  const alerts = snapshot?.alerts ?? []
  const marketDataLabel = titleCaseWord(account?.market_data_mode ?? 'unknown')
  const state = operatorState(snapshot, marketDataLabel, nowMs, wsConnected)
  const primaryAlert = alerts.find((alert) => isActionableAlert(alert)) ?? null
  const tradePlan = snapshot?.trade_plan

  return (
    <div className="mx-auto max-w-2xl px-4 pb-24 pt-6 sm:px-6">
      <section className="mb-5 overflow-hidden rounded-[28px] border border-white/8 bg-[linear-gradient(160deg,rgba(201,168,76,0.14),rgba(13,30,53,0.92)_34%,rgba(13,30,53,0.96))] p-5 shadow-[0_20px_60px_rgba(0,0,0,0.3)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-anchor-fog/88">
              {account?.mode === 'paper' ? 'Paper futures' : 'Live futures'}
            </p>
            <h1 className="mt-2 font-display text-[2rem] leading-none text-anchor-brass sm:text-[2.2rem]">
              {state.operatorLabel}
            </h1>
            <p className="mt-3 max-w-[28rem] text-[13px] leading-relaxed text-anchor-slate">
              {state.summary}
            </p>
          </div>
          <div className="rounded-[18px] border border-white/10 bg-white/[0.04] px-4 py-3 text-right">
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Account value</p>
            <p className="mt-1 font-mono text-[1.35rem] font-bold text-anchor-navy">{dollars(account?.equity)}</p>
            <p className="mt-2 font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">{positions.length} open positions</p>
          </div>
        </div>
      </section>

      {primaryAlert ? (
        <section className="mb-5 overflow-hidden rounded-[22px] border border-white/8 bg-white/[0.03] shadow-[0_14px_34px_rgba(0,0,0,0.2)]">
          <div className="flex gap-3 px-5 py-4 sm:px-6">
            <div className={cn(
              'mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full',
              primaryAlert.severity === 'critical' ? 'bg-anchor-loss' :
              primaryAlert.severity === 'warning' ? 'bg-anchor-warn' :
              'bg-anchor-rule/60',
            )} />
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Attention</p>
              <p className="mt-1 text-[14px] font-semibold text-anchor-navy">{primaryAlert.title}</p>
              <p className="mt-1 text-[11px] text-anchor-fog/90">{primaryAlert.detail}</p>
            </div>
          </div>
        </section>
      ) : (
        <section className="mb-5 rounded-[22px] border border-emerald-500/15 bg-emerald-500/[0.05] px-5 py-4 shadow-[0_14px_34px_rgba(0,0,0,0.16)]">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">Attention</p>
          <p className="mt-1 text-sm font-semibold text-anchor-win">Nothing needs attention right now.</p>
        </section>
      )}

      {status?.drawdown_guard?.active && (
        <SectionCard title="Guardrail" kicker="Risk control">
          <div className="px-5 py-5 sm:px-6">
            <p className="text-[13px] font-semibold text-anchor-navy">{status.drawdown_guard.title}</p>
            <p className="mt-1 text-[11px] text-anchor-fog/90">{status.drawdown_guard.reason}</p>
            <p className="mt-2 font-mono text-[10px] tracking-[0.05em] text-anchor-fog/84">
              Triggered {humanTime(status.drawdown_guard.occurred_at, nowMs)}
            </p>
          </div>
        </SectionCard>
      )}

      <SectionCard title="Trade plan">
        <div className="px-5 py-4 sm:px-6">
          {tradePlan?.blocked_reason ? (
            <p className="text-[12px] text-anchor-fog/90">Plan is currently blocked: {tradePlan.blocked_reason}.</p>
          ) : (
            <p className="text-[12px] text-anchor-fog/90">What Anchor wants to do next. You can place these manually in Robinhood.</p>
          )}
          {tradePlan?.generated_at && (
            <p className="mt-1 font-mono text-[10px] tracking-[0.05em] text-anchor-fog/84">
              Generated {humanTime(tradePlan.generated_at, nowMs)}
            </p>
          )}
          {tradePlan?.next_rebalance_at && (
            <p className="mt-1 font-mono text-[10px] tracking-[0.05em] text-anchor-fog/84">
              Next rebalance {humanTime(tradePlan.next_rebalance_at, nowMs)}
            </p>
          )}
        </div>

        {(tradePlan?.actions?.length ?? 0) > 0 ? (
          <div className="divide-y divide-white/8 border-t border-white/8">
            {tradePlan?.actions.map((action, index) => {
              const mkt = marketInfo(action.market || action.instrument)
              const manualFillRaw = manualFills[action.instrument] ?? ''
              const manualFill = parseFillPrice(manualFillRaw)
              const adjustedStop = adjustedEmergencyStop(
                manualFill,
                action.reference_price,
                action.emergency_stop,
              )
              return (
                <div key={`${action.instrument}-${index}`} className="px-5 py-4 sm:px-6">
                  <p className="text-[14px] font-semibold text-anchor-navy">{tradePlanTitle(action.action, action.direction, action.contracts, mkt.name)}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/90">
                    {action.instrument}
                    {action.reason ? ` · ${tradePlanReason(action.reason)}` : ''}
                  </p>
                  <div className="mt-2 space-y-1.5 text-[11px] leading-relaxed text-anchor-fog/92">
                    <p>{action.entry_note}</p>
                    {action.reference_price != null && (
                      <p>
                        Reference price {numberPrice(action.reference_price)}
                        {action.action === 'open' ? ' on the latest daily close.' : '.'}
                      </p>
                    )}
                    {action.emergency_stop != null && (
                      <p>Emergency stop {numberPrice(action.emergency_stop)}.</p>
                    )}
                    <p>{humanHoldNote(action.hold_note, nowMs)}</p>
                    <p>{humanExitNote(action.exit_note, nowMs)}</p>
                  </div>
                  {action.action === 'open' && (
                    <div className="mt-3 rounded-[16px] border border-white/8 bg-white/[0.03] px-3 py-3">
                      <label className="block">
                        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                          Your Robinhood fill
                        </span>
                        <input
                          inputMode="decimal"
                          placeholder="Enter your fill price"
                          value={manualFillRaw}
                          onChange={(event) => {
                            const value = event.target.value
                            setManualFills((current) => ({ ...current, [action.instrument]: value }))
                          }}
                          className="mt-2 w-full rounded-[12px] border border-white/10 bg-anchor-night/50 px-3 py-2 text-[14px] text-anchor-navy outline-none transition placeholder:text-anchor-fog/55 focus:border-anchor-brass/45"
                        />
                      </label>
                      {manualFill != null ? (
                        <div className="mt-3 space-y-1.5 text-[11px] leading-relaxed text-anchor-fog/92">
                          <p>Your fill {numberPrice(manualFill)}.</p>
                          {adjustedStop != null && (
                            <p>Adjusted emergency stop {numberPrice(adjustedStop)}.</p>
                          )}
                          <p>This tracks your manual Robinhood entry separately from Anchor&apos;s paper reference.</p>
                        </div>
                      ) : (
                        <p className="mt-3 text-[11px] text-anchor-fog/84">
                          Enter your actual fill and Anchor will adjust the stop to your price.
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <div className="border-t border-white/8 px-5 py-6 sm:px-6">
            <p className="text-sm text-anchor-fog">No trade changes planned right now.</p>
          </div>
        )}
      </SectionCard>
    </div>
  )
}

function isActionableAlert(alert: { severity: string; title: string }) {
  const severity = alert.severity.toLowerCase()
  const title = alert.title.toLowerCase()
  if (severity === 'info') return false
  if (title.includes('paper readiness')) return false
  return true
}

function tradePlanTitle(action: string, direction: string, contracts: number, marketName: string) {
  if (action === 'close') {
    return `Close ${contracts} ${marketName} contract${contracts === 1 ? '' : 's'}`
  }
  const verb = direction === 'SHORT' ? 'Sell' : 'Buy'
  return `${verb} ${contracts} ${marketName} contract${contracts === 1 ? '' : 's'}`
}

function tradePlanReason(reason: string) {
  switch (reason) {
    case 'target_delta':
      return 'target rebalance'
    case 'roll_or_contract_mismatch':
      return 'contract roll'
    default:
      return reason.replace(/_/g, ' ')
  }
}

function numberPrice(value: number) {
  const abs = Math.abs(value)
  const digits = abs >= 1000 ? 2 : abs >= 100 ? 3 : 5
  return value.toFixed(digits)
}

function parseFillPrice(raw: string) {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const value = Number(trimmed)
  if (!Number.isFinite(value) || value <= 0) return null
  return value
}

function adjustedEmergencyStop(
  manualFill: number | null,
  referencePrice: number | null,
  emergencyStop: number | null,
) {
  if (manualFill == null || referencePrice == null || emergencyStop == null || referencePrice <= 0) {
    return null
  }
  return (manualFill * emergencyStop) / referencePrice
}

function humanHoldNote(note: string, nowMs: number) {
  return note.replace(
    /on (\d{4}-\d{2}-\d{2}T[^ ]+)/,
    (_, iso) => `on ${humanTime(iso, nowMs)}`,
  )
}

function humanExitNote(note: string, nowMs: number) {
  return note.replace(
    /on (\d{4}-\d{2}-\d{2}T[^ ]+)/,
    (_, iso) => `on ${humanTime(iso, nowMs)}`,
  )
}
