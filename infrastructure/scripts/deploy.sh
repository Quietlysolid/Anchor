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
  docker compose build --parallel

  echo "==> Running DB migrations..."
  # Attempt upgrade; if the DB has a stale revision stamp (e.g. after a
  # volume wipe on a redeploy), clear alembic_version and re-stamp to head.
  if ! docker compose run --rm engine alembic upgrade head; then
    echo "==> Stale revision — clearing alembic_version and re-stamping..."
    docker compose exec -T db psql -U anchor -d anchor -c "DELETE FROM alembic_version;"
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
