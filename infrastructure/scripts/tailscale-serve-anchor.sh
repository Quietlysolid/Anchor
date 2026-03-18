#!/bin/sh
set -eu

if ! command -v tailscale >/dev/null 2>&1; then
  echo "tailscale is not installed on this host" >&2
  exit 1
fi

echo "Publishing Anchor privately to your tailnet from http://127.0.0.1:8080 ..."
tailscale serve --bg --http=80 http://127.0.0.1:8080

echo
echo "Current Tailscale Serve status:"
tailscale serve status
