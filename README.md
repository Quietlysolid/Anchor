# Anchor — Autonomous Forex Trading System

Fully automated forex trading system. OANDA practice account → live $1000 account when validated.
Private system, personal funds only.

---

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 async |
| Database | PostgreSQL + TimescaleDB |
| Task queue | Redis + Celery |
| Frontend | React 18, TypeScript, Vite 5, TanStack Query 5, Zustand 4 |
| Charts | lightweight-charts, Recharts |
| Broker | OANDA (oandapyV20), practice first |
| ML | XGBoost + LightGBM + 3-state Gaussian HMM |
| Infra | Docker Compose (10 services), Hetzner VPS CX22 (~€4/mo) |

---

## Quick Start (local dev)

```bash
cp .env.example .env          # fill in OANDA keys + DB creds
docker compose up -d          # start all services
make upgrade                  # run DB migrations
make import-history           # pull OANDA H1/H4/D candles into DB
```

Dashboard: `http://localhost/`
API: `http://localhost/api/v1/`

---

## Strategies

Two strategies run in parallel, regime-gated by HMM:

| Strategy | Regime | Engine | File |
|----------|--------|--------|------|
| **MTF Trend-Following** | TRENDING | `ConfluenceEngine` | `signals/engine.py` |
| **BB Mean Reversion** | RANGING | `MeanReversionEngine` | `signals/mean_reversion_engine.py` |
| *(nothing)* | VOLATILE | — | Both blocked |

**Trend engine** (H1 + M15): direction from 4H+Daily MTF alignment, RSI divergence as booster, 7-component confluence (RSI, BB/KC, ADX, S/R, MTF, sentiment, COT), threshold 0.65. M15 signals use H1 as confirmation timeframe, GTD 1h.

**Mean-reversion engine** (H1): fades price back to BB midband when price touches outer BB (2σ) with RSI extreme (65/35) + pin bar rejection + ADX < 25. Threshold 0.60. SL beyond outer band + 0.5×ATR, TP at midband. GTD 2 hours.

**Partial TP**: at 1×ATR profit, close 50% of any open position and move SL to breakeven on the remaining 50%. Runs every 5 minutes.

---

## Trading Rules ("Casino Rules")

These are non-negotiable. Every rule has a data-driven reason.

| # | Rule | Value |
|---|------|-------|
| 1 | Confluence threshold | ≥ 0.65 |
| 2 | Risk per trade | 1% of account (fixed fractional) |
| 3 | Session | **London 07:15–12:00 UTC** (first 15 min skipped — fake moves); **Asian 00:00–03:00 UTC for JPY pairs only** |
| 4 | Direction | **MTF trend-following** (RSI divergence is a booster, never a direction driver) |
| 5 | Stop loss | 1.5× ATR |
| 6 | Take profit | 2.0× ATR → 1.33:1 R:R |
| 7 | Drawdown: halve size | 8% drawdown |
| 8 | Drawdown: halt trading | 15% drawdown |
| 9 | Daily loss limit | 3% → halt rest of day |
| 10 | Correlation block | Block new positions if existing correlation > 0.70 |
| 11 | ML fallback | ML unavailable → rule-based only (no penalty) |
| 12 | Partial TP | Close 50% at 1×ATR profit; move SL to breakeven on remainder |
| 13 | COT filter | Institutional positioning (CFTC weekly) as confluence component (5% weight) |

**Why MTF, not RSI divergence as direction?**
RSI-divergence-led mean-reversion trades showed ~11% WR in backtests.
MTF trend-following showed ~50% WR. RSI divergence is now a confluence
booster only — it adds score when it agrees with the trend.

---

## Instruments

**Tier-1** (original 5): EUR_USD, GBP_USD, USD_JPY, AUD_USD, USD_CAD

**Tier-2** (added): NZD_USD, USD_CHF, EUR_GBP, GBP_JPY

Total: 9 instruments. Correlation block (>0.70) prevents simultaneous correlated trades.

---

## OOS Backtest Results

London-only session, 2× ATR TP, MTF direction. Period: 2024–2026.

