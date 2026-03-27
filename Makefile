.PHONY: up down dev logs shell-engine shell-db migrate upgrade seed backtest backtest-all lint test \
        download-history ablation walk-forward fit-weights instrument-confidence monte-carlo lcr-backtest lcr-backtest-all \
        arb-backtest arb-backtest-all event-study event-study-nfp nfp-drift nfp-signal-backtest fix-flow-backtest fix-signal-backtest month-end-rebalancing combined-sleeve-validation london-funnel-diagnostics london-rescue-matrix eurusd-london-direct-entry sleeve-validate-nfp sleeve-validate-fix calendar-backfill calendar-cleanup \
        lcr-walkforward lcr-walkforward-all london-walk-forward london-walk-forward-all v2-backtest v2-carry v2-cpi v2-value v2-sweep \
        threshold-sweep threshold-sweep-london portfolio-backtest live-perf-check

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f engine celery_worker watchdog

dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

dev-build:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

migrate:
	docker compose exec engine alembic revision --autogenerate -m "$(msg)"

upgrade:
	docker compose exec engine alembic upgrade head

downgrade:
	docker compose exec engine alembic downgrade -1

shell-engine:
	docker compose exec engine bash

shell-db:
	docker compose exec db psql -U anchor -d anchor

redis-cli:
	docker compose exec redis redis-cli

import-history:
	docker compose exec engine python -m anchor.data.bootstrap_history

import-dukascopy:
	docker compose exec engine python -m anchor.data.dukascopy

download-history:
	@mkdir -p data
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do 		echo "==> Downloading $$pair H1 (2018-2024)..."; 		docker compose exec engine python -m anchor.data.dukascopy 			--instrument $$pair --start 2018-01-01 --end 2024-12-31 			--timeframe H1 --output data/$${pair}_H1.csv; 		echo "==> Downloading $$pair H4 (2018-2024)..."; 		docker compose exec engine python -m anchor.data.dukascopy 			--instrument $$pair --start 2018-01-01 --end 2024-12-31 			--timeframe H4 --output data/$${pair}_H4.csv; 		echo "==> Downloading $$pair D (2018-2024)..."; 		docker compose exec engine python -m anchor.data.dukascopy 			--instrument $$pair --start 2018-01-01 --end 2024-12-31 			--timeframe D --output data/$${pair}_D.csv; 	done
	@echo "Download complete. Run make ablation next."

PAIR ?= EUR_USD
ablation:
	docker compose exec engine python -m anchor.backtesting.ablation_runner \
		--instrument $(PAIR) \
		--h1-csv  data/$(PAIR)_H1.csv \
		--h4-csv  data/$(PAIR)_H4.csv \
		--d-csv   data/$(PAIR)_D.csv \
		--balance 10000 \
		--suggest-weights \
		--export-csv data/ablation_$(PAIR).csv

ablation-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD NZD_USD USD_CHF EUR_GBP GBP_JPY; do \
		echo "========== ABLATION: $$pair =========="; \
		$(MAKE) ablation PAIR=$$pair; \
	done

walk-forward:
	docker compose exec engine python -m anchor.backtesting.walk_forward_backtest 		--instrument $(PAIR) 		--h1-csv  data/$(PAIR)_H1.csv 		--h4-csv  data/$(PAIR)_H4.csv 		--d-csv   data/$(PAIR)_D.csv 		--train-end  2021-12-31 		--val-end    2023-12-31 		--balance    10000

walk-forward-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do 		echo "========== WALK-FORWARD: $$pair =========="; 		$(MAKE) walk-forward PAIR=$$pair; 	done

mr-backtest:
	docker compose exec engine python -m anchor.backtesting.mr_backtest \
		--instrument $(PAIR) \
		--h1-csv  data/$(PAIR)_H1.csv \
		--d-csv   data/$(PAIR)_D.csv \
		--balance 10000

mr-backtest-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD NZD_USD USD_CHF EUR_GBP GBP_JPY; do \
		echo "========== MR BACKTEST: $$pair =========="; \
		$(MAKE) mr-backtest PAIR=$$pair; \
	done

