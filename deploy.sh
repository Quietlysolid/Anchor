#!/bin/bash
# Deploy current code on the VPS (baked images, no volume mounts)
# Usage: ./deploy.sh
set -euo pipefail

DEPLOY_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo local-sync)"

echo "==> deploying current working tree (${DEPLOY_SHA})..."
echo "==> building images..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml build \
  engine celery_worker celery_beat watchdog mlflow frontend nginx

echo "==> starting database..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db

echo "==> ensuring MLflow database exists..."
mlflow_db_exists=$(docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  psql -U "${DB_USER:-anchor}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${MLFLOW_DB_NAME:-anchor_mlflow}'" \
  | tr -d '[:space:]')
if [ "$mlflow_db_exists" != "1" ]; then
  docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
    psql -U "${DB_USER:-anchor}" -d postgres -c "CREATE DATABASE ${MLFLOW_DB_NAME:-anchor_mlflow};"
fi

echo "==> running DB migrations..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm engine alembic upgrade head

echo "==> restarting services..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d \
  engine celery_worker celery_beat watchdog mlflow frontend nginx

echo "==> deployed ${DEPLOY_SHA}"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps \
  engine celery_worker celery_beat watchdog mlflow frontend nginx
