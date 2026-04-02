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
  order_book_score: number | null
  cme_flow_score: number | null
  fx_options_score: number | null
  econ_surprise_score: number | null
  cross_asset_score: number | null
  news_multiplier: number | null
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
  current_price: number | null
  unrealized_pl: number | null
  stop_loss: number | null
  take_profit: number | null
  opened_at: string
  broker_trade_id: string
  record_origin?: 'current_broker_state' | 'anchor_audit_history'
}

export interface Order {
  id: string
  created_at: string
  instrument: string
  direction: Direction
  order_type: string
  units: number
  state: OrderState
  broker_order_id: string | null
  stop_loss: number | null
  take_profit: number | null
  record_origin?: 'current_broker_state' | 'anchor_audit_history'
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
  gross_pl?: number
  commission?: number
  expected_entry_price?: number | null
  fill_price?: number | null
  entry_slippage_pips?: number | null
  spread_at_fill?: number | null
  fill_at?: string | null
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
  status: 'ok' | 'degraded'
  db: 'ok' | 'error'
  timestamp: string
  deployed_sha: string | null
  deployed_at: string | null
  account_mode: 'paper' | 'live'
  account_environment: 'practice' | 'live' | string
  last_reconciliation: string | null
  stream_connected: boolean
  open_positions: number
  account_balance: number
  account_equity: number
  broker_day_pl: number | null
  today_pl: number | null
  account_id: string | null
  market_data_mode: string
}

export interface OperatorWindow {
  engine: string
  label: string
  starts_at: string
  ends_at: string
}

export interface OperatorState {
  as_of: string
  operator_state: 'DEGRADED' | 'MANAGING_POSITIONS' | 'WAITING_ON_ORDERS' | 'SCANNING' | 'OFF_WINDOW'
  session_status: 'OPEN' | 'CLOSED' | 'NO_WINDOW'
  working_orders_count: number
  open_positions_count: number
  active_window: OperatorWindow | null
  next_window: OperatorWindow | null
  active_engines: string[]
  blocker_code: string | null
  blocker_reason: string | null
  blocker_instrument: string | null
  blocker_at: string | null
}

export interface HomepageSnapshotActivity {
  id: string
  occurred_at: string
  kind: string
  tone: 'good' | 'warn' | 'bad' | 'info'
  badge: string
  instrument: string | null
  reason_code: string | null
  title: string
  detail: string
}

export interface HomepageSnapshotExposurePosition {
  id: string
  instrument: string
  direction: Direction
  units: number
  avg_entry_price: number
  current_price: number | null
  unrealized_pl: number | null
  stop_loss: number | null
  take_profit: number | null
  opened_at: string
}

export interface HomepageSnapshotExposureOrder {
  id: string
  instrument: string
  direction: Direction
  order_type: string
  units: number
  state: OrderState
  created_at: string
  stop_loss: number | null
  take_profit: number | null
}

export interface HomepageSnapshotExecutionFill {
  id: string
  instrument: string
  direction: Direction
  order_type: string
  units: number
  fill_price: number
  fill_at: string
  slippage_pips: number | null
  commission: number
}

export interface HomepageSnapshotWatchItem {
  instrument: string
  status: string
  reason_codes: string[]
  regime: string | null
  confidence: number | null
}

export interface HomepageSnapshotResult {
  id: string
  instrument: string
  direction: Direction
  opened_at: string
  closed_at: string
  net_pl: number
  close_reason: string | null
}

export interface HomepageSnapshotCalendarItem {
  event_time: string
  currency: string
  impact: 'HIGH' | 'MEDIUM'
  event_name: string
}

