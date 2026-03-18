#!/bin/bash
# Deploy latest code to prod (baked images, no volume mounts)
# Usage: ./deploy.sh
set -e

echo "==> pulling latest code..."
git pull

echo "==> building images ($(git rev-parse --short HEAD))..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml build \
  engine celery_worker celery_beat watchdog mlflow frontend nginx

echo "==> starting database..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d db

echo "==> ensuring MLflow database exists..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  psql -U "${DB_USER:-anchor}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${MLFLOW_DB_NAME:-anchor_mlflow}'" \
  | grep -q 1 || \
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T db \
  psql -U "${DB_USER:-anchor}" -d postgres -c "CREATE DATABASE ${MLFLOW_DB_NAME:-anchor_mlflow};"

echo "==> restarting services..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d \
  engine celery_worker celery_beat watchdog mlflow frontend nginx

echo "==> deployed $(git rev-parse --short HEAD)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps \
  engine celery_worker celery_beat watchdog mlflow frontend nginx
