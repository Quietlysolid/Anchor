# Anchor — Systematic Futures Trading System

Personal algorithmic trading system running on a Hetzner VPS (Ubuntu 24.04).
Paper trading via Interactive Brokers. One strategy live: **Futures v1**.

---

## Maintenance rule

Before closing any session that changes the files listed below, update the
relevant section of this document. The rule is a checklist item, not a
separate task — check it when you finish.

| What changed | Section to update |
|---|---|
| `execution/ibkr_client.py`, `main.py`, `execution/broker_client.py` | Data flow |
| `futures/strategy.py`, `futures/rebalance.py`, `scheduler/futures_tasks.py` | Strategy wiring |
| `docker-compose.yml`, `infrastructure/scripts/deploy.sh`, `.github/workflows/` | Infrastructure |
| `database/models.py`, `alembic/versions/` | Database schema |
| Any module in "What is wired" or "What is NOT wired" | Wiring status |
| `config.py` (new settings) | Configuration reference |

---

## Repository layout

```
backend/anchor/
  api/           FastAPI routers + WebSocket fanout
  data/          IBKR stream client (broker_stream.py, broker_history.py)
  database/      SQLAlchemy models, Alembic migrations, repositories
  execution/     IBKR TWS client, broker_client.py facade, order manager
  futures/       Strategy v1 — strategy.py, rebalance.py, contracts.py, io.py
  monitoring/    Heartbeat, watchdog, alerts
  regime/        HMM detector (trained, NOT wired to Futures v1 yet)
  risk/          Drawdown monitor, daily limiter, position sizer, spread monitor
  scheduler/     Celery app, beat schedule, futures_tasks.py, snapshot_tasks.py
  signals/       Near-empty — ghost of legacy FX architecture, can be deleted
frontend/src/    React 18 + Vite + TypeScript dashboard
infrastructure/  nginx, postgres init, prometheus, grafana, deploy scripts
```

---

## Active strategy: Futures v1

**Signal:** 63-day trailing return per market → LONG / SHORT / FLAT.
**Sizing:** inverse-volatility weighting, 2.5× gross exposure target.
**Markets:** MNQ (Micro Nasdaq-100), ZN (10-Year Treasury), MGC (Micro Gold), MCL (Micro Crude Oil).
**Rebalance:** daily 22:15 UTC, Monday–Friday, via Celery Beat.
**Mode:** paper-only (`futures_v1_paper_only = True`, `TRADING_MODE=paper`).

Entry in `config.py`:
```python
futures_v1_enabled = True
futures_v1_paper_only = True
futures_v1_auto_execute = True
futures_v1_markets = ["MNQ", "ZN", "MGC", "MCL"]
futures_v1_trend_lookback_days = 63
futures_daily_signal_time_utc = "22:15"
```

---

## Data flow (live path)

```
IBKR TWS API (port 4002 paper / 4001 live)
  └─ IBKRBrokerClient (execution/ibkr_client.py)
       threading.Lock serialises all TWS calls
       _positions_sync() uses reqAccountUpdates → updatePortfolio callback
         → returns live market price + unrealized P&L
  └─ BrokerClient (execution/broker_client.py)
       thin async facade over IBKRBrokerClient
       used by: main.py reconcile loop, futures_tasks.py rebalance, reconciler.py

main.py  _reconcile_account()  — runs every 7 seconds
  get_account_summary()   → set_account_info(balance, equity)
  get_open_trades()       → set_open_positions_count() + set_cached_broker_state()
  get_pending_orders()    → set_cached_broker_state() (try/except, returns [] on timeout)
  publish to Redis:       account channel, positions channel

Redis pub/sub
  └─ _redis_fanout() in main.py → ws_manager.broadcast() → browser WebSocket
       channels: ticks, regime, signals, positions, orders, account

API endpoints (served from cache — no live broker calls on request path)
  GET /api/v1/positions           → get_cached_positions() from system.py
  GET /api/v1/orders              → get_cached_orders() from system.py
  GET /api/v1/system/health       → uses cached _open_positions_count
  GET /api/v1/system/homepage-snapshot → uses cached state + DB queries
```

**Rebalance path:**
```
Celery Beat (22:15 UTC weekdays)
  └─ run_futures_v1_rebalance (scheduler/futures_tasks.py)
       BrokerClient.get_account_summary()  → equity
       BrokerClient.get_open_positions()   → current positions
       build_futures_rebalance_plan()      → target deltas
       BrokerClient.place_order() per delta
       _drawdown_monitor / _daily_limiter check before executing
```

---

## What IS wired and running

