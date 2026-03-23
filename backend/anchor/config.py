from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("instruments", "confirmation_timeframes", "expected_pilot_instruments", mode="before")
    @classmethod
    def _split_csv_list(cls, value):
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith("["):
                return value
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

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

    # ── Polygon.io ────────────────────────────────────────────
    polygon_api_key: str = ""

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
    monthly_halt_pct: float = 0.06         # 6% MTD loss → halt for rest of month
    min_confluence_score: float = 0.72  # raised from 0.70 — fewer but higher quality signals
    min_ml_confidence: float = 0.58
    max_position_pct: float = 0.05         # 5% hard cap per position notional
    correlation_block_threshold: float = 0.70
    spread_spike_multiplier: float = 3.0   # suppress if spread > 3x session median
    # Trend engine (London confluence, 07:15-12:00 UTC) is DISABLED.
    # No live-validated edge exists yet. LCR is the only active live strategy (2026-03-23).
    # The HMM RANGING gate and the 0.72 confluence threshold also prevent firing in the
    # current market, but those are runtime conditions — this flag is the explicit hard disable.
    # Re-enable only after OOS London backtest shows PF > 1.15 with the 13-signal engine.
    enable_trend_engine: bool = False
    trend_paper_only: bool = False
    trend_risk_pct: float = 0.01
    # MR (mean-reversion / London BB fade) is DISABLED.
    # Validation (2026-03-23) showed no reliable OOS edge after fixing the SL-direction
    # bug and running a non-leaky HMM regime backtest. 4 of 5 active pairs had OOS PF < 1.0.
    # Code is kept for future research. Re-enable only after a new production-faithful
    # validation shows OOS PF > 1.15 on at least 2-3 pairs with 50+ trades each.
    enable_mr_engine: bool = False
    mr_paper_only: bool = False
    mr_risk_pct: float = 0.01
    enable_lcr_engine: bool = True
    lcr_paper_only: bool = False
    lcr_risk_pct: float = 0.01
    # Hard cap on simultaneous open LCR positions (portfolio heat limiter).
    # At 1% base risk: 3 positions = max 3% gross portfolio heat at once.
    # Correlation scaling may further reduce individual position sizes.
    max_concurrent_lcr_positions: int = 3
    enable_m15_engine: bool = False
    m15_paper_only: bool = True
    m15_risk_pct: float = 0.005
    expected_pilot_instruments: list[str] = ["EUR_USD", "NZD_USD", "AUD_USD", "USD_CAD", "EUR_JPY"]
    expected_pilot_trend_enabled: bool = False  # disabled — see enable_trend_engine comment above
    expected_pilot_trend_paper_only: bool = True
    expected_pilot_trend_risk_pct: float = 0.001
    expected_pilot_mr_enabled: bool = False  # disabled — see enable_mr_engine comment above
    expected_pilot_mr_paper_only: bool = False
    expected_pilot_mr_risk_pct: float = 0.0015
    expected_pilot_lcr_enabled: bool = True
    expected_pilot_lcr_paper_only: bool = False
    expected_pilot_lcr_risk_pct: float = 0.0035
    expected_pilot_m15_enabled: bool = False
    expected_pilot_m15_paper_only: bool = True
    expected_pilot_m15_risk_pct: float = 0.0005

    # ── Anthropic ─────────────────────────────────────────────
    anthropic_api_key: str = ""

    # ── MLflow ────────────────────────────────────────────────
    mlflow_tracking_uri: str = "http://mlflow:5000"

    # ── Instruments ───────────────────────────────────────────
    instruments: list[str] = [
        # Top 6 by LCR backtest (8-year, spread-adjusted, ranked by PF × DD trade-off):
        # NZD_USD: PF 1.697, DD -18.7%, 11.6/mo — #1 overall despite being overlooked
        # EUR_USD: PF 1.669, DD -23.6%, 13.3/mo — highest liquidity
        # USD_CAD: PF 1.540, DD -15.2%, ~11/mo  — best risk-adjusted (lowest DD) — 0.75% risk
        # EUR_JPY: PF 1.511, DD -18.6%, ~12/mo  — beats USD_JPY on every metric — 0.75% risk
        # AUD_USD: PF 1.470, DD ~-20%, ~12/mo   — solid diversification
        # GBP_USD: PF 1.434, DD -20.9%, 13.5/mo — strong London moves
        #
        # Removed:
        #   USD_JPY  — PF 1.367, DD -22.8%, dominated by EUR_JPY in all metrics
        #   USD_CHF  — PF 1.557 but DD -39.7% at 1% risk — disqualified by drawdown
        #   GBP_JPY  — weak edge, ~PF 1.2, wide spread
        #   NZD_USD  — re-added 2026-03-16 after backtest confirmed PF 1.697
        "EUR_USD", "GBP_USD", "NZD_USD", "USD_CAD", "EUR_JPY", "AUD_USD",
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
