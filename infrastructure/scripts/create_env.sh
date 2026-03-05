#!/bin/bash
SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
cat > /opt/anchor/.env << EOF
DB_USER=anchor
DB_PASSWORD=Anch0r_DB_2024!
DB_HOST=db
DB_PORT=5432
DB_NAME=anchor
DATABASE_URL=postgresql+asyncpg://anchor:Anch0r_DB_2024!@db:5432/anchor
REDIS_URL=redis://redis:6379/0
OANDA_API_KEY=your_oanda_api_key_here
OANDA_ACCOUNT_ID=your_oanda_account_id_here
OANDA_ENVIRONMENT=practice
FRED_API_KEY=your_fred_api_key_here
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
APP_ENV=production
SECRET_KEY=${SECRET}
LOG_LEVEL=INFO
GRAFANA_PASSWORD=Anch0r_Graf_2024!
MAX_RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.03
DRAWDOWN_REDUCE_PCT=0.08
DRAWDOWN_HALT_PCT=0.15
MIN_CONFLUENCE_SCORE=0.65
MIN_ML_CONFIDENCE=0.58
EOF
echo "Done. Contents:"
cat /opt/anchor/.env
