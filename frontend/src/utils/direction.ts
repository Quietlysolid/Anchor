import type { Direction } from '../types'

export function directionLabel(direction: Direction, casing: 'upper' | 'lower' = 'upper'): string {
  const label = direction === 'LONG' ? 'buy' : 'sell'
  return casing === 'upper' ? label.toUpperCase() : label
}
