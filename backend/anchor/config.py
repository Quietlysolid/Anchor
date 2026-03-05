from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── App ───────────────────────────────────────────────────
    app_env: str = "development"
    secret_key: str = "change_me"
    log_level: str = "INFO"
    service_name: str = "engine"

    # ── Database ──────────────────────────────────────────────
    db_user: str = "anchor"
    db_password: str = "anchor"
    db_host: str = "db"
    db_port: int = 5432
    db_name: str = "anchor"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def sync_database_url(self) -> str:
        return (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ── Redis ─────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── OANDA ────────────────────────────────────────────────
    oanda_api_key: str = ""
    oanda_account_id: str = ""
    oanda_environment: str = "practice"  # practice | live

    @property
    def oanda_base_url(self) -> str:
        if self.oanda_environment == "live":
            return "https://api-fxtrade.oanda.com"
        return "https://api-fxpractice.oanda.com"

    @property
    def oanda_stream_url(self) -> str:
        if self.oanda_environment == "live":
            return "https://stream-fxtrade.oanda.com"
        return "https://stream-fxpractice.oanda.com"

    # ── FRED ──────────────────────────────────────────────────
    fred_api_key: str = ""

    # ── Alerts ───────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ── Risk Parameters ───────────────────────────────────────
    max_risk_per_trade: float = 0.01       # 1% of account
    daily_loss_limit_pct: float = 0.03     # 3% daily loss limit → halt for the day
    drawdown_reduce_pct: float = 0.08      # 8% → halve position size
    drawdown_halt_pct: float = 0.15        # 15% → halt all trading
    min_confluence_score: float = 0.65
    min_ml_confidence: float = 0.58
    max_position_pct: float = 0.05         # 5% hard cap per position notional
    correlation_block_threshold: float = 0.70
    spread_spike_multiplier: float = 3.0   # suppress if spread > 3x session median

    # ── MLflow ────────────────────────────────────────────────
    mlflow_tracking_uri: str = "http://mlflow:5000"

    # ── Instruments ───────────────────────────────────────────
    instruments: list[str] = [
        "EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD",
    ]

    # ── Timeframes ────────────────────────────────────────────
    signal_timeframe: str = "H1"
    confirmation_timeframes: list[str] = ["H4", "D"]

    # ── Grafana ───────────────────────────────────────────────
    grafana_password: str = "admin"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Module-level alias so `from anchor.config import settings` works
settings: Settings = get_settings()
