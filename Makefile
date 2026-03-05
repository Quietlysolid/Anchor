.PHONY: up down dev logs shell-engine shell-db migrate upgrade seed backtest lint test

# ── Production ─────────────────────────────────────────────────────────────────
up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f engine celery_worker watchdog

# ── Development ────────────────────────────────────────────────────────────────
dev:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

dev-build:
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build

# ── Database ───────────────────────────────────────────────────────────────────
migrate:
	docker compose exec engine alembic revision --autogenerate -m "$(msg)"

upgrade:
	docker compose exec engine alembic upgrade head

downgrade:
	docker compose exec engine alembic downgrade -1

# ── Shells ─────────────────────────────────────────────────────────────────────
shell-engine:
	docker compose exec engine bash

shell-db:
	docker compose exec db psql -U anchor -d anchor

redis-cli:
	docker compose exec redis redis-cli

# ── Data ───────────────────────────────────────────────────────────────────────
import-history:
	docker compose exec engine python -m anchor.data.oanda_history

import-dukascopy:
	docker compose exec engine python -m anchor.data.dukascopy

import-cot:
	docker compose exec engine python -m anchor.data.cot_parser

# ── ML ─────────────────────────────────────────────────────────────────────────
backtest:
	docker compose exec engine python -m anchor.backtesting.engine

retrain:
	docker compose exec celery_worker celery -A anchor.scheduler.celery_app call anchor.ml.retraining.run_retraining

# ── Code quality ───────────────────────────────────────────────────────────────
lint:
	docker compose exec engine ruff check anchor/
	docker compose exec engine mypy anchor/

test:
	docker compose exec engine pytest tests/ -v

test-unit:
	docker compose exec engine pytest tests/unit/ -v

test-integration:
	docker compose exec engine pytest tests/integration/ -v -m "not live"

# ── Deploy ─────────────────────────────────────────────────────────────────────
deploy:
	bash infrastructure/scripts/deploy.sh

backup:
	bash infrastructure/scripts/backup.sh
