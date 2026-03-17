#!/bin/bash
# Deploy latest code to prod (baked images, no volume mounts)
# Usage: ./deploy.sh
set -e

echo "==> pulling latest code..."
git pull

echo "==> building images ($(git rev-parse --short HEAD))..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml build \
  engine celery_worker celery_beat watchdog frontend

echo "==> restarting services..."
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d \
  engine celery_worker celery_beat watchdog frontend

echo "==> deployed $(git rev-parse --short HEAD)"
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps \
  engine celery_worker celery_beat watchdog