## ── Math fixes & new strategies ─────────────────────────────────────────────

# Replace hand-tuned WEIGHTS in engine.py with L1 logistic regression coefficients.
# Uses in-sample trade data (before 2024-01-01) to find data-optimal component weights.
# Run with --apply to auto-patch engine.py. Re-run after 3 months of new live data.
fit-weights:
	docker compose exec engine python -m anchor.backtesting.fit_weights \
		--instruments EUR_USD,GBP_USD,USD_JPY,AUD_USD,GBP_JPY \
		--end 2024-01-01

fit-weights-apply:
	docker compose exec engine python -m anchor.backtesting.fit_weights \
		--instruments EUR_USD,GBP_USD,USD_JPY,AUD_USD,GBP_JPY \
		--end 2024-01-01 \
		--apply

# OOS validation using live trades from PostgreSQL.
# Run once you have 200+ closed live trades. Triggered automatically by
# the daily check_fit_weights_trigger Celery task (sends Telegram alert).
fit-weights-live:
	docker compose exec engine python -m anchor.backtesting.fit_weights --live-trades

fit-weights-live-apply:
	docker compose exec engine python -m anchor.backtesting.fit_weights --live-trades --apply

# Check current live trade count and when next regression trigger fires.
fit-weights-progress:
	docker compose exec engine python -c "\
from sqlalchemy import create_engine, text; \
from anchor.config import settings; \
db = create_engine(settings.sync_database_url); \
conn = db.connect(); \
n = conn.execute(text(\"SELECT COUNT(*) FROM trades WHERE closed_at IS NOT NULL AND signal_id IS NOT NULL\")).scalar(); \
last = conn.execute(text(\"SELECT metadata FROM system_events WHERE event_type='FW_TRIGGER_RAN' ORDER BY event_at DESC LIMIT 1\")).fetchone(); \
last_n = last[0].get('trade_count', 0) if last and last[0] else 0; \
nxt = ((n // 200) + 1) * 200 if n < 200 else ((n // 200) + 1) * 200; \
print(f'Closed trades: {n}'); print(f'Last regression at: {last_n} trades'); print(f'Next trigger at: {nxt} trades ({nxt - n} to go)') \
"

# IC-based instrument ranking: which pairs to keep, deprioritize, or drop.
# Removes guesswork from pair selection using statistical signal reliability.
instrument-confidence:
	docker compose exec engine python -m anchor.backtesting.instrument_confidence \
		--instruments EUR_USD,GBP_USD,USD_JPY,AUD_USD,NZD_USD,USD_CHF,EUR_GBP,GBP_JPY \
		--end 2024-01-01

# Monte Carlo validation: bootstrap CIs + permutation test + forward equity sims.
# Answers: "Is my edge real?" and "What's my realistic 6-month outcome range?"
# Use --forward-months and --trades-per-month to match your expected trade frequency.
monte-carlo:
	docker compose exec engine python -m anchor.backtesting.monte_carlo \
		--instrument $(or $(PAIR),EUR_USD) \
		--end 2024-01-01 \
		--forward-months 6 \
		--trades-per-month 20

monte-carlo-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD GBP_JPY; do \
		echo "========== MONTE CARLO: $$pair =========="; \
		docker compose exec engine python -m anchor.backtesting.monte_carlo \
			--instrument $$pair --end 2024-01-01 --forward-months 6; \
	done

# Generic macro event study. Uses a bind-mounted ad hoc container on the compose network
# so it can access both the current workspace code and the running Postgres calendar DB.
EVENT_QUERY ?=
event-study:
	@DB_HOST=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_HOST=' | cut -d= -f2-); \
	DB_PORT=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PORT=' | cut -d= -f2-); \
	DB_USER=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_USER=' | cut -d= -f2-); \
	DB_PASSWORD=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PASSWORD=' | cut -d= -f2-); \
	DB_NAME=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_NAME=' | cut -d= -f2-); \
	docker run --rm \
		--network anchor_default \
		-e DB_HOST="$$DB_HOST" \
		-e DB_PORT="$$DB_PORT" \
		-e DB_USER="$$DB_USER" \
		-e DB_PASSWORD="$$DB_PASSWORD" \
		-e DB_NAME="$$DB_NAME" \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.event_study \
			--pairs EUR_USD,GBP_USD,USD_JPY,USD_CAD \
			--currencies USD,EUR,GBP,JPY,CAD \
			--impacts HIGH \
			$(if $(EVENT_QUERY),--event-query '$(EVENT_QUERY)',) \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--windows 1,4,8,24"