| Component | File | Notes |
|---|---|---|
| Futures v1 rebalance | `scheduler/futures_tasks.py` | Fires 22:15 UTC Mon–Fri |
| Equity snapshots | `scheduler/snapshot_tasks.py` | Every 15 min |
| Drawdown monitor | `risk/drawdown_monitor.py` | Singleton in `_shared.py`; 8% reduce, 15% halt |
| Daily loss limiter | `risk/daily_limiter.py` | Singleton in `_shared.py`; 3% daily halt |
| Spread monitor | `risk/spread_monitor.py` | Singleton in `_shared.py` |
| Broker reconciler | `execution/reconciler.py` | Called from futures_tasks.py |
| Position cache | `api/routers/system.py` | In-memory globals, set by reconcile loop |
| WebSocket fanout | `main.py` + `api/websocket.py` | Redis pub/sub → browser |
| Heartbeat | `monitoring/heartbeat.py` | Engine-only service |
| Watchdog | `monitoring/watchdog.py` | Separate container (anchor_watchdog) |
| Weekend guard | `risk/weekend_guard.py` | Wired in main.py but **disabled** for futures (`futures_weekend_guard_enabled = False`) |

---

## What is NOT wired (tech debt — do not assume these are active)

| Item | Location | Status |
|---|---|---|
| HMM regime detector | `regime/hmm_detector.py` | Trained and saved; **not called** by Futures v1 strategy or rebalance |
| signals/ directory | `signals/retail_sentiment.py` | Near-empty stub, ghost of FX era; safe to delete |
| DB columns `oanda_trade_id` / `oanda_order_id` | `database/models.py`, `execution/reconciler.py` | Still named oanda_*; migration `0007_rename_oanda_to_ibkr.py` exists on disk but columns are still old names in models.py — reconciler references them directly |
| Celery async DB bootstrap | `scheduler/futures_tasks.py`, `snapshot_tasks.py` | Each task calls `await init_db()` inline; should be centralised before adding a third async-DB task |
| MLflow auto-logging | `docker-compose.yml` mlflow service | Container runs, UI accessible at :5000, but nothing auto-logs to it |

---

## Database schema (current head: 0007)

Key tables: `positions`, `orders`, `trades`, `signals`, `equity_curve_points`,
`system_events`, `tick_data`, `regime_history`.

`positions` and `orders` still have `oanda_trade_id` / `oanda_order_id` columns
(renamed to `ibkr_*` is pending — migration file exists but models not updated yet).

---

## Infrastructure

**Docker Compose services** (all on one VPS):

| Service | Container | Exposes |
|---|---|---|
| FastAPI engine | anchor_engine | 127.0.0.1:8000 |
| Celery worker | anchor_celery | — |
| Celery beat | anchor_beat | — |
| Watchdog | anchor_watchdog | — |
| Frontend (Vite/React) | anchor_frontend | 127.0.0.1:3000 |
| nginx (reverse proxy) | anchor_nginx | 0.0.0.0:8080 → public |
| PostgreSQL/TimescaleDB | anchor_db | 127.0.0.1:5432 |
| Redis | anchor_redis | — (internal) |
| IB Gateway | anchor_ib_gateway | 127.0.0.1:4001/4002/5900 |
| MLflow | anchor_mlflow | 127.0.0.1:5000 |
| Prometheus | anchor_prometheus | 127.0.0.1:9090 |
| Grafana | anchor_grafana | 127.0.0.1:3001 |

**Deploy pipeline:**
- Push to `main` → GitHub Actions (`.github/workflows/deploy.yml`)
- Smoke test (ruff, import check, pytest) → SSH deploy via `infrastructure/scripts/deploy.sh`
- deploy.sh: 3 isolated SSH stages — BUILD (fail-fast), MIGRATE (warn-and-continue), RESTART (always runs if build succeeded)
- `build_info.py` is gitignored; deploy.sh writes it with `DEPLOYED_SHA` before building images
- Health check verifies `deployed_sha` matches `github.sha`

**Rollback:** `.github/workflows/rollback.yml` — runs same smoke tests first, then deploys the specified ref, verifies SHA.

**New Python files** must be `docker cp`'d into the running container OR a full image rebuild is needed (files are baked in, not volume-mounted).

---

## Configuration reference (key .env vars)

```
TRADING_MODE=paper          # paper | live
TWS_USERID / TWS_PASSWORD   # IB Gateway credentials
IBKR_HOST=ib_gateway        # container name
IBKR_PORT=4002              # 4002=paper, 4001=live
IBKR_PAPER_ACCOUNT_ID=...   # DU... account number
DB_PASSWORD=...
GRAFANA_PASSWORD=...
FUTURES_V1_ENABLED=true
FUTURES_V1_PAPER_ONLY=true
FUTURES_DAILY_SIGNAL_TIME_UTC=22:15
```

---

## Running commands

```bash
# On the VPS — compose is at /opt/anchor
docker compose logs -f engine
docker compose logs -f celery_worker
docker compose exec engine python -m anchor.futures.cli_targets   # dry-run targets

# From local machine — always use full path
docker compose -f /opt/anchor/docker-compose.yml ps
```
