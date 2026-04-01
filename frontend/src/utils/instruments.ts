const FUTURES_MARKET_NAMES: Record<string, string> = {
  MES: 'S&P 500',
  MNQ: 'Nasdaq',
  MGC: 'Gold',
  MCL: 'Crude Oil',
  ZN: '10-Year Treasury',
}

const FUTURES_POINT_VALUES: Record<string, number> = {
  MES: 5,
  MNQ: 2,
  MGC: 10,
  MCL: 100,
  ZN: 1000,
}

export function instrumentRoot(instrument: string): string {
  const normalized = instrument.trim().toUpperCase()
  if (!normalized) return normalized
  if (normalized.includes('_')) return normalized
  return normalized.split('-', 1)[0]
}

export function instrumentLabel(instrument: string): string {
  const root = instrumentRoot(instrument)
  if (!root) return instrument

  if (root.includes('_')) {
    return root.replace('_', '/')
  }

  return FUTURES_MARKET_NAMES[root] ?? instrument
}

export function instrumentPointValue(instrument: string): number | null {
  const root = instrumentRoot(instrument)
  return FUTURES_POINT_VALUES[root] ?? null
}