event-study-nfp:
	$(MAKE) event-study EVENT_QUERY='Non-Farm Employment Change'

nfp-drift:
	@DB_HOST=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_HOST=' | cut -d= -f2-); \
	DB_PORT=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PORT=' | cut -d= -f2-); \
	DB_USER=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_USER=' | cut -d= -f2-); \
	DB_PASSWORD=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PASSWORD=' | cut -d= -f2-); \
	DB_NAME=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_NAME=' | cut -d= -f2-); \
	docker run --rm \
		--network anchor_default \
		-e APP_ENV=production \
		-e DB_HOST="$$DB_HOST" \
		-e DB_PORT="$$DB_PORT" \
		-e DB_USER="$$DB_USER" \
		-e DB_PASSWORD="$$DB_PASSWORD" \
		-e DB_NAME="$$DB_NAME" \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.nfp_drift_backtest \
			--pairs EUR_USD,GBP_USD,USD_JPY,USD_CAD \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--windows 4,8,24 \
			--include-delayed \
			--include-fade"

nfp-signal-backtest:
	@DB_HOST=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_HOST=' | cut -d= -f2-); \
	DB_PORT=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PORT=' | cut -d= -f2-); \
	DB_USER=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_USER=' | cut -d= -f2-); \
	DB_PASSWORD=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PASSWORD=' | cut -d= -f2-); \
	DB_NAME=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_NAME=' | cut -d= -f2-); \
	docker run --rm \
		--network anchor_default \
		-e APP_ENV=production \
		-e DB_HOST="$$DB_HOST" \
		-e DB_PORT="$$DB_PORT" \
		-e DB_USER="$$DB_USER" \
		-e DB_PASSWORD="$$DB_PASSWORD" \
		-e DB_NAME="$$DB_NAME" \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.nfp_signal_backtest \
			--pairs EUR_USD,GBP_USD,USD_CAD,USD_JPY \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--horizon-hours 24 \
			--agreements ALIGNED \
			--sizes BIG \
			--spread-mult $(or $(SIGNAL_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(SIGNAL_SLIPPAGE_PIPS),0.0)"

