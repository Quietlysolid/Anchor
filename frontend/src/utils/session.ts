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
