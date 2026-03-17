#!/bin/bash
# Wrapper for dev environment
# Examples:
#   ./dev.sh up -d
#   ./dev.sh restart engine
#   ./dev.sh logs engine -f
#   ./dev.sh down
set -e
docker compose -f docker-compose.yml -f docker-compose.dev.yml "$@"
