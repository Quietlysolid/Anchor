#!/bin/bash
# Deploy Anchor to Hetzner VPS
# Usage: VPS_HOST=1.2.3.4 bash infrastructure/scripts/deploy.sh

set -euo pipefail

VPS_USER="${VPS_USER:-root}"
VPS_HOST="${VPS_HOST:?Set VPS_HOST environment variable}"
APP_DIR="/opt/anchor"
DEPLOY_SHA="${DEPLOY_SHA:-$(git rev-parse HEAD)}"
DEPLOYED_AT="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "==> Deploying SHA ${DEPLOY_SHA:0:12} to $VPS_HOST..."

# Ensure remote app directory exists
ssh "$VPS_USER@$VPS_HOST" "mkdir -p $APP_DIR"

# Sync code using git archive piped over ssh — no rsync required.
# Sends only tracked files; never touches .env on the server.
echo "==> Syncing code..."
git archive HEAD | ssh "$VPS_USER@$VPS_HOST" "tar -x -C $APP_DIR"

# Write build_info.py before building images so it gets baked in
echo "==> Writing build_info.py..."
ssh "$VPS_USER@$VPS_HOST" "cat > $APP_DIR/backend/anchor/build_info.py" << EOF
DEPLOYED_SHA = "${DEPLOY_SHA}"
DEPLOYED_AT = "${DEPLOYED_AT}"
EOF

# Remote commands — split into discrete steps so failure is clearly attributed
ssh "$VPS_USER@$VPS_HOST" bash << 'REMOTE'
  set -euo pipefail
  cd /opt/anchor

  echo "--- [1/6] Building Python service images..."
  docker compose build --no-cache engine celery_worker celery_beat watchdog mlflow

  echo "--- [2/6] Building frontend and nginx..."
  docker compose build frontend nginx

  echo "--- [3/6] Starting database..."
  docker compose up -d db redis

  echo "--- [4/6] Waiting for database to be ready..."
  for i in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U "${DB_USER:-anchor}" -d "${DB_NAME:-anchor}" > /dev/null 2>&1; then
      echo "    db ready after ${i}x2s"
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo "    ERROR: database did not become ready in 60s"
      exit 1
    fi
    sleep 2
  done

  echo "--- [5/6] Ensuring MLflow database exists..."
  _mlflow_db_exists=$(docker compose exec -T db psql -U "${DB_USER:-anchor}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '${MLFLOW_DB_NAME:-anchor_mlflow}'" | tr -d '[:space:]')
  if [ "$_mlflow_db_exists" != "1" ]; then
    docker compose exec -T db psql -U "${DB_USER:-anchor}" -d postgres -c "CREATE DATABASE ${MLFLOW_DB_NAME:-anchor_mlflow};"
  fi

  echo "--- [5b/6] Running DB migrations..."
  if ! docker compose run --rm engine alembic upgrade head; then
    echo "    Migration failed on first attempt — clearing alembic_version and retrying..."
    docker compose exec -T db psql -U "${DB_USER:-anchor}" -d "${DB_NAME:-anchor}" -c "DELETE FROM alembic_version;"
    if ! docker compose run --rm engine alembic upgrade head; then
      echo "    ERROR: Migration failed on both attempts. Aborting to protect database integrity."
      exit 1
    fi
  fi

  echo "--- [6/6] Starting all services..."
  docker compose rm -sf engine celery_worker celery_beat watchdog mlflow frontend nginx || true
  docker compose up -d --remove-orphans engine celery_worker celery_beat watchdog mlflow frontend nginx

  echo ""
  echo "==> Services:"
  docker compose ps
REMOTE

echo ""
echo "==> Deploy complete! Dashboard: http://$VPS_HOST:8080/"
