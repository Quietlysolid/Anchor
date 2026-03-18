# Anchor — Autonomous Forex Trading System

Fully automated forex trading system running on OANDA practice account.
Target: validate edge over 200+ live trades, then deploy to live $1,000 account.
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
| Broker | OANDA (oandapyV20), practice → live |
| ML | XGBoost + 3-state Gaussian HMM regime detector |
| Infra | Docker Compose (11 services), Hetzner VPS CX22 (~€4/mo) |

---

## Quick Start

```bash
cp .env.example .env          # fill in OANDA keys + DB creds
docker compose up -d          # start all services
make upgrade                  # run DB migrations
make import-history           # pull OANDA H1/H4/D candles into DB
```

Dashboard: `http://localhost/`
API docs: `http://localhost/api/v1/docs`

---

## Strategies

Two strategies run in parallel on EUR_USD, GBP_USD, USD_JPY:

### Strategy 1 — London Trend (07:15–12:00 UTC)

Direction set by H4+Daily MTF alignment. RSI divergence used as confluence booster only (never direction driver — RSI-led trades showed ~11% WR vs ~56% for MTF trend-following).

8-component weighted confluence score, threshold **0.72**:

| Component | Weight | Basis |
|-----------|--------|-------|
| BB/KC squeeze | 0.22 | Volatility compression precedes expansion (Bollinger 1992) |
| RSI divergence | 0.20 | Entry timing booster — only when aligned with MTF direction |
| S/R strength | 0.20 | Stop clustering at structural levels (Osler 2003) |
| ADX filter | 0.15 | Trend strength gate (Wilder 1978) |
| MTF agreement | 0.10 | Primary: 4H+Daily alignment (Lo & MacKinlay 1988) |
| COT signal | 0.05 | CFTC institutional positioning (Leuthold et al. 1994) |
| OANDA sentiment | 0.04 | Contrarian retail signal |
| Rate divergence | 0.04 | Carry-trade macro filter (Lustig & Verdelhan 2007) |

**TSMOM gate** (Moskowitz, Ooi & Pedersen 2012 JFE): 12-week sign-of-return direction filter. Blocks signals where MTF direction conflicts with 84-day momentum. Fail-open when daily data unavailable.

**NR4 flag** (Crabel 1990): Asian-session compression metadata logged per signal for ML feature use.

**HMM regime gate**: TRENDING → allow; RANGING → suppress; VOLATILE → suppress.

OOS result: **56% WR, PF 1.29** (London session, spread-adjusted).

---

### Strategy 2 — London Close Reversal / LCR (17:00–19:59 UTC)

At London close, institutional traders liquidate intraday positions creating counter-trend pressure. Price reverts toward the session's statistical midpoint (Harris & Pisedtasalasai 2006, Breedon & Ranaldo 2013).

**Mechanics:**
- London range = H/L of 07:00–16:00 UTC bars
- Direction: price in top 25% of range → SHORT; bottom 25% → LONG
- TP = London midpoint (natural reversion target)
- SL = London extreme + 0.5×ATR

4-component confluence, threshold **0.55** (0.65 in TRENDING regime):

| Component | Weight | Basis |
|-----------|--------|-------|
| Range position | 0.40 | How extreme price is within London range |
| RSI extreme | 0.30 | Momentum exhaustion at extreme (RSI 60/40 thresholds) |
| Rejection candle | 0.20 | Pin bar / wick confirms momentum shift |
| Range quality | 0.10 | London range ≥ 0.4×ATR (filters dead days) |

**HMM regime gate**: VOLATILE → block; TRENDING → raise threshold 0.55→0.65.

**8-year OOS results** (spread-adjusted, 3-pip minimum SL filter):

| Pair | WR% | PF | Max DD | Trades/mo | Risk |
|------|-----|----|--------|-----------|------|
| EUR_USD | 46.9% | 1.669 | -23.6% | 13.3 | 1.0% |
| GBP_USD | 43.8% | 1.434 | -20.9% | 13.5 | 1.0% |
| USD_JPY | 42.9% | 1.367 | -22.8% | 14.0 | 0.5% |

USD_JPY uses 0.5% risk due to higher drawdown profile.

---

## Instruments

**Active (3):** EUR_USD, GBP_USD, USD_JPY

**Removed:** AUD_USD (no LCR edge), NZD_USD (wide spread), USD_CHF (r=-0.85 with EUR_USD — redundant), EUR_GBP (15 pip/day ATR — too narrow), GBP_JPY (no LCR edge — re-evaluate after 3 months live data)

---

## Risk Rules

| # | Rule | Value |
|---|------|-------|
| 1 | Confluence threshold — London Trend | ≥ 0.72 |
| 2 | Confluence threshold — LCR | ≥ 0.55 (≥ 0.65 in TRENDING regime) |
| 3 | Risk per trade | 1% (USD_JPY LCR: 0.5%) |
| 4 | Session — London Trend | 07:15–12:00 UTC (first 15 min skipped) |
| 5 | Session — LCR | 17:00–19:59 UTC (Friday after 18:00 suppressed) |
| 6 | TSMOM gate | Block if 84-day momentum conflicts with MTF direction |
| 7 | Drawdown: halve size | -8% from peak |
| 8 | Drawdown: halt trading | -15% from peak |
| 9 | Monthly halt | -6% MTD → halt rest of month |
| 10 | Daily loss limit | -3% → halt rest of day |
| 11 | Spread spike | Suppress if spread > 3× session median |
| 12 | Correlation block | Block new positions if open position correlation > 0.70 |
| 13 | ML fallback | ML unavailable → rule-based confluence only |

