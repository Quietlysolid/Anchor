from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_THIS_FILE = Path(__file__).resolve()
_ENV_CANDIDATES = [
    Path.cwd() / ".env",
    _THIS_FILE.parents[2] / ".env",
]
if len(_THIS_FILE.parents) > 3:
    _ENV_CANDIDATES.append(_THIS_FILE.parents[3] / ".env")
_ENV_FILE = next((path for path in _ENV_CANDIDATES if path.exists()), _ENV_CANDIDATES[0])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(_ENV_FILE), extra="ignore", enable_decoding=False)

    @field_validator(
        "instruments",
        "trend_instruments",
        "confirmation_timeframes",
        "expected_pilot_instruments",
        "fix_instruments",
        "nfp_instruments",
        "futures_v1_markets",
        "futures_v1_candidate_markets",
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

    # ── Broker ───────────────────────────────────────────────
    broker_provider: str = "ibkr"
    trading_domain: str = "futures"

    # ── IBKR ─────────────────────────────────────────────────
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = 4002
    ibkr_client_id: int = 1
    ibkr_paper_account_id: str = ""
    ibkr_paper_username: str = ""
    ibkr_account_id: str = ""
    ibkr_account_alias: str = ""
    ibkr_exchange_forex: str = "IDEALPRO"
    ibkr_exchange_crypto: str = "PAXOS"
    ibkr_default_futures_exchange: str = "CME"
    ibkr_market_data_type: int = 1  # 1=live, 2=frozen, 3=delayed, 4=delayed-frozen
    ibkr_read_only_api: bool = False

    @property
    def account_mode(self) -> str:
        return "paper" if self.ibkr_port in {4002, 4004, 7497, 7499} else "live"

    @property
    def account_environment(self) -> str:
        if self.ibkr_account_alias:
            return self.ibkr_account_alias
        return f"{self.ibkr_host}:{self.ibkr_port}"

    @property
    def broker_account_id(self) -> str:
        return self.ibkr_account_id or self.ibkr_paper_account_id

    # ── Polygon.io ────────────────────────────────────────────
    polygon_api_key: str = ""

    # ── FRED ──────────────────────────────────────────────────
    fred_api_key: str = ""

    # ── Alerts ───────────────────────────────────────────────
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    watchdog_health_endpoint_url: str = "http://engine:8000/api/v1/system/health"
    watchdog_heartbeat_stale_seconds: int = 180
    watchdog_broker_sync_stale_seconds: int = 120
    watchdog_broker_sync_critical_seconds: int = 300
    watchdog_check_interval_seconds: int = 120
    watchdog_alert_cooldown_seconds: int = 1800
    watchdog_recent_event_lookback_minutes: int = 15
    watchdog_warn_on_delayed_market_data: bool = True

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
    enable_trend_engine: bool = False
    trend_paper_only: bool = True
    trend_risk_pct: float = 0.01
    # London trend research conclusion (2026-03-24):
    # - EUR_JPY is the only survivor of the current pullback-limit trend design
    # - EUR_USD direct-entry rescue produced trades but still failed economically
    # - NZD_USD produced no meaningful signal stream
    # Keep the live/paper trend universe explicit and narrow.
    trend_instruments: Annotated[list[str], NoDecode] = []
    # MR (mean-reversion / London BB fade) is DISABLED.
    # Validation (2026-03-23) showed no reliable OOS edge after fixing the SL-direction
    # bug and running a non-leaky HMM regime backtest. 4 of 5 active pairs had OOS PF < 1.0.
    # Code is kept for future research. Re-enable only after a new production-faithful
    # validation shows OOS PF > 1.15 on at least 2-3 pairs with 50+ trades each.
    enable_mr_engine: bool = False
    mr_paper_only: bool = False
    mr_risk_pct: float = 0.01
    enable_lcr_engine: bool = False
    lcr_paper_only: bool = False
    lcr_risk_pct: float = 0.01
    # London benchmark-fix continuation sleeve (paper only, 2026-03-24).
    # Research validation:
    # - strongest on regular days, not month-end specific
    # - pair ranking: USD_JPY, EUR_USD, GBP_USD
    # This sleeve is wired paper-only until a live execution spec exists for
    # time-based exits and fix-window slippage measurement.
    enable_fix_engine: bool = False
    fix_paper_only: bool = True
    fix_risk_pct: float = 0.005
    fix_instruments: Annotated[list[str], NoDecode] = []
    # NFP continuation sleeve (paper only, 2026-03-24).
    # Research validation:
    # - strongest focused cut: immediate continuation, ALIGNED + BIG, 24h hold
    # - first-tier pairs: EUR_USD, GBP_USD, USD_CAD
    # - USD_JPY remains secondary / watchlist but included in paper for evidence
    enable_nfp_engine: bool = False
    nfp_paper_only: bool = True
    nfp_risk_pct: float = 0.005
    nfp_instruments: Annotated[list[str], NoDecode] = []
    # Hard cap on simultaneous open LCR positions (portfolio heat limiter).
    # At 1% base risk: 3 positions = max 3% gross portfolio heat at once.
    # Correlation scaling may further reduce individual position sizes.
    max_concurrent_lcr_positions: int = 3
    enable_m15_engine: bool = False
    m15_paper_only: bool = True
    m15_risk_pct: float = 0.005
    expected_pilot_instruments: Annotated[list[str], NoDecode] = ["EUR_USD", "NZD_USD", "EUR_JPY"]
    expected_pilot_trend_enabled: bool = False  # disabled — see enable_trend_engine comment above
    expected_pilot_trend_paper_only: bool = True
    expected_pilot_trend_risk_pct: float = 0.001
    expected_pilot_mr_enabled: bool = False  # disabled — see enable_mr_engine comment above
    expected_pilot_mr_paper_only: bool = False
    expected_pilot_mr_risk_pct: float = 0.0015
    expected_pilot_lcr_enabled: bool = False
    expected_pilot_lcr_paper_only: bool = False
    expected_pilot_lcr_risk_pct: float = 0.0035
    expected_pilot_m15_enabled: bool = False
    expected_pilot_m15_paper_only: bool = True
    expected_pilot_m15_risk_pct: float = 0.0005

    # ── Futures v1 ───────────────────────────────────────────
    futures_v1_enabled: bool = True
    futures_v1_paper_only: bool = True
    futures_v1_auto_execute: bool = True
    futures_v1_strategy: str = "trend"
    futures_v1_markets: Annotated[list[str], NoDecode] = ["MNQ", "ZN", "MGC", "MCL"]
    futures_v1_candidate_markets: Annotated[list[str], NoDecode] = ["MNQ", "ZN", "MGC", "MCL"]
    futures_v1_trend_lookback_days: int = 63
    futures_rebalance_frequency: str = "weekly"
    futures_signal_threshold: float = 0.01
    futures_vol_lookback_days: int = 20
    futures_roll_days_before_expiry: int = 7
    futures_target_volatility: float = 0.10
    futures_target_gross_exposure: float = 2.50
    futures_max_margin_usage_pct: float = 0.35
    futures_max_contracts_per_market: int = 2
    futures_daily_signal_time_utc: str = "22:15"
    futures_transaction_cost_bps: float = 1.0
    futures_roll_cost_bps: float = 2.0
    futures_margin_warn_usage_pct: float = 0.45
    futures_margin_block_new_opens_pct: float = 0.55
    futures_margin_force_derisk_usage_pct: float = 0.65
    futures_weekend_guard_enabled: bool = True
    futures_readiness_min_track_record_days: int = 30
    futures_readiness_min_rebalances: int = 8
    futures_readiness_max_drawdown_pct: float = 10.0
    futures_readiness_min_since_start_return_pct: float = 0.0
    futures_readiness_max_margin_incidents_30d: int = 0
    futures_readiness_max_operational_incidents_30d: int = 0

    # ── Anthropic ─────────────────────────────────────────────
    anthropic_api_key: str = ""

    # ── MLflow ────────────────────────────────────────────────
    mlflow_tracking_uri: str = "http://mlflow:5000"

    # ── Instruments ───────────────────────────────────────────
    instruments: Annotated[list[str], NoDecode] = ["MNQ", "ZN", "MGC", "MCL"]

    # ── Timeframes ────────────────────────────────────────────
    signal_timeframe: str = "H1"
    confirmation_timeframes: Annotated[list[str], NoDecode] = ["H4", "D"]

    # ── Grafana ───────────────────────────────────────────────
    grafana_password: str = "admin"


@lru_cache
def get_settings() -> Settings:
    return Settings()


# Module-level alias so `from anchor.config import settings` works
settings: Settings = get_settings()
