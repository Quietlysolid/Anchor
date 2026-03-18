// ── Session phase logic ──────────────────────────────────────
export type SessionPhase = 'pre' | 'london' | 'post' | 'lcr' | 'off'

export interface PhaseInfo {
  phase:      SessionPhase
  label:      string
  remaining:  number | null  // minutes until this phase ends
  nextLabel:  string
  nextIn:     number         // minutes until next trading session
}

export function getPhaseInfo(utcMins: number): PhaseInfo {
  const PRE   = 6 * 60 + 30   // 390
  const LON_S = 7 * 60        // 420
  const LON_E = 12 * 60       // 720
  const POST  = 12 * 60 + 30  // 750
  const LCR_S = 17 * 60       // 1020
  const LCR_E = 19 * 60       // 1140

  if (utcMins >= PRE   && utcMins < LON_S) return { phase: 'pre',    label: 'Pre-Brief',      remaining: LON_S - utcMins, nextLabel: 'London', nextIn: LON_S - utcMins }
  if (utcMins >= LON_S && utcMins < LON_E) return { phase: 'london', label: 'London Session',  remaining: LON_E - utcMins, nextLabel: 'LCR',    nextIn: LCR_S - utcMins }
  if (utcMins >= LON_E && utcMins < POST)  return { phase: 'post',   label: 'Post-Brief',      remaining: POST  - utcMins, nextLabel: 'LCR',    nextIn: LCR_S - utcMins }
  if (utcMins >= LCR_S && utcMins < LCR_E) return { phase: 'lcr',   label: 'LCR Window',      remaining: LCR_E - utcMins, nextLabel: 'London', nextIn: 24 * 60 - utcMins + PRE }

  let nextLabel: string, nextIn: number
  if (utcMins < PRE)        { nextLabel = 'London'; nextIn = LON_S - utcMins }
  else if (utcMins < LCR_S) { nextLabel = 'LCR';    nextIn = LCR_S - utcMins }
  else                      { nextLabel = 'London'; nextIn = 24 * 60 - utcMins + PRE }

  return { phase: 'off', label: 'Markets Quiet', remaining: null, nextLabel, nextIn }
}

export function fmtMins(m: number): string {
  const h   = Math.floor(m / 60)
  const min = m % 60
  return h > 0 ? `${h}h ${min}m` : `${min}m`
}

// ── Signal suppression text ──────────────────────────────────
export const SUPPRESSION_TEXT: Record<string, string> = {
  WEEKEND_CLOSE:       'Markets closed — weekend',
  OUTSIDE_SESSION:     'Outside trading hours',
  LOW_CONFLUENCE:      'Setup not strong enough yet',
  DRAWDOWN_HALT:       'Trading paused — drawdown limit hit',
  MONTHLY_HALT:        'Trading paused — monthly loss limit hit',
  HIGH_SPREAD:         'Spread too wide — waiting for normal conditions',
  NO_LONDON_RANGE:     'London range not established yet',
  MONDAY_GAP_RISK:     'Waiting for Monday gap to settle',
  LONDON_OPEN_NOISE:   'First 15 min of London — waiting for clean price action',
  OFF_SESSION_ASIAN:   'Outside trading hours — Asian session',
  OFF_SESSION_OVERLAP: 'Outside trading hours — London/NY overlap',
  OFF_SESSION_NEWYORK: 'Outside trading hours — NY session',
  OFF_SESSION_OFF:     'Outside trading hours',
}

export function suppressionText(reason: string | null | undefined): string {
  if (!reason) return 'Signal suppressed'
  // Handle dynamic reasons like HMM_RANGING:0.990 or LCR_LOW_CONFLUENCE:0.42<0.55
  if (reason.startsWith('HMM_RANGING'))    return 'Market ranging — waiting for trend'
  if (reason.startsWith('HMM_VOLATILE'))   return 'Market volatile — sitting out'
  if (reason.startsWith('LCR_LOW_CONFLUENCE')) return 'Setup not strong enough yet'
  if (reason.startsWith('LCR_OFF_WINDOW')) return 'Outside LCR trading window'
  if (reason.startsWith('LOW_CONFLUENCE')) return 'Setup not strong enough yet'
  if (reason.startsWith('COT_CONFLICT'))   return 'Institutional positioning opposes this trade (CFTC data)'
  if (reason.startsWith('CME_CONFLICT'))   return 'Futures flow opposes this trade direction'
  if (reason.startsWith('TSMOM_CONFLICT')) return 'Medium-term trend opposes this trade'
  if (reason.startsWith('ML_DISAGREES'))   return 'AI model disagrees with signal direction'
  if (reason.startsWith('ML_LOW_CONFIDENCE')) return 'AI confidence too low'
  if (reason.startsWith('NEWS:'))          return `Suppressed — high-impact event: ${reason.slice(5)}`
  return SUPPRESSION_TEXT[reason] ?? reason.replace(/_/g, ' ').toLowerCase()
}
