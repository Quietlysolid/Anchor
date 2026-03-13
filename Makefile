.PHONY: up down dev logs shell-engine shell-db migrate upgrade seed backtest backtest-all lint test \
        download-history ablation walk-forward fit-weights instrument-confidence monte-carlo lcr-backtest lcr-backtest-all \
        lcr-walkforward lcr-walkforward-all

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
	docker compose exec engine python -m anchor.data.oanda_history

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

lcr-walkforward:
	docker compose exec engine python -m anchor.backtesting.lcr_walkforward \
		--instrument $(or $(PAIR),EUR_USD) \
		--h1-csv data/$(or $(PAIR),EUR_USD)_H1.csv

lcr-walkforward-all:
	@for pair in EUR_USD GBP_USD USD_JPY; do \
		$(MAKE) lcr-walkforward PAIR=$$pair; \
	done

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