fix-flow-backtest:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.fix_flow_backtest \
			--pairs EUR_USD,GBP_USD,USD_JPY \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--pre-hours 1 \
			--post-hours 1 \
			--min-pre-move-pips 5 \
			--focus-setup post_fix_continuation \
			--focus-month-end REG,ME \
			--spread-mult $(or $(FIX_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(FIX_SLIPPAGE_PIPS),0.0)"

fix-signal-backtest:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.fix_signal_backtest \
			--pairs USD_JPY,EUR_USD,GBP_USD \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--pre-hours 1 \
			--post-hours 1 \
			--min-pre-move-pips 5 \
			--tags REG,ME \
			--spread-mult $(or $(FIX_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(FIX_SLIPPAGE_PIPS),0.0)"

month-end-rebalancing:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.month_end_rebalancing \
			--pairs EUR_USD,GBP_USD,USD_JPY \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--offsets=-1,0,1 \
			--pre-hours 1 \
			--post-hours 1 \
			--min-pre-move-pips 5 \
			--focus-setup post_fix_continuation \
			--spread-mult $(or $(ME_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(ME_SLIPPAGE_PIPS),0.0)"

combined-sleeve-validation:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.combined_sleeve_validation \
			--trend-pairs '$(if $(TREND_PAIRS),$(TREND_PAIRS),EUR_JPY)' \
			--lcr-pairs '$(if $(LCR_PAIRS),$(LCR_PAIRS),EUR_JPY,EUR_USD,NZD_USD)' \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--spread-mult $(or $(COMBINED_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(COMBINED_SLIPPAGE_PIPS),0.0)"

london-funnel-diagnostics:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.london_funnel_diagnostics \
			--pairs '$(if $(TREND_PAIRS),$(TREND_PAIRS),EUR_JPY)' \
			--start 2018-01-01 \
			--end 2025-12-31"

london-rescue-matrix:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.london_rescue_matrix \
			--pairs '$(if $(TREND_PAIRS),$(TREND_PAIRS),EUR_JPY)' \
			--thresholds '$(if $(TREND_THRESHOLDS),$(TREND_THRESHOLDS),0.55,0.76)' \
			--start 2018-01-01 \
			--end 2025-12-31"

eurusd-london-direct-entry:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.eurusd_london_direct_entry_backtest \
			--pair EUR_USD \
			--threshold $(or $(EURUSD_TREND_THRESHOLD),0.55) \
			$(if $(NO_HMM),--no-hmm,) \
			--start 2018-01-01 \
			--end 2025-12-31"

sleeve-validate-nfp:
	@DB_HOST=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_HOST=' | cut -d= -f2-); \
	DB_PORT=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PORT=' | cut -d= -f2-); \
	DB_USER=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_USER=' | cut -d= -f2-); \
	DB_PASSWORD=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PASSWORD=' | cut -d= -f2-); \
	DB_NAME=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_NAME=' | cut -d= -f2-); \
	docker run --rm \
		--network anchor_default \
		-e APP_ENV=production \
		-e DB_HOST="$$DB_HOST" \
		-e DB_PORT="$$DB_PORT" \
		-e DB_USER="$$DB_USER" \
		-e DB_PASSWORD="$$DB_PASSWORD" \
		-e DB_NAME="$$DB_NAME" \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.nfp_signal_backtest \
			--pairs EUR_USD,GBP_USD,USD_CAD,USD_JPY \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--horizon-hours 24 \
			--agreements ALIGNED \
			--sizes BIG \
			--spread-mult $(or $(SIGNAL_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(SIGNAL_SLIPPAGE_PIPS),0.0) \
			--export-csv /app/data/validation_nfp && \
			python -m anchor.backtesting.sleeve_validation \
			--csv /app/data/validation_nfp.observations.csv \
			--pnl-col ret_pips_net \
			--group-col pair \
			--min-trades 5 \
			--block-size 2 \
			--trades-per-month 1"

sleeve-validate-fix:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.fix_flow_backtest \
			--pairs EUR_USD,GBP_USD,USD_JPY \
			--start 2018-01-01 \
			--end 2025-12-31 \
			--pre-hours 1 \
			--post-hours 1 \
			--min-pre-move-pips 5 \
			--focus-setup post_fix_continuation \
			--focus-month-end REG,ME \
			--spread-mult $(or $(FIX_SPREAD_MULT),1.0) \
			--slippage-pips $(or $(FIX_SLIPPAGE_PIPS),0.0) \
			--export-csv /app/data/validation_fix && \
			python -m anchor.backtesting.sleeve_validation \
			--csv /app/data/validation_fix.observations.csv \
			--pnl-col ret_pips_net \
			--group-col pair \
			--filter-col setup \
			--filter-values post_fix_continuation \
			--min-trades 50 \
			--block-size 5 \
			--trades-per-month 20"

calendar-backfill:
	@DB_HOST=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_HOST=' | cut -d= -f2-); \
	DB_PORT=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PORT=' | cut -d= -f2-); \
	DB_USER=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_USER=' | cut -d= -f2-); \
	DB_PASSWORD=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PASSWORD=' | cut -d= -f2-); \
	DB_NAME=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_NAME=' | cut -d= -f2-); \
	docker run --rm \
		--network anchor_default \
		-e APP_ENV=production \
		-e DB_HOST="$$DB_HOST" \
		-e DB_PORT="$$DB_PORT" \
		-e DB_USER="$$DB_USER" \
		-e DB_PASSWORD="$$DB_PASSWORD" \
		-e DB_NAME="$$DB_NAME" \
		-v /opt/anchor/backend:/app \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.data.economic_calendar_backfill \
			--start 2018-01-01 \
			--end 2025-12-31"