export interface HomepageSnapshot {
  as_of: string
  operator: OperatorState
  account: {
    provider?: string
    mode: 'paper' | 'live'
    environment: string
    account_id: string | null
    market_data_mode: string
    balance: number
    equity: number
    available_funds: number
    excess_liquidity: number
    initial_margin: number
    maintenance_margin: number
    margin_usage_pct: number | null
    broker_day_pl: number | null
    today_pl: number | null
    anchor_day_pl: number | null
    stream_connected: boolean
    last_reconciliation: string | null
  }
  history_notice?: string
  status: {
    stream_connected: boolean
    last_broker_sync: string | null
    open_positions_count: number
    open_orders_count: number
    last_rebalance: {
      occurred_at: string
      title: string
      detail: string
    } | null
    drawdown_guard: {
      active: boolean
      mode: 'monthly_halt' | 'halt' | 'reduced'
      title: string
      reason: string
      blocking: string
      clear_when: string
      current_drawdown_pct: number
      reduce_threshold_pct: number
      halt_threshold_pct: number
      monthly_halt_threshold_pct: number
      scale_factor: number
      occurred_at: string | null
    } | null
    active_guardrails: Array<{
      event_type: string
      occurred_at: string
      title: string
      detail: string
    }>
  }
  alerts: Array<{
    severity: 'info' | 'warning' | 'critical'
    title: string
    detail: string
  }>
  strategies: Array<{
    engine: string
    label: string
    status: 'running' | 'enabled' | 'off'
    paper_only: boolean
    readiness: 'live_ready' | 'paper_trial' | 'paper_validated' | 'no_go' | 'research_only' | 'unknown'
    readiness_label: string
  }>
  activity: HomepageSnapshotActivity[]
  decisions: Array<{
    id: string
    occurred_at: string
    tone: 'good' | 'warn' | 'bad' | 'info'
    title: string
    detail: string
    reason_code: string | null
  }>
  performance: {
    equity_curve: EquityPoint[]
    returns: {
      '1w_pct': number | null
      mtd_pct: number | null
      since_start_pct: number | null
    }
    drawdown: {
      current_pct: number | null
      max_pct: number | null
    }
    realized_pl: {
      mtd: number | null
      since_start: number | null
    }
    unrealized_pl: number | null
    track_record_days: number
  }
  readiness: {
    state: 'not_ready' | 'observe' | 'paper_validated' | 'candidate_for_live_mirror'
    label: string
    recommendation: string
    incident_cutoff: string
    track_record_days: number
    rebalance_count: number
    margin_incidents: number
    operational_incidents: number
    criteria: Array<{
      key: string
      label: string
      value: string
      target: string
      passed: boolean
    }>
    passed_checks: number
    total_checks: number
    failing_checks: string[]
  }
  exposure: {
    positions: HomepageSnapshotExposurePosition[]
    orders: HomepageSnapshotExposureOrder[]
  }
  execution: {
    open_orders: HomepageSnapshotExposureOrder[]
    recent_fills: HomepageSnapshotExecutionFill[]
  }
  watchlist: HomepageSnapshotWatchItem[]
  recent_results: HomepageSnapshotResult[]
  calendar: HomepageSnapshotCalendarItem[]
}

export interface SystemEvent {
  id: string
  event_at: string
  event_type: string
  severity: string
  component: string | null
  message: string
  metadata: Record<string, unknown>
}

export interface RebalanceEvent {
  event_type: string
  severity: string
  component: string | null
  message: string
  metadata: Record<string, unknown>
}

export interface EngineRolloutConfig {
  enabled: boolean
  paper_only: boolean
  risk_pct: number
}

export interface PilotRolloutConfig {
  instruments: string[]
  trend: EngineRolloutConfig
  mean_reversion: EngineRolloutConfig
  lcr: EngineRolloutConfig
  m15: EngineRolloutConfig
}

export interface RolloutConfig {
  account_mode?: 'paper' | 'live'
  account_environment?: 'practice' | 'live' | string
  instruments: string[]
  trend: EngineRolloutConfig
  mean_reversion: EngineRolloutConfig
  lcr: EngineRolloutConfig
  m15: EngineRolloutConfig
  expected_pilot: PilotRolloutConfig
  max_risk_per_trade_fallback: number
  min_confluence_score: number
  min_ml_confidence: number
}

// ── Edge confidence ───────────────────────────────────────────
export interface EdgeConfidenceSignal {
  flagged: boolean
  reason:  string
  detail:  Record<string, unknown>
}

export interface EdgeConfidence {
  confidence:          'HIGH' | 'REDUCED' | 'LOW' | null
  assessed_at:         string | null
  flag_count:          number
  message:             string | null
  previous_confidence: string | null
  signals: {
    session:     EdgeConfidenceSignal | null
    correlation: EdgeConfidenceSignal | null
    macro:       EdgeConfidenceSignal | null
  } | null
}

// ── Live performance check ────────────────────────────────────
export interface PerfCheckSummary {
  strategy:        string
  n_trades:        number
  rolling_wr_pct:  number
  bench_wr_pct:    number
  rolling_pf:      number
  bench_pf:        number
  days_since_win:  number
  status:          'ok' | 'DEGRADED_WR' | 'UNPROFITABLE' | 'WIN_DROUGHT' | 'insufficient_trades'
}

export interface PerfCheckResult {
  event_at:  string
  severity:  'INFO' | 'WARNING'
  message:   string
  summaries: PerfCheckSummary[]
  alerts:    string[]
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
export type WsChannel = 'ticks' | 'signals' | 'positions' | 'orders' | 'regime' | 'heartbeat' | 'account' | 'events'

export interface WsMessage {
  channel: WsChannel
  data: unknown
}
