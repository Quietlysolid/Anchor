// ── Market ────────────────────────────────────────────────────
export interface Candle {
  time: number       // Unix seconds
  open: number
  high: number
  low: number
  close: number
  volume?: number
}

export interface LivePrice {
  instrument: string
  bid: number
  ask: number
  spread: number
  time: string
}

// ── Signal ────────────────────────────────────────────────────
export type Direction = 'LONG' | 'SHORT'
export type Regime    = 'TRENDING' | 'RANGING' | 'VOLATILE'
export type Session   = 'LONDON' | 'NEWYORK' | 'ASIAN' | 'OFF' | 'NY_LCR'

export interface Signal {
  id: string
  created_at: string
  instrument: string
  timeframe: string
  direction: Direction
  confluence_score: number
  // London trend engine fields
  rsi_score: number | null
  bb_kc_score: number | null
  adx_score: number | null
  sr_score: number | null
  mtf_score: number | null
  csi_score: number | null
  cot_score: number | null
  rate_divergence_score: number | null
  ml_confidence: number | null
  regime_state: Regime | null
  session: Session | null
  suppressed: boolean
  suppression_reason: string | null
  // LCR-specific (present when session === 'NY_LCR', reuses score fields)
  // adx_score = range_pos_score, bb_kc_score = rejection_score, sr_score = range_qual_score
  london_high: number | null
  london_low: number | null
  london_mid: number | null
  position_in_range: number | null
}

// ── Position / Order ─────────────────────────────────────────
export type OrderState = 'PENDING'|'SUBMITTED'|'ACKNOWLEDGED'|'PARTIAL'|'FILLED'|'CANCELLED'|'REJECTED'|'EXPIRED'

export interface Position {
  id: string
  instrument: string
  direction: Direction
  units: number
  avg_entry_price: number
  current_price: number
  unrealized_pl: number
  stop_loss: number | null
  take_profit: number | null
  opened_at: string
  oanda_trade_id: string
}

export interface Order {
  id: string
  created_at: string
  instrument: string
  direction: Direction
  order_type: string
  units: number
  state: OrderState
  oanda_order_id: string | null
  stop_loss: number | null
  take_profit: number | null
}

export interface Trade {
  id: string
  instrument: string
  direction: Direction
  units: number
  entry_price: number
  exit_price: number
  opened_at: string
  closed_at: string
  net_pl: number
  close_reason: string
  regime_at_entry: Regime | null
  session_at_entry: Session | null
}

// ── Performance ──────────────────────────────────────────────
export interface PerformanceSummary {
  total_trades: number
  win_rate: number
  profit_factor: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  max_drawdown: number
  max_drawdown_pct: number  // alias — frontend uses this
  avg_win_pips: number
  avg_loss_pips: number
  avg_win: number           // alias
  avg_loss: number          // alias
  net_pnl: number
  gross_pnl: number
  total_commission: number
}

export interface EquityPoint {
  time: string
  account_balance: number
  account_equity: number
  drawdown_pct: number
}

export interface MonteCarloResult {
  median_return: number
  p5_return: number
  p95_return: number
  median_max_drawdown: number
  p95_max_drawdown: number
  risk_of_ruin: number
  expected_sharpe: number
  n_simulations: number
}

// ── System ────────────────────────────────────────────────────
export interface SystemHealth {
  status: 'healthy' | 'degraded' | 'critical'
  heartbeat_age_seconds: number
  last_reconciliation: string | null
  stream_connected: boolean
  open_positions: number
  account_balance: number
  account_equity: number
}

// ── Calendar ─────────────────────────────────────────────────
export interface EconomicEvent {
  event_time: string
  currency: string
  impact: 'HIGH' | 'MEDIUM'
  event_name: string
  forecast: string | null
  previous: string | null
}

// ── WebSocket ────────────────────────────────────────────────
export type WsChannel = 'ticks' | 'signals' | 'positions' | 'orders' | 'regime' | 'heartbeat'

export interface WsMessage {
  channel: WsChannel
  data: unknown
}