calendar-cleanup:
	@DB_HOST=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_HOST=' | cut -d= -f2-); \
	DB_PORT=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PORT=' | cut -d= -f2-); \
	DB_USER=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_USER=' | cut -d= -f2-); \
	DB_PASSWORD=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_PASSWORD=' | cut -d= -f2-); \
	DB_NAME=$$(docker inspect anchor_engine --format '{{range .Config.Env}}{{println .}}{{end}}' | rg '^DB_NAME=' | cut -d= -f2-); \
	docker run --rm \
		--network anchor_default \
		-e APP_ENV=production \
		-e DB_HOST="$$DB_HOST" \
		-e DB_PORT="$$DB_PORT" \
		-e DB_USER="$$DB_USER" \
		-e DB_PASSWORD="$$DB_PASSWORD" \
		-e DB_NAME="$$DB_NAME" \
		-v /opt/anchor/backend:/app \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.data.economic_calendar_cleanup --future-days 1"

# Backtest the London Close Reversal (NY session) strategy in isolation.
# Tests EUR_USD, GBP_USD, USD_JPY only — the pairs with documented LCR edge.
lcr-backtest:
	docker compose exec engine python -m anchor.backtesting.lcr_backtest \
		--instrument $(or $(PAIR),EUR_USD) \
		--h1-csv  data/$(or $(PAIR),EUR_USD)_H1.csv \
		--balance 10000

lcr-backtest-all:
	@for pair in EUR_USD GBP_USD USD_JPY; do \
		echo "========== LCR BACKTEST: $$pair =========="; \
		$(MAKE) lcr-backtest PAIR=$$pair; \
	done

# Standalone Asian Range Breakout validation on the 5-pair research universe.
arb-backtest:
	docker run --rm \
		-v /opt/anchor/backend:/app \
		-v /opt/anchor/data:/app/data \
		-w /app \
		anchor-engine \
		bash -lc "python -m anchor.backtesting.arb_backtest \
			--instrument $(or $(PAIR),EUR_USD) \
			--h1-csv /app/data/$(or $(PAIR),EUR_USD)_H1.csv \
			--balance 10000"

arb-backtest-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do \
		echo "========== ARB BACKTEST: $$pair =========="; \
		$(MAKE) arb-backtest PAIR=$$pair; \
	done

lcr-walkforward:
	docker compose exec engine python -m anchor.backtesting.walk_forward \
		--instrument $(or $(PAIR),EUR_USD) \
		--h1-csv data/$(or $(PAIR),EUR_USD)_H1.csv

lcr-walkforward-all:
	@for pair in EUR_USD GBP_USD USD_JPY; do \
		$(MAKE) lcr-walkforward PAIR=$$pair; \
	done

## ── London Trend walk-forward ───────────────────────────────────────────────

# Walk-forward OOS validation for the London Trend strategy.
# Tests each annual window 2018-2024 independently (no parameter fitting per window).
# Target: 75%+ windows profitable, avg PF > 1.2.
london-walk-forward:
	docker compose exec engine python -m anchor.backtesting.london_walk_forward \
		--instrument $(or $(PAIR),EUR_USD) \
		--h1-csv data/$(or $(PAIR),EUR_USD)_H1.csv \
		--h4-csv data/$(or $(PAIR),EUR_USD)_H4.csv \
		--d-csv  data/$(or $(PAIR),EUR_USD)_D.csv

london-walk-forward-all:
	@for pair in EUR_USD GBP_USD NZD_USD USD_CAD EUR_JPY AUD_USD; do \
		echo "========== LONDON WF: $$pair =========="; \
		$(MAKE) london-walk-forward PAIR=$$pair; \
	done

## ── Threshold sensitivity sweep ─────────────────────────────────────────────

# Sweeps confluence threshold (0.40→0.70 for LCR, 0.65→0.78 for London Trend).
# A robust edge keeps PF > 1.0 across all thresholds (not just at the hand-tuned value).
threshold-sweep:
	docker compose exec engine python -m anchor.backtesting.threshold_sweep \
		--instrument $(or $(PAIR),EUR_USD) \
		--strategy lcr \
		--h1-csv data/$(or $(PAIR),EUR_USD)_H1.csv

