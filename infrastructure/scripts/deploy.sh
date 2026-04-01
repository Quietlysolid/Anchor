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

# Write build_info.py before building images so it gets baked in.
# This is a separate SSH call so it's not inside the set -euo heredoc.
echo "==> Writing build_info.py..."
ssh "$VPS_USER@$VPS_HOST" "cat > $APP_DIR/backend/anchor/build_info.py" << EOF
DEPLOYED_SHA = "${DEPLOY_SHA}"
DEPLOYED_AT = "${DEPLOYED_AT}"
EOF

# Step 1: Build images (fail fast — nothing to deploy if build fails)
echo "==> [1/3] Building images..."
ssh "$VPS_USER@$VPS_HOST" bash << 'BUILD'
  set -euo pipefail
  cd /opt/anchor
  echo "--- Building Python service images (no-cache)..."
  docker compose build --no-cache engine celery_worker celery_beat watchdog mlflow
  echo "--- Building frontend and nginx..."
  docker compose build frontend nginx
  echo "--- Build complete."
  docker compose images
BUILD

# Step 2: Migrate (best-effort — log failures but don't block service restart)
echo "==> [2/3] Running pre-deploy steps..."
ssh "$VPS_USER@$VPS_HOST" bash << 'MIGRATE'
  set -uo pipefail
  cd /opt/anchor

  echo "--- Starting database and redis..."
  docker compose up -d db redis

  echo "--- Waiting for database to be ready (up to 60s)..."
  for i in $(seq 1 30); do
    if docker compose exec -T db pg_isready -U "${DB_USER:-anchor}" -d "${DB_NAME:-anchor}" > /dev/null 2>&1; then
      echo "    db ready (attempt $i)"
      break
    fi
    if [ "$i" -eq 30 ]; then
      echo "    WARNING: database not ready after 60s — skipping migration"
      exit 0
    fi
    sleep 2
  done

  echo "--- Ensuring MLflow database exists..."
  _mlflow_db_exists=$(docker compose exec -T db psql -U "${DB_USER:-anchor}" -d postgres -tc \
    "SELECT 1 FROM pg_database WHERE datname = '${MLFLOW_DB_NAME:-anchor_mlflow}'" \
    2>/dev/null | tr -d '[:space:]') || _mlflow_db_exists=""
  if [ "$_mlflow_db_exists" != "1" ]; then
    docker compose exec -T db psql -U "${DB_USER:-anchor}" -d postgres \
      -c "CREATE DATABASE ${MLFLOW_DB_NAME:-anchor_mlflow};" \
      2>/dev/null || echo "    WARNING: could not create MLflow database"
  fi

  echo "--- Running DB migrations..."
  if docker compose run --rm engine alembic upgrade head; then
    echo "    Migrations OK"
  else
    echo "    First migration attempt failed — clearing alembic_version and retrying..."
    docker compose exec -T db psql -U "${DB_USER:-anchor}" -d "${DB_NAME:-anchor}" \
      -c "DELETE FROM alembic_version;" 2>/dev/null || true
    if docker compose run --rm engine alembic upgrade head; then
      echo "    Migrations OK (after reset)"
    else
      echo "    WARNING: Migration failed on both attempts — deploying anyway, check logs"
    fi
  fi
MIGRATE

# Step 3: Restart services (always runs if build succeeded)
echo "==> [3/3] Starting services..."
ssh "$VPS_USER@$VPS_HOST" bash << 'RESTART'
  set -euo pipefail
  cd /opt/anchor
  docker compose rm -sf engine celery_worker celery_beat watchdog mlflow frontend nginx || true
  docker compose up -d --remove-orphans engine celery_worker celery_beat watchdog mlflow frontend nginx
  echo ""
  docker compose ps
RESTART

echo ""
echo "==> Deploy complete! Dashboard: http://$VPS_HOST:8080/"
