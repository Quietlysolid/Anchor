.PHONY: up down dev logs shell-engine shell-db migrate upgrade seed backtest lint test         download-history ablation walk-forward

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
	docker compose exec engine python -m anchor.backtesting.ablation_runner 		--instrument $(PAIR) 		--h1-csv  data/$(PAIR)_H1.csv 		--h4-csv  data/$(PAIR)_H4.csv 		--d-csv   data/$(PAIR)_D.csv 		--balance 10000 		--export-csv data/ablation_$(PAIR).csv

ablation-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do 		echo "========== ABLATION: $$pair =========="; 		$(MAKE) ablation PAIR=$$pair; 	done

walk-forward:
	docker compose exec engine python -m anchor.backtesting.walk_forward_backtest 		--instrument $(PAIR) 		--h1-csv  data/$(PAIR)_H1.csv 		--h4-csv  data/$(PAIR)_H4.csv 		--d-csv   data/$(PAIR)_D.csv 		--train-end  2021-12-31 		--val-end    2023-12-31 		--balance    10000

walk-forward-all:
	@for pair in EUR_USD GBP_USD USD_JPY AUD_USD USD_CAD; do 		echo "========== WALK-FORWARD: $$pair =========="; 		$(MAKE) walk-forward PAIR=$$pair; 	done

import-cot:
	docker compose exec engine python -m anchor.data.cot_parser

backtest:
	docker compose exec engine python -m anchor.backtesting.engine

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
