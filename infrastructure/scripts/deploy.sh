#!/bin/bash
# Deploy Anchor to Hetzner VPS
# Usage: VPS_HOST=1.2.3.4 bash infrastructure/scripts/deploy.sh

set -euo pipefail

VPS_USER="${VPS_USER:-root}"
VPS_HOST="${VPS_HOST:?Set VPS_HOST environment variable}"
APP_DIR="/opt/anchor"

echo "==> Deploying to $VPS_HOST..."

# Sync code — exclude .env so we never overwrite the server's secrets
rsync -avz \
  --exclude='.git' \
  --exclude='node_modules' \
  --exclude='mlflow_artifacts' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  ./ "$VPS_USER@$VPS_HOST:$APP_DIR/"

# Remote commands
ssh "$VPS_USER@$VPS_HOST" bash << EOF
  set -euo pipefail
  cd $APP_DIR

  echo "==> Building images..."
  docker compose build --parallel

  echo "==> Running DB migrations..."
  docker compose run --rm engine alembic upgrade head

  echo "==> Starting services..."
  docker compose up -d --remove-orphans

  echo ""
  echo "==> Services:"
  docker compose ps
EOF

echo ""
echo "==> Deploy complete! Dashboard: http://$VPS_HOST/"