---

## Rollout Controls

Anchor now supports env-driven rollout controls so you can freeze the live pilot without editing code:

- `INSTRUMENTS=EUR_USD,GBP_USD,USD_CAD`
- `ENABLE_TREND_ENGINE=true` + `TREND_PAPER_ONLY=true`
- `ENABLE_MR_ENGINE=true`
- `ENABLE_LCR_ENGINE=true`
- `ENABLE_M15_ENGINE=false`
- `TREND_RISK_PCT=0.001`
- `MR_RISK_PCT=0.0015`
- `LCR_RISK_PCT=0.0035`

This lets the scheduler keep evaluating all configured sleeves while only submitting live orders for the sleeves and risk budgets you explicitly enable in `.env`.

---

## Weight Optimization

L1 logistic regression optimizer using actual trade outcomes as labels.

```bash
# Backtest mode (CSV data, in-sample)
docker compose exec engine python -m anchor.backtesting.fit_weights

# Live trades mode (OOS validation — use at 200+ closed live trades)
docker compose exec engine python -m anchor.backtesting.fit_weights --live-trades

# Apply optimized weights to engine.py (also updates fit_weights.py + writes audit event)
docker compose exec engine python -m anchor.backtesting.fit_weights --live-trades --apply
```

Current weights were fit on 77 in-sample trades (below 200 minimum). Re-run `--live-trades --apply` once 200+ live trades have closed. Weight change audit trail is stored in the `system_events` table.

---

## Key Files

```
backend/anchor/
  config.py                            # all settings, instruments, risk params
  signals/engine.py                    # London Trend confluence engine (WEIGHTS, TSMOM, NR4)
  signals/london_close_reversion.py    # LCR engine (NY session, range-based mean reversion)
  signals/session_filter.py            # London 07:15-12:00 + LCR 17:00-20:00 UTC
  risk/drawdown_monitor.py             # drawdown/monthly halt logic
  risk/spread_monitor.py               # spread spike detection
  regime/hmm_detector.py               # 3-state HMM (TRENDING/RANGING/VOLATILE)
  backtesting/engine.py                # London Trend backtest runner
  backtesting/lcr_backtest.py          # LCR 8-year backtest (spread-adjusted)
  backtesting/lcr_walkforward.py       # LCR walk-forward validation
  backtesting/fit_weights.py           # L1 weight optimizer (backtest + live-trades modes)
  backtesting/mr_backtest.py           # mean-reversion backtest runner
  backtesting/aceb_backtest.py         # ACEB strategy (archived — failed statistical validation)
  scheduler/jobs.py                    # Celery tasks: signal eval, equity snapshot, LCR, risk
  execution/broker_client.py           # OANDA order execution
  data/polygon_history.py              # Polygon.io 8-year CSV downloader
```

---

## Common Commands

```bash
make up                            # start stack
make down                          # stop stack
make logs                          # tail engine + worker + beat logs
make shell-engine                  # bash into engine container
make upgrade                       # run pending DB migrations
make import-history                # import OANDA candles (H1/H4/D)

# Backtesting (requires data CSVs in /opt/anchor/data/)
make lcr-backtest-all              # LCR backtest — all 3 pairs
make lcr-walkforward PAIR=EUR_USD  # LCR walk-forward — one pair
make fit-weights                   # weight optimizer (backtest/CSV mode)
make fit-weights-live              # weight optimizer (live trades / OOS mode)
make ablation PAIR=EUR_USD         # trend ablation sweep — one pair
make ablation-all                  # trend ablation sweep — all pairs
make walk-forward PAIR=EUR_USD     # London Trend walk-forward
make monte-carlo PAIR=EUR_USD      # bootstrap CI + permutation test + forward sim
make instrument-confidence         # IC analysis per pair
```

---

## Deployment

VPS: `89.167.82.233` (`/opt/anchor`)

```bash
git push origin main               # triggers GitHub Actions deploy when configured
docker compose up -d --build       # manual fallback on the VPS
```

See [DEPLOY.md](DEPLOY.md) for full Hetzner VPS setup.

### GitHub Actions Deploy

This repo includes `.github/workflows/deploy.yml` for auto-deploy on push to `main`.

Required repository secrets:

- `DEPLOY_SSH_PRIVATE_KEY`
- `DEPLOY_KNOWN_HOSTS`
- `DEPLOY_VPS_HOST`
- `DEPLOY_VPS_USER`

The workflow reuses `infrastructure/scripts/deploy.sh` and verifies
`/api/v1/system/health` after deployment.

It now also:

- runs backend/frontend smoke tests before deploy
- exposes `deployed_sha` in `/api/v1/system/health`
- includes `.github/workflows/rollback.yml` for manual rollback to a chosen ref

---

## Monitoring

- **Dashboard**: private over Tailscale, served from the VPS's tailnet IP or MagicDNS name
- **Grafana**: keep private; use SSH tunnel unless you explicitly publish it to your tailnet
- **Telegram**: order execution, circuit breaker triggers, errors
- **system_events table**: audit trail for weight changes, regime transitions

---

## OOS Validation Checklist (before going live)

- [ ] 200+ closed live trades with signal data in DB
- [ ] Re-run `make fit-weights-live` — optimized weights close to current weights
- [ ] London Trend: PF > 1.2 over 200+ live trades
- [ ] LCR: PF > 1.3 over 200+ live trades (8yr backtest baseline: 1.37–1.67)
- [ ] Max drawdown stayed under 15% during demo period
- [ ] No parameter changes made during the 200-trade OOS window
