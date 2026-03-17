import type { EquityPoint, Trade, Position, Signal, EconomicEvent } from '../types'

// ── Equity curve — 6 months of realistic growth ──────────────
const START = 10000
const NOW   = Date.now()
const DAY   = 86400_000

function genEquity(): EquityPoint[] {
  const pts: EquityPoint[] = []
  let bal = START
  for (let i = 180; i >= 0; i--) {
    const t   = new Date(NOW - i * DAY)
    const dow = t.getUTCDay()
    if (dow === 0 || dow === 6) continue
    const delta = (Math.random() - 0.42) * 120
    bal = Math.max(9000, bal + delta)
    const dd = bal < START ? (bal - START) / START : 0
    pts.push({
      time:            t.toISOString(),
      account_balance: parseFloat(bal.toFixed(2)),
      account_equity:  parseFloat((bal + (Math.random() - 0.5) * 60).toFixed(2)),
      drawdown_pct:    parseFloat(dd.toFixed(4)),
    })
  }
  return pts
}

export const MOCK_EQUITY: EquityPoint[] = genEquity()

// ── Open positions ────────────────────────────────────────────
export const MOCK_POSITIONS: Position[] = [
  {
    id: 'p1',
    instrument: 'EUR_USD',
    direction: 'LONG',
    units: 10000,
    avg_entry_price: 1.08342,
    current_price:   1.08519,
    unrealized_pl:   17.70,
    stop_loss:       1.08100,
    take_profit:     1.08800,
    opened_at:       new Date(NOW - 2.4 * 3600_000).toISOString(),
    oanda_trade_id:  'T-00321',
  },
  {
    id: 'p2',
    instrument: 'GBP_USD',
    direction: 'LONG',
    units: 8000,
    avg_entry_price: 1.26180,
    current_price:   1.26044,
    unrealized_pl:   -10.88,
    stop_loss:       1.25900,
    take_profit:     1.26700,
    opened_at:       new Date(NOW - 1.1 * 3600_000).toISOString(),
    oanda_trade_id:  'T-00322',
  },
  {
    id: 'p3',
    instrument: 'NZD_USD',
    direction: 'SHORT',
    units: 12000,
    avg_entry_price: 0.60821,
    current_price:   0.60714,
    unrealized_pl:   12.84,
    stop_loss:       0.61050,
    take_profit:     0.60400,
    opened_at:       new Date(NOW - 0.6 * 3600_000).toISOString(),
    oanda_trade_id:  'T-00323',
  },
]

// ── Signals ───────────────────────────────────────────────────
export const MOCK_SIGNALS: Signal[] = [
  { id: 's1', created_at: new Date(NOW - 5*60_000).toISOString(),  instrument: 'EUR_USD', timeframe: 'H1', direction: 'LONG',  confluence_score: 0.84, rsi_score: 0.78, bb_kc_score: 0.91, adx_score: 0.72, sr_score: 0.85, mtf_score: 0.80, csi_score: null, cot_score: 0.62, rate_divergence_score: 0.71, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.81, regime_state: 'TRENDING', session: 'LONDON', suppressed: false, suppression_reason: null, london_high: null, london_low: null, london_mid: null, position_in_range: null },
  { id: 's2', created_at: new Date(NOW - 18*60_000).toISOString(), instrument: 'GBP_USD', timeframe: 'H1', direction: 'LONG',  confluence_score: 0.77, rsi_score: 0.65, bb_kc_score: 0.82, adx_score: 0.68, sr_score: 0.74, mtf_score: 0.71, csi_score: null, cot_score: 0.55, rate_divergence_score: 0.60, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.74, regime_state: 'TRENDING', session: 'LONDON', suppressed: false, suppression_reason: null, london_high: null, london_low: null, london_mid: null, position_in_range: null },
  { id: 's3', created_at: new Date(NOW - 34*60_000).toISOString(), instrument: 'NZD_USD', timeframe: 'H1', direction: 'SHORT', confluence_score: 0.81, rsi_score: 0.80, bb_kc_score: 0.76, adx_score: 0.79, sr_score: 0.83, mtf_score: 0.77, csi_score: null, cot_score: 0.48, rate_divergence_score: 0.58, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.79, regime_state: 'TRENDING', session: 'LONDON', suppressed: false, suppression_reason: null, london_high: null, london_low: null, london_mid: null, position_in_range: null },
  { id: 's4', created_at: new Date(NOW - 62*60_000).toISOString(), instrument: 'USD_CAD', timeframe: 'H1', direction: 'SHORT', confluence_score: 0.68, rsi_score: 0.61, bb_kc_score: 0.70, adx_score: 0.60, sr_score: 0.65, mtf_score: 0.62, csi_score: null, cot_score: 0.40, rate_divergence_score: 0.45, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.66, regime_state: 'RANGING', session: 'LONDON', suppressed: true,  suppression_reason: 'below_threshold', london_high: null, london_low: null, london_mid: null, position_in_range: null },
  { id: 's5', created_at: new Date(NOW - 91*60_000).toISOString(), instrument: 'EUR_JPY', timeframe: 'H1', direction: 'LONG',  confluence_score: 0.79, rsi_score: 0.74, bb_kc_score: 0.81, adx_score: 0.75, sr_score: 0.78, mtf_score: 0.73, csi_score: null, cot_score: 0.66, rate_divergence_score: 0.69, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.77, regime_state: 'TRENDING', session: 'LONDON', suppressed: false, suppression_reason: null, london_high: null, london_low: null, london_mid: null, position_in_range: null },
  { id: 's6', created_at: new Date(NOW - 110*60_000).toISOString(), instrument: 'AUD_USD', timeframe: 'H4', direction: 'LONG',  confluence_score: 0.74, rsi_score: 0.68, bb_kc_score: 0.79, adx_score: 0.65, sr_score: 0.72, mtf_score: 0.70, csi_score: null, cot_score: 0.59, rate_divergence_score: 0.62, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.72, regime_state: 'TRENDING', session: 'LONDON', suppressed: false, suppression_reason: null, london_high: null, london_low: null, london_mid: null, position_in_range: null },
  { id: 's7', created_at: new Date(NOW - 3600_000).toISOString(),   instrument: 'GBP_USD', timeframe: 'H4', direction: 'SHORT', confluence_score: 0.56, rsi_score: 0.50, bb_kc_score: 0.58, adx_score: 0.49, sr_score: 0.55, mtf_score: 0.52, csi_score: null, cot_score: 0.35, rate_divergence_score: 0.38, order_book_score: null, cme_flow_score: null, fx_options_score: null, econ_surprise_score: null, cross_asset_score: null, news_multiplier: 1.0, ml_confidence: 0.54, regime_state: 'VOLATILE', session: 'LONDON', suppressed: true,  suppression_reason: 'below_threshold', london_high: null, london_low: null, london_mid: null, position_in_range: null },
]