| Pair | Trades | WR% | PF | Net% |
|------|--------|-----|----|------|
| EUR_USD | 16 | 50.0% | 1.04 | +0.40% |
| GBP_USD | 23 | 47.8% | 1.01 | +0.08% |
| AUD_USD | 22 | 59.1% | 1.53 | +5.42% |
| USD_CAD | 35 | 48.6% | 0.98 | -0.44% |
| USD_JPY | 4 | 100% | ∞ | +0.07% (tiny sample) |

**USD_CAD** is the weakest pair (PF < 1.0) — monitor closely, consider disabling.
Need 50+ live trades / ~3 months on practice before going live.

---

## ML Models

XGBoost + LightGBM direction classifiers. Not yet trained — need labeled live
trade data first. Threshold for deployment: ≥ 58% OOS accuracy.

HMM regime detector (3 states: TRENDING / RANGING / VOLATILE):
- VOLATILE state blocks all new entries
- Trained on daily candles, stable state mapping via feature means

---

## Key Files

```
backend/anchor/
  config.py                          # all settings via .env, instruments list (9 pairs)
  signals/engine.py                  # confluence engine — trend-following (H1 + M15)
  signals/mean_reversion_engine.py   # BB-fade engine — RANGING regime only
  signals/session_filter.py          # London 07:15-12:00 + Asian 00:00-03:00 for JPY
  signals/cot_signal.py              # COT institutional positioning scorer (5% weight)
  risk/position_sizer.py             # 1% fixed fractional, correct pip values per pair
  execution/partial_tp_manager.py    # partial TP at 1×ATR, SL → breakeven on remainder
  regime/hmm_detector.py             # 3-state HMM save/load
  backtesting/engine.py              # trend backtest runner
  backtesting/mr_backtest.py         # mean-reversion backtest runner
  backtesting/ablation_runner.py     # sweeps all components on/off → CSV
  backtesting/walk_forward_backtest.py  # train/val/OOS walk-forward harness
  data/oanda_history.py              # OANDA H1/H4/D/M15 candle importer
  data/polygon_history.py            # Polygon.io 8-year CSV downloader
  ml/retraining.py                   # monthly Celery retraining task
  execution/broker_client.py         # OANDA order execution
  scheduler/jobs.py                  # all scheduled tasks (trend + MR + M15 + partial TP)
  scheduler/celery_app.py            # Celery config
```

---

## Common Commands

```bash
make up                        # start stack
make down                      # stop stack
make logs                      # tail engine + worker + watchdog logs
make shell-engine              # bash into engine container
make upgrade                   # run pending DB migrations
make import-history            # import OANDA candles (H1/H4/D)
make backtest                  # run backtest
make retrain                   # trigger ML retrain via Celery
make lint                      # ruff + mypy
make test                      # all tests

# Backtesting (requires data CSVs in data/)
make download-history              # download 8yr history via Dukascopy
make ablation PAIR=EUR_USD         # trend ablation — one pair
make ablation-all                  # trend ablation — all pairs
make walk-forward PAIR=EUR_USD     # walk-forward — one pair
make walk-forward-all              # walk-forward — all pairs
make mr-backtest PAIR=EUR_USD      # MR backtest — one pair
make mr-backtest-all               # MR backtest — all 9 pairs
```

---

## Deployment

See [DEPLOY.md](DEPLOY.md) for full Hetzner VPS setup.

VPS: `89.167.82.233` (`/opt/anchor`)
Deploy: `VPS_HOST=89.167.82.233 bash infrastructure/scripts/deploy.sh`

---

## Monitoring

- Telegram alerts: orders executed, circuit breakers triggered, errors
- Grafana: `http://VPS_IP:3001/` (use SSH tunnel)
- Dashboard: `http://VPS_IP/`

---

## Validation Checklist (before going live)

- [ ] 50+ trades on practice account
- [ ] 3 months of live practice data
- [ ] Net PF > 1.0 across all active pairs
- [ ] Max drawdown stays under 10% in practice
- [ ] ML models trained with ≥ 58% OOS accuracy (optional, can skip)
- [ ] USD_CAD still PF < 1.0 → disable it
