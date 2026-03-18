#!/bin/bash
# Deploy Anchor to Hetzner VPS
# Usage: VPS_HOST=1.2.3.4 bash infrastructure/scripts/deploy.sh

set -euo pipefail

VPS_USER="${VPS_USER:-root}"
VPS_HOST="${VPS_HOST:?Set VPS_HOST environment variable}"
APP_DIR="/opt/anchor"

echo "==> Deploying to $VPS_HOST..."

# Ensure remote app directory exists
ssh "$VPS_USER@$VPS_HOST" "mkdir -p $APP_DIR"

# Sync code using git archive piped over ssh — no rsync required.
# Sends only tracked files; never touches .env on the server.
echo "==> Syncing code..."
git archive HEAD | ssh "$VPS_USER@$VPS_HOST" "tar -x -C $APP_DIR"

# Remote commands
ssh "$VPS_USER@$VPS_HOST" bash << EOF
  set -euo pipefail
  cd $APP_DIR

  echo "==> Building images..."
  # --no-cache on Python services ensures the COPY . . layer is never stale
  # after a code sync. Frontend/nginx are cache-friendly (rarely change).
  docker compose build --parallel --no-cache engine celery_worker celery_beat watchdog mlflow
  docker compose build --parallel frontend nginx

  echo "==> Starting database..."
  docker compose up -d db

  echo "==> Ensuring MLflow database exists..."
  docker compose exec -T db psql -U "\${DB_USER:-anchor}" -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '\${MLFLOW_DB_NAME:-anchor_mlflow}'" | grep -q 1 \
    || docker compose exec -T db psql -U "\${DB_USER:-anchor}" -d postgres -c "CREATE DATABASE \${MLFLOW_DB_NAME:-anchor_mlflow};"

  echo "==> Running DB migrations..."
  # Attempt upgrade; if the DB has a stale revision stamp (e.g. after a
  # volume wipe on a redeploy), clear alembic_version and re-stamp to head.
  if ! docker compose run --rm engine alembic upgrade head; then
    echo "==> Stale revision — clearing alembic_version and re-stamping..."
    docker compose exec -T db psql -U "\${DB_USER:-anchor}" -d "\${DB_NAME:-anchor}" -c "DELETE FROM alembic_version;"
    docker compose run --rm engine alembic stamp head
  fi

  echo "==> Starting services..."
  docker compose up -d --remove-orphans

  echo ""
  echo "==> Services:"
  docker compose ps
EOF

echo ""
echo "==> Deploy complete! Dashboard: http://$VPS_HOST/"
