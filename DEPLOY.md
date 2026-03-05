# Anchor — Hetzner VPS Deployment Guide

## Step 1: Provision Hetzner Server

1. Go to https://console.hetzner.com
2. New Project → `anchor`
3. Add Server:
   - **Location**: Nuremberg (EU) — closest to OANDA
   - **Image**: Ubuntu 24.04
   - **Type**: CX22 (2 vCPU, 4GB RAM, ~€4.15/mo)
   - **SSH Key**: paste your `~/.ssh/id_ed25519.pub`
   - **Name**: `anchor-vps`
4. Note the public IP (e.g. `5.161.x.x`)

---

## Step 2: Bootstrap the Server (run once)

From your Windows machine (Git Bash / WSL):

```bash
# Replace with your actual VPS IP
VPS_IP=5.161.x.x

# Copy and run bootstrap script
scp infrastructure/scripts/bootstrap_vps.sh root@$VPS_IP:/tmp/
ssh root@$VPS_IP "bash /tmp/bootstrap_vps.sh"
```

---

## Step 3: Create .env on the Server

```bash
ssh root@$VPS_IP

# Create the app directory
mkdir -p /opt/anchor

# Create .env (fill in real values)
cat > /opt/anchor/.env << 'EOF'
DB_USER=anchor
DB_PASSWORD=CHANGE_ME_STRONG_PASSWORD_HERE
DB_HOST=db
DB_PORT=5432
DB_NAME=anchor
DATABASE_URL=postgresql+asyncpg://anchor:CHANGE_ME_STRONG_PASSWORD_HERE@db:5432/anchor

REDIS_URL=redis://redis:6379/0

OANDA_API_KEY=your_oanda_api_key_here
OANDA_ACCOUNT_ID=your_oanda_account_id_here
OANDA_ENVIRONMENT=practice

FRED_API_KEY=your_fred_api_key_here

TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id

APP_ENV=production
SECRET_KEY=CHANGE_ME_64_CHAR_RANDOM_STRING_HERE
LOG_LEVEL=INFO

GRAFANA_PASSWORD=CHANGE_ME_GRAFANA_PASSWORD

MAX_RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.03
DRAWDOWN_REDUCE_PCT=0.08
DRAWDOWN_HALT_PCT=0.15
MIN_CONFLUENCE_SCORE=0.65
MIN_ML_CONFIDENCE=0.58
EOF
```

Generate a strong SECRET_KEY:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## Step 4: Deploy Code

From your Windows machine (Git Bash / WSL), from the project root:

```bash
VPS_IP=5.161.x.x
VPS_HOST=$VPS_IP bash infrastructure/scripts/deploy.sh
```

The deploy script will:
- rsync all code to `/opt/anchor/` on the server
- Run `docker compose up -d`
- Run DB migrations

---

## Step 5: Verify

```bash
ssh root@$VPS_IP "cd /opt/anchor && docker compose ps"
```

All services should show `running`. Then open:
- Dashboard: `http://YOUR_VPS_IP/`
- API health: `http://YOUR_VPS_IP/api/v1/system/health`
- Grafana: `http://YOUR_VPS_IP:3001/` (internally exposed — use SSH tunnel)

For Grafana via SSH tunnel:
```bash
ssh -L 3001:localhost:3001 root@$VPS_IP
# then open http://localhost:3001
```

---

## Step 6: (Optional) Add SSL with Let's Encrypt

Only worth doing if you have a domain. If using IP only, skip this.

```bash
ssh root@$VPS_IP
apt install certbot -y
certbot certonly --standalone -d your.domain.com
```

Then uncomment the HTTPS server block in `infrastructure/nginx/nginx.conf` and
uncomment the letsencrypt volume in `docker-compose.yml`, then redeploy.

---

## After Deployment

1. **Verify OANDA connection**: `GET /api/v1/system/health` should show broker connected
2. **Import historical data**: `docker compose exec engine python -m anchor.data.importer`
3. **Run backtest**: Use the dashboard or `POST /api/v1/backtest`
4. **Monitor logs**: `docker compose logs -f engine`

---

## Useful Commands on Server

```bash
cd /opt/anchor

# View all service logs
docker compose logs -f

# View just trading engine logs
docker compose logs -f engine

# Restart a single service
docker compose restart engine

# Full restart
docker compose down && docker compose up -d

# Database shell
docker compose exec db psql -U anchor anchor

# Redis CLI
docker compose exec redis redis-cli
```
