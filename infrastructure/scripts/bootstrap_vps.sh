#!/bin/bash
# Anchor VPS Bootstrap — run this ONCE on a fresh Ubuntu 24.04 Hetzner server
# Usage: bash bootstrap_vps.sh
# Run as root (Hetzner default)

set -euo pipefail

echo "==> [1/7] System update..."
apt-get update -qq && apt-get upgrade -y -qq

echo "==> [2/7] Install dependencies..."
apt-get install -y -qq \
  curl git ufw fail2ban unattended-upgrades \
  ca-certificates gnupg lsb-release

echo "==> [3/7] Install Docker..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
  > /etc/apt/sources.list.d/docker.list

apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

systemctl enable docker
systemctl start docker

echo "==> [4/7] Firewall (UFW)..."
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "==> [5/7] Fail2ban..."
systemctl enable fail2ban
systemctl start fail2ban

echo "==> [6/7] Create app directory..."
mkdir -p /opt/anchor
chmod 755 /opt/anchor

echo "==> [7/7] Unattended security upgrades..."
dpkg-reconfigure -plow unattended-upgrades

echo ""
echo "========================================"
echo "  Bootstrap complete!"
echo "  Next: rsync your code and run deploy"
echo "========================================"
docker --version
docker compose version
