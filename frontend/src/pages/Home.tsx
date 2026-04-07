import { useEffect, useState } from 'react'
import { usePositionStore, useSystemStore } from '../store'
import { useManualTradeJournal, useUpsertManualTradeJournal } from '../api/hooks'
import type { ManualTradeJournalEntry } from '../types'
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

type ManualTradeDraft = {
  taken: boolean
  closed: boolean
  fillPrice: string
  stopPrice: string
  exitPrice: string
  notes: string
}

export default function Home() {
  const snapshot = useDashboardSnapshot()
  const { positions } = usePositionStore()
  const { wsConnected } = useSystemStore()
  const [nowMs, setNowMs] = useState(() => Date.now())
  const [manualTrades, setManualTrades] = useState<Record<string, ManualTradeDraft>>({})
  const { data: journalEntries } = useManualTradeJournal()
  const upsertManualTrade = useUpsertManualTradeJournal()

  useEffect(() => {
    document.title = 'Anchor'
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])

  useEffect(() => {
    if (!journalEntries) return
    setManualTrades((current) => {
      const next = { ...current }
      for (const entry of journalEntries) {
        if (!next[entry.action_key]) {
          next[entry.action_key] = manualTradeDraftFromEntry(entry)
        }
      }
      return next
    })
  }, [journalEntries])

  const account = snapshot?.account
  const status = snapshot?.status
  const alerts = snapshot?.alerts ?? []
  const marketDataLabel = titleCaseWord(account?.market_data_mode ?? 'unknown')
  const state = operatorState(snapshot, marketDataLabel, nowMs, wsConnected)
  const primaryAlert = alerts.find((alert) => isActionableAlert(alert)) ?? null
  const tradePlan = snapshot?.trade_plan
  const journalMap = Object.fromEntries((journalEntries ?? []).map((entry) => [entry.action_key, entry]))

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
          <p className="mt-2 text-[11px] leading-relaxed text-anchor-fog/84">
            Keep Anchor as the planner. Use Robinhood to place the order, then mark what you actually did here.
          </p>
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
              const tradeKey = manualTradeKey(tradePlan?.generated_at ?? null, action.action, action.instrument)
              const draft = manualTrades[tradeKey] ?? emptyManualTradeDraft()
              const savedEntry = journalMap[tradeKey]
              const manualFill = parseFillPrice(draft.fillPrice)
              const manualStop = parseFillPrice(draft.stopPrice)
              const manualExit = parseFillPrice(draft.exitPrice)
              const adjustedStop = adjustedEmergencyStop(
                manualFill,
                action.reference_price,
                action.emergency_stop,
              )
              const suggestedStop = manualStop ?? adjustedStop ?? action.emergency_stop
              const robinhoodSide = robinhoodSideLabel(action.action, action.direction)
              return (
                <div key={`${action.instrument}-${index}`} className="px-5 py-4 sm:px-6">
                  <p className="text-[14px] font-semibold text-anchor-navy">{tradePlanTitle(action.action, action.direction, action.contracts, mkt.name)}</p>
                  <p className="mt-1 text-[11px] text-anchor-fog/90">
                    {action.instrument}
                    {action.reason ? ` · ${tradePlanReason(action.reason)}` : ''}
                  </p>
                  <div className="mt-3 grid grid-cols-2 gap-2 rounded-[16px] border border-white/8 bg-white/[0.03] px-3 py-3 text-[11px] text-anchor-fog/92 sm:grid-cols-4">
                    <SummaryStat label="Robinhood side" value={robinhoodSide} />
                    <SummaryStat label="Contracts" value={String(action.contracts)} />
                    <SummaryStat label="Anchor entry" value={action.reference_price != null ? numberPrice(action.reference_price) : 'Market'} />
                    <SummaryStat label="Suggested stop" value={suggestedStop != null ? numberPrice(suggestedStop) : 'None'} />
                  </div>
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
                  <div className="mt-3 rounded-[16px] border border-white/8 bg-white/[0.03] px-3 py-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                        Manual Robinhood
                      </p>
                      <div className="flex flex-wrap gap-2">
                        <TogglePill
                          active={draft.taken}
                          label={action.action === 'close' ? 'Closed in Robinhood' : 'Taken in Robinhood'}
                          onClick={() => updateManualTrade(setManualTrades, tradeKey, { taken: !draft.taken })}
                        />
                        <TogglePill
                          active={draft.closed}
                          label="Done"
                          onClick={() => updateManualTrade(setManualTrades, tradeKey, { closed: !draft.closed })}
                        />
                      </div>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                      <label className="block">
                        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                          Actual fill
                        </span>
                        <input
                          inputMode="decimal"
                          placeholder="Your fill price"
                          value={draft.fillPrice}
                          onChange={(event) => updateManualTrade(setManualTrades, tradeKey, { fillPrice: event.target.value })}
                          className="mt-2 w-full rounded-[12px] border border-white/10 bg-anchor-night/50 px-3 py-2 text-[14px] text-anchor-navy outline-none transition placeholder:text-anchor-fog/55 focus:border-anchor-brass/45"
                        />
                      </label>
                      <label className="block">
                        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                          Stop you placed
                        </span>
                        <input
                          inputMode="decimal"
                          placeholder={suggestedStop != null ? numberPrice(suggestedStop) : 'Optional'}
                          value={draft.stopPrice}
                          onChange={(event) => updateManualTrade(setManualTrades, tradeKey, { stopPrice: event.target.value })}
                          className="mt-2 w-full rounded-[12px] border border-white/10 bg-anchor-night/50 px-3 py-2 text-[14px] text-anchor-navy outline-none transition placeholder:text-anchor-fog/55 focus:border-anchor-brass/45"
                        />
                      </label>
                      <label className="block">
                        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                          Exit price
                        </span>
                        <input
                          inputMode="decimal"
                          placeholder={action.action === 'close' ? 'Actual close price' : 'When you exit'}
                          value={draft.exitPrice}
                          onChange={(event) => updateManualTrade(setManualTrades, tradeKey, { exitPrice: event.target.value })}
                          className="mt-2 w-full rounded-[12px] border border-white/10 bg-anchor-night/50 px-3 py-2 text-[14px] text-anchor-navy outline-none transition placeholder:text-anchor-fog/55 focus:border-anchor-brass/45"
                        />
                      </label>
                    </div>
                    <label className="mt-3 block">
                      <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-anchor-fog/84">
                        Notes
                      </span>
                      <textarea
                        rows={2}
                        placeholder="Why you took it, what you changed, or what happened."
                        value={draft.notes}
                        onChange={(event) => updateManualTrade(setManualTrades, tradeKey, { notes: event.target.value })}
                        className="mt-2 w-full resize-none rounded-[12px] border border-white/10 bg-anchor-night/50 px-3 py-2 text-[13px] text-anchor-navy outline-none transition placeholder:text-anchor-fog/55 focus:border-anchor-brass/45"
                      />
                    </label>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                      <p className="text-[11px] text-anchor-fog/84">
                        {savedEntry
                          ? `Saved ${humanTime(savedEntry.updated_at, nowMs)}.`
                          : 'Not saved to Anchor journal yet.'}
                      </p>
                      <button
                        type="button"
                        onClick={() =>
                          upsertManualTrade.mutate({
                            action_key: tradeKey,
                            action: action.action,
                            instrument: action.instrument,
                            market: action.market,
                            direction: action.direction,
                            contracts: action.contracts,
                            reason: action.reason,
                            anchor_generated_at: tradePlan?.generated_at ?? null,
                            anchor_reference_price: action.reference_price,
                            anchor_stop_price: action.emergency_stop,
                            anchor_entry_note: action.entry_note,
                            anchor_exit_note: action.exit_note,
                            taken: draft.taken,
                            closed: draft.closed,
                            fill_price: manualFill,
                            stop_price: manualStop,
                            exit_price: manualExit,
                            notes: draft.notes.trim() || null,
                          })
                        }
                        disabled={upsertManualTrade.isPending}
                        className={cn(
                          'rounded-full border px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] transition',
                          upsertManualTrade.isPending
                            ? 'border-white/10 bg-white/[0.03] text-anchor-fog/70'
                            : 'border-anchor-brass/35 bg-anchor-brass/10 text-anchor-navy',
                        )}
                      >
                        {upsertManualTrade.isPending ? 'Saving...' : 'Save journal'}
                      </button>
                    </div>
                    <div className="mt-3 space-y-1.5 text-[11px] leading-relaxed text-anchor-fog/92">
                      {manualFill != null ? (
                        <p>Your planned stop from this fill is {numberPrice(adjustedStop ?? suggestedStop ?? manualFill)}.</p>
                      ) : (
                        <p>Enter your actual Robinhood fill to adapt the stop from Anchor&apos;s reference.</p>
                      )}
                      {manualExit != null ? (
                        <p>Your manual exit is logged at {numberPrice(manualExit)}.</p>
                      ) : (
                        <p>Use exit price when you close so Anchor becomes your simple journal.</p>
                      )}
                    </div>
                  </div>
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

function emptyManualTradeDraft(): ManualTradeDraft {
  return {
    taken: false,
    closed: false,
    fillPrice: '',
    stopPrice: '',
    exitPrice: '',
    notes: '',
  }
}

function manualTradeDraftFromEntry(entry: ManualTradeJournalEntry): ManualTradeDraft {
  return {
    taken: entry.taken,
    closed: entry.closed,
    fillPrice: entry.fill_price != null ? String(entry.fill_price) : '',
    stopPrice: entry.stop_price != null ? String(entry.stop_price) : '',
    exitPrice: entry.exit_price != null ? String(entry.exit_price) : '',
    notes: entry.notes ?? '',
  }
}

function manualTradeKey(generatedAt: string | null, action: string, instrument: string) {
  return `${generatedAt ?? 'unknown'}:${action}:${instrument}`
}

function updateManualTrade(
  setManualTrades: React.Dispatch<React.SetStateAction<Record<string, ManualTradeDraft>>>,
  key: string,
  patch: Partial<ManualTradeDraft>,
) {
  setManualTrades((current) => ({
    ...current,
    [key]: {
      ...(current[key] ?? emptyManualTradeDraft()),
      ...patch,
    },
  }))
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

function robinhoodSideLabel(action: string, direction: string) {
  if (action === 'close') return 'Close position'
  return direction === 'SHORT' ? 'Sell to open' : 'Buy to open'
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

function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-anchor-fog/84">{label}</p>
      <p className="mt-1 text-[12px] font-semibold text-anchor-navy">{value}</p>
    </div>
  )
}

function TogglePill({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-[0.14em] transition',
        active
          ? 'border-emerald-500/35 bg-emerald-500/10 text-anchor-win'
          : 'border-white/10 bg-white/[0.03] text-anchor-fog/84',
      )}
    >
      {label}
    </button>
  )
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