// ── AI brief ─────────────────────────────────────────────────
export const MOCK_BRIEF_CONTENT = `**Pre-Session Analysis — London Open**

EUR/USD is approaching the 1.0854 resistance zone with RSI divergence forming on H1. The BB/KC squeeze is compressed — expect a breakout within 1-2 candles. ADX at 28 confirms a developing trend; wait for 1H close above 1.0850 before entry.

**Key Levels Today**
- EUR/USD: S1 1.0812, R1 1.0858, R2 1.0882
- GBP/USD: S1 1.2588, R1 1.2641
- NZD/USD: S1 0.6063, R1 0.6095

**Session Bias:** Mildly bullish USD pairs on DXY strength. EUR/JPY showing strongest confluence at 0.84 — prioritise.

**Risk Note:** ECB Lagarde speaks at 10:30 UTC. Size conservatively until post-event.`

// ── Economic calendar ─────────────────────────────────────────
export const MOCK_CALENDAR: EconomicEvent[] = [
  { event_time: new Date(NOW + 38*60_000).toISOString(),     currency: 'EUR', impact: 'HIGH',   event_name: 'ECB President Lagarde Speech', forecast: null,    previous: null    },
  { event_time: new Date(NOW + 2.5*3600_000).toISOString(),  currency: 'USD', impact: 'HIGH',   event_name: 'Core CPI m/m',                  forecast: '0.3%',  previous: '0.4%'  },
  { event_time: new Date(NOW + 5.2*3600_000).toISOString(),  currency: 'GBP', impact: 'MEDIUM', event_name: 'Manufacturing PMI',              forecast: '47.8',  previous: '47.5'  },
]

// ── Trades (closed) ───────────────────────────────────────────
const regimes: Array<'TRENDING' | 'RANGING' | 'VOLATILE'> = ['TRENDING', 'RANGING', 'VOLATILE']
const pairs = ['EUR_USD', 'GBP_USD', 'NZD_USD', 'USD_CAD', 'EUR_JPY', 'AUD_USD']
const sessions: Array<'LONDON' | 'NY_LCR'> = ['LONDON', 'NY_LCR']

export const MOCK_TRADES: Trade[] = Array.from({ length: 40 }, (_, i) => {
  const win   = Math.random() > 0.44
  const dir   = Math.random() > 0.5 ? 'LONG' as const : 'SHORT' as const
  const pair  = pairs[i % pairs.length]
  const entry = pair.includes('JPY') ? 154.2 + Math.random() * 2 : 1.0800 + Math.random() * 0.08
  const move  = (win ? 1 : -1) * (Math.random() * 0.0080 + 0.0020)
  const exit  = dir === 'LONG' ? entry + move : entry - move
  const hold  = Math.floor(Math.random() * 180 + 30)
  const openAt = new Date(NOW - (i * 10 + Math.random() * 8) * 3600_000)
  return {
    id:               `t${i + 1}`,
    instrument:       pair,
    direction:        dir,
    units:            Math.floor(Math.random() * 8000 + 5000),
    entry_price:      parseFloat(entry.toFixed(pair.includes('JPY') ? 3 : 5)),
    exit_price:       parseFloat(exit.toFixed(pair.includes('JPY') ? 3 : 5)),
    opened_at:        openAt.toISOString(),
    closed_at:        new Date(openAt.getTime() + hold * 60_000).toISOString(),
    net_pl:           parseFloat(((win ? 1 : -1) * (Math.random() * 45 + 5)).toFixed(2)),
    close_reason:     win ? 'take_profit' : Math.random() > 0.5 ? 'stop_loss' : 'manual',
    regime_at_entry:  regimes[Math.floor(Math.random() * 3)],
    session_at_entry: sessions[Math.floor(Math.random() * 2)],
  }
})
