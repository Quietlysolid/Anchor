from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator(
        "instruments",
        "trend_instruments",
        "confirmation_timeframes",
        "expected_pilot_instruments",
        "fix_instruments",
        "nfp_instruments",
        mode="before",
    )
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
    min_confluence_score: float = 0.55  # lowered from 0.72 — backtest max confluence ~0.62, 0.72 was unreachable
    min_ml_confidence: float = 0.58
    max_position_pct: float = 0.05         # 5% hard cap per position notional
    correlation_block_threshold: float = 0.70
    spread_spike_multiplier: float = 3.0   # suppress if spread > 3x session median
    # Trend engine (London confluence, 07:15-12:00 UTC) — PAPER ONLY (2026-03-24).
    # Threshold lowered from 0.72 → 0.55 (backtest max confluence ~0.62; 0.72 was physically
    # unreachable, engine had never fired). Paper trading to accumulate live signal data.
    # Re-enable live only after 50+ paper trades show PF > 1.15 OOS.
    enable_trend_engine: bool = True
    trend_paper_only: bool = True
    trend_risk_pct: float = 0.01
    # London trend research conclusion (2026-03-24):
    # - EUR_JPY is the only survivor of the current pullback-limit trend design
    # - EUR_USD direct-entry rescue produced trades but still failed economically
    # - NZD_USD produced no meaningful signal stream
    # Keep the live/paper trend universe explicit and narrow.
    trend_instruments: list[str] = ["EUR_JPY"]
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
    # London benchmark-fix continuation sleeve (paper only, 2026-03-24).
    # Research validation:
    # - strongest on regular days, not month-end specific
    # - pair ranking: USD_JPY, EUR_USD, GBP_USD
    # This sleeve is wired paper-only until a live execution spec exists for
    # time-based exits and fix-window slippage measurement.
    enable_fix_engine: bool = True
    fix_paper_only: bool = True
    fix_risk_pct: float = 0.005
    fix_instruments: list[str] = ["USD_JPY", "EUR_USD", "GBP_USD"]
    # NFP continuation sleeve (paper only, 2026-03-24).
    # Research validation:
    # - strongest focused cut: immediate continuation, ALIGNED + BIG, 24h hold
    # - first-tier pairs: EUR_USD, GBP_USD, USD_CAD
    # - USD_JPY remains secondary / watchlist but included in paper for evidence
    enable_nfp_engine: bool = True
    nfp_paper_only: bool = True
    nfp_risk_pct: float = 0.005
    nfp_instruments: list[str] = ["EUR_USD", "GBP_USD", "USD_CAD", "USD_JPY"]
    # Hard cap on simultaneous open LCR positions (portfolio heat limiter).
    # At 1% base risk: 3 positions = max 3% gross portfolio heat at once.
    # Correlation scaling may further reduce individual position sizes.
    max_concurrent_lcr_positions: int = 3
    enable_m15_engine: bool = False
    m15_paper_only: bool = True
    m15_risk_pct: float = 0.005
    expected_pilot_instruments: list[str] = ["EUR_USD", "NZD_USD", "EUR_JPY"]
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
        # Reduced unattended-observation set (2026-03-24):
        # - EUR_USD: strongest validated live candidate
        # - NZD_USD: secondary live candidate
        # - EUR_JPY: paper-only observation until 20+ paper trades accrue
        "EUR_USD", "NZD_USD", "EUR_JPY",
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