threshold-sweep-london:
	docker compose exec engine python -m anchor.backtesting.threshold_sweep \
		--instrument $(or $(PAIR),EUR_USD) \
		--strategy london \
		--h1-csv data/$(or $(PAIR),EUR_USD)_H1.csv \
		--h4-csv data/$(or $(PAIR),EUR_USD)_H4.csv \
		--d-csv  data/$(or $(PAIR),EUR_USD)_D.csv

## ── Portfolio backtest ───────────────────────────────────────────────────────

# Runs LCR on all 6 active pairs and combines into a single portfolio equity curve.
# Measures: combined MaxDD, diversification ratio, inter-pair correlation.
# Key question: do 6 pairs together compound drawdowns or diversify them?
portfolio-backtest:
	docker compose exec engine python -m anchor.backtesting.portfolio_backtest \
		--pairs EUR_USD,GBP_USD,NZD_USD,USD_CAD,EUR_JPY,AUD_USD \
		--data-dir data \
		--balance 10000

v2-backtest:
	docker compose exec engine python -m anchor.backtesting.v2_portfolio_backtest \
		--data-dir /app/data \
		--currencies '$(if $(CURRENCIES),$(CURRENCIES),EUR,JPY,GBP,CHF,CAD,AUD,NZD,SEK,NOK)' \
		$(if $(CARRY_CSV),--carry-csv $(CARRY_CSV),) \
		$(if $(VALUE_CSV),--value-csv $(VALUE_CSV),) \
		--balance $(or $(BALANCE),100000)

v2-carry:
	docker compose exec engine python -m anchor.data.v2_carry \
		--api-key $(FRED_API_KEY) \
		$(if $(SERIES_MAP_JSON),--series-map-json $(SERIES_MAP_JSON),) \
		--output $(or $(OUTPUT),/app/data/v2_carry.csv)

v2-cpi:
	docker compose exec engine python -m anchor.data.v2_cpi \
		--api-key $(FRED_API_KEY) \
		$(if $(SERIES_MAP_JSON),--series-map-json $(SERIES_MAP_JSON),) \
		--output $(or $(OUTPUT),/app/data/v2_cpi.csv)

v2-value:
	docker compose exec engine python -m anchor.data.v2_value \
		--data-dir /app/data \
		--cpi-csv $(or $(CPI_CSV),/app/data/v2_cpi.csv) \
		--currencies '$(if $(CURRENCIES),$(CURRENCIES),EUR,JPY,GBP,CHF,CAD,AUD,NZD,SEK,NOK)' \
		--lookback-months $(or $(LOOKBACK_MONTHS),60) \
		--output $(or $(OUTPUT),/app/data/v2_value.csv)

v2-sweep:
	docker compose exec engine python -m anchor.backtesting.v2_experiment_sweep \
		--data-dir /app/data \
		--currencies '$(if $(CURRENCIES),$(CURRENCIES),EUR,JPY,GBP,CAD,AUD,NZD)' \
		$(if $(CARRY_CSV),--carry-csv $(CARRY_CSV),) \
		$(if $(VALUE_CSV),--value-csv $(VALUE_CSV),) \
		--balance $(or $(BALANCE),100000)

v2-pair-momentum:
	docker compose exec engine python -m anchor.backtesting.v2_pair_momentum_backtest \
		--data-dir /app/data \
		--pairs '$(if $(PAIRS),$(PAIRS),EUR_USD,USD_JPY,GBP_USD,USD_CNY,USD_CAD,AUD_USD,USD_CHF,EUR_JPY,EUR_GBP,NZD_USD)' \
		--momentum-lookback-months $(or $(LOOKBACK_MONTHS),6) \
		--rebalance-frequency $(or $(REBALANCE_FREQUENCY),quarterly) \
		--top-n $(or $(TOP_N),3) \
		--balance $(or $(BALANCE),100000) \
		$(if $(EXPORT_JSON),--export-json $(EXPORT_JSON),) \
		$(if $(EXPORT_MONTHLY),--export-monthly $(EXPORT_MONTHLY),)

futures-v1-backtest:
	docker compose exec engine python -m anchor.backtesting.futures_v1_backtest \
		--data-dir /app/data \
		--markets '$(if $(MARKETS),$(MARKETS),MES,MNQ,ZN,MGC,MCL)' \
		--balance $(or $(BALANCE),100000) \
		--trend-lookback-days $(or $(TREND_LOOKBACK_DAYS),126) \
		--vol-lookback-days $(or $(VOL_LOOKBACK_DAYS),20) \
		--rebalance-frequency $(or $(REBALANCE_FREQUENCY),weekly) \
		--threshold $(or $(THRESHOLD),0.0) \
		$(if $(EXPORT_JSON),--export-json $(EXPORT_JSON),) \
		$(if $(EXPORT_DAILY),--export-daily $(EXPORT_DAILY),)

futures-v1-sweep:
	PYTHONPATH=/opt/anchor/backend /opt/anchor/.venv/bin/python -m anchor.backtesting.futures_v1_experiment_sweep \
		--data-dir /opt/anchor/data \
		--markets '$(if $(MARKETS),$(MARKETS),MES,MNQ,ZN,MGC,MCL)' \
		--balance $(or $(BALANCE),100000) \
		--export-csv /opt/anchor/data/futures_v1_sweep.csv \
		--export-json /opt/anchor/data/futures_v1_sweep.json

futures-history:
	PYTHONPATH=/opt/anchor/backend /opt/anchor/.venv/bin/python -m anchor.data.futures_history \
		--markets '$(if $(MARKETS),$(MARKETS),MES,MNQ,ZN,MGC,MCL)' \
		--output-dir /opt/anchor/data \
		--period $(or $(PERIOD),10y)

## ── Live performance check ───────────────────────────────────────────────────

# Manually trigger the live performance monitoring job (normally runs daily via Celery).
# Compares rolling live WR/PF vs 8-year backtest benchmarks.
# Alerts if actual performance degrades significantly.
live-perf-check:
	docker compose exec celery_worker celery -A anchor.scheduler.celery_app call \
		anchor.scheduler.jobs.monitor_live_performance

import-cot:
	docker compose exec engine python -m anchor.data.cot_parser

backtest:
	docker compose exec engine python -m anchor.backtesting.engine \
		--instrument $(PAIR) \
		--csv /app/data/$(PAIR)_H1.csv \
		--balance 100000 \
		--analyze \
		--export-csv /app/data/backtest_$(PAIR).csv

backtest-all:
	@mkdir -p data
	@echo "Step 1/2: downloading 2018-2026 H1/H4/D data via Polygon for all 5 pairs..."
	docker compose exec engine python -m anchor.data.polygon_history \
		--pairs EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD \
		--start 2018-01-01 \
		--end 2026-01-01 \
		--output-dir /app/data
	@echo "Step 2/2: ablation backtest across all 5 live pairs (balance=100000)..."
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do \
		echo "========== $$pair =========="; \
		docker compose exec engine python -m anchor.backtesting.ablation_runner \
			--instrument $$pair \
			--h1-csv  /app/data/$${pair}_H1.csv \
			--h4-csv  /app/data/$${pair}_H4.csv \
			--d-csv   /app/data/$${pair}_D.csv \
			--balance 100000 \
			--suggest-weights \
			--export-csv /app/data/ablation_$${pair}.csv; \
	done
	@echo "Done. Results in data/ablation_*.csv"

retrain:
	docker compose exec celery_worker celery -A anchor.scheduler.celery_app call anchor.ml.retraining.run_retraining

lint:
	docker compose exec engine ruff check anchor/
	docker compose exec engine mypy anchor/

test:
	docker compose exec engine pytest tests/ -v

test-unit:
	docker compose exec engine pytest tests/unit/ -v

test-integration:
	docker compose exec engine pytest tests/integration/ -v -m "not live"

deploy:
	bash infrastructure/scripts/deploy.sh

backup:
	bash infrastructure/scripts/backup.sh
