-- ============================================================
-- Anchor — Database Schema
-- PostgreSQL 16 + TimescaleDB 2.x
-- ============================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- MARKET DATA (Hypertable, partitioned monthly)
-- ============================================================
CREATE TABLE IF NOT EXISTS market_data (
    id           BIGSERIAL,
    time         TIMESTAMPTZ   NOT NULL,
    instrument   VARCHAR(12)   NOT NULL,
    timeframe    VARCHAR(4)    NOT NULL,   -- '1m','5m','15m','H1','H4','D'
    open         NUMERIC(18,6) NOT NULL,
    high         NUMERIC(18,6) NOT NULL,
    low          NUMERIC(18,6) NOT NULL,
    close        NUMERIC(18,6) NOT NULL,
    volume       INTEGER,
    spread_avg   NUMERIC(10,5),
    source       VARCHAR(20)   NOT NULL DEFAULT 'ibkr'
);

SELECT create_hypertable('market_data', 'time',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS ix_md_instrument_timeframe_time
    ON market_data (instrument, timeframe, time DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_md_time_inst_tf
    ON market_data (time, instrument, timeframe);

ALTER TABLE market_data SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument,timeframe'
);
SELECT add_compression_policy('market_data', INTERVAL '7 days', if_not_exists => TRUE);

-- ============================================================
-- TICK DATA (Hypertable, partitioned daily)
-- ============================================================
CREATE TABLE IF NOT EXISTS tick_data (
    time         TIMESTAMPTZ   NOT NULL,
    instrument   VARCHAR(12)   NOT NULL,
    bid          NUMERIC(18,6) NOT NULL,
    ask          NUMERIC(18,6) NOT NULL,
    source       VARCHAR(20)   NOT NULL DEFAULT 'ibkr'
);

SELECT create_hypertable('tick_data', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);
CREATE INDEX IF NOT EXISTS ix_tick_instrument_time
    ON tick_data (instrument, time DESC);

-- ============================================================
-- SIGNALS (Append-only, immutable)
-- ============================================================
CREATE TABLE IF NOT EXISTS signals (
    id                UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    instrument        VARCHAR(12)  NOT NULL,
    timeframe         VARCHAR(4)   NOT NULL DEFAULT 'H1',
    direction         VARCHAR(5)   NOT NULL CHECK (direction IN ('LONG','SHORT')),
    confluence_score  NUMERIC(5,4) NOT NULL,
    rsi_score         NUMERIC(5,4),
    bb_kc_score       NUMERIC(5,4),
    adx_score         NUMERIC(5,4),
    sr_score          NUMERIC(5,4),
    mtf_score         NUMERIC(5,4),
    csi_score         NUMERIC(5,4),
    ml_confidence     NUMERIC(5,4),
    regime_state      VARCHAR(12),
    session           VARCHAR(8),
    suppressed        BOOLEAN      NOT NULL DEFAULT FALSE,
    suppression_reason TEXT,
    signal_metadata   JSONB
);

CREATE INDEX IF NOT EXISTS ix_signals_instrument_created
    ON signals (instrument, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_signals_active
    ON signals (created_at DESC) WHERE suppressed = FALSE;

-- ============================================================
-- ORDER STATE ENUM
-- ============================================================
DO $$ BEGIN
    CREATE TYPE order_state AS ENUM (
        'PENDING','SUBMITTED','ACKNOWLEDGED','PARTIAL',
        'FILLED','CANCELLED','REJECTED','EXPIRED'
    );
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================
-- ORDERS (Append-only, state progression via order_events)
-- ============================================================
CREATE TABLE IF NOT EXISTS orders (
    id                     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    signal_id              UUID         REFERENCES signals(id),
    instrument             VARCHAR(12)  NOT NULL,
    direction              VARCHAR(5)   NOT NULL CHECK (direction IN ('LONG','SHORT')),
    order_type             VARCHAR(12)  NOT NULL DEFAULT 'MARKET',
    requested_units        NUMERIC(18,2) NOT NULL,
    state                  order_state  NOT NULL DEFAULT 'PENDING',
    broker_order_id         VARCHAR(64),
    limit_price            NUMERIC(18,6),
    stop_price             NUMERIC(18,6),
    take_profit            NUMERIC(18,6),
    stop_loss              NUMERIC(18,6),
    trailing_stop_distance NUMERIC(18,6),
    submitted_at           TIMESTAMPTZ,
    acknowledged_at        TIMESTAMPTZ,
    filled_at              TIMESTAMPTZ,
    cancelled_at           TIMESTAMPTZ,
    reject_reason          TEXT,
    metadata               JSONB
);

CREATE INDEX IF NOT EXISTS ix_orders_instrument_state
    ON orders (instrument, state);
CREATE INDEX IF NOT EXISTS ix_orders_created
    ON orders (created_at DESC);

-- ============================================================
-- ORDER EVENTS (Full audit trail, append-only)
-- ============================================================
CREATE TABLE IF NOT EXISTS order_events (
    id           BIGSERIAL    PRIMARY KEY,
    order_id     UUID         NOT NULL REFERENCES orders(id),
    event_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    from_state   order_state,
    to_state     order_state  NOT NULL,
    event_data   JSONB
);

CREATE INDEX IF NOT EXISTS ix_order_events_order_id
    ON order_events (order_id, event_at DESC);

-- ============================================================
-- FILLS (Hypertable, partitioned monthly)
-- ============================================================
CREATE TABLE IF NOT EXISTS fills (
    id              UUID         NOT NULL DEFAULT uuid_generate_v4(),
    order_id        UUID         NOT NULL REFERENCES orders(id),
    fill_at         TIMESTAMPTZ  NOT NULL,
    instrument      VARCHAR(12)  NOT NULL,
    units_filled    NUMERIC(18,2) NOT NULL,
    fill_price      NUMERIC(18,6) NOT NULL,
    expected_price  NUMERIC(18,6),
    slippage_pips   NUMERIC(10,4),
    spread_at_fill  NUMERIC(10,5),
    commission      NUMERIC(18,6) DEFAULT 0,
    pl_realized     NUMERIC(18,6),
    pl_currency     VARCHAR(3)   NOT NULL DEFAULT 'USD',
    broker_fill_id   VARCHAR(64),
    metadata        JSONB
);

SELECT create_hypertable('fills', 'fill_at',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists => TRUE
);
ALTER TABLE fills ADD CONSTRAINT fills_pkey PRIMARY KEY (id, fill_at);

-- ============================================================
-- POSITIONS (Current open + closed positions)
-- ============================================================
CREATE TABLE IF NOT EXISTS positions (
    id                     UUID         PRIMARY KEY DEFAULT uuid_generate_v4(),
    opened_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    closed_at              TIMESTAMPTZ,
    instrument             VARCHAR(12)  NOT NULL,
    direction              VARCHAR(5)   NOT NULL,
    units                  NUMERIC(18,2) NOT NULL,
    avg_entry_price        NUMERIC(18,6) NOT NULL,
    current_price          NUMERIC(18,6),
    unrealized_pl          NUMERIC(18,6),
    realized_pl            NUMERIC(18,6) DEFAULT 0,
    stop_loss              NUMERIC(18,6),
    take_profit            NUMERIC(18,6),
    trailing_stop_distance NUMERIC(18,6),
    broker_trade_id         VARCHAR(64)  UNIQUE,
    status                 VARCHAR(8)   NOT NULL DEFAULT 'OPEN'
                               CHECK (status IN ('OPEN','CLOSED')),
    signal_id              UUID         REFERENCES signals(id)
);

CREATE INDEX IF NOT EXISTS ix_positions_open
    ON positions (instrument) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS ix_positions_opened_at
    ON positions (opened_at DESC);

-- ============================================================
-- TRADES (Immutable closed trade record)
-- ============================================================
CREATE TABLE IF NOT EXISTS trades (
    id                       UUID         NOT NULL DEFAULT uuid_generate_v4(),
    position_id              UUID         NOT NULL REFERENCES positions(id),
    instrument               VARCHAR(12)  NOT NULL,
    direction                VARCHAR(5)   NOT NULL,
    units                    NUMERIC(18,2) NOT NULL,
    entry_price              NUMERIC(18,6) NOT NULL,
    exit_price               NUMERIC(18,6) NOT NULL,
    opened_at                TIMESTAMPTZ  NOT NULL,
    closed_at                TIMESTAMPTZ  NOT NULL,
    duration_minutes         INTEGER,
    gross_pl                 NUMERIC(18,6) NOT NULL,
    commission               NUMERIC(18,6) DEFAULT 0,
    net_pl                   NUMERIC(18,6) NOT NULL,
    pl_currency              VARCHAR(3)   NOT NULL DEFAULT 'USD',
    max_adverse_excursion    NUMERIC(18,6),
    max_favorable_excursion  NUMERIC(18,6),
    close_reason             VARCHAR(32),
    regime_at_entry          VARCHAR(12),
    session_at_entry         VARCHAR(8),
    signal_id                UUID         REFERENCES signals(id)
);

SELECT create_hypertable('trades', 'closed_at',
    chunk_time_interval => INTERVAL '1 month',
    if_not_exists => TRUE
);
ALTER TABLE trades ADD CONSTRAINT trades_pkey PRIMARY KEY (id, closed_at);
CREATE INDEX IF NOT EXISTS ix_trades_instrument_closed
    ON trades (instrument, closed_at DESC);

-- ============================================================
-- EQUITY CURVE (Hypertable, written every 15 minutes)
-- ============================================================
CREATE TABLE IF NOT EXISTS equity_curve (
    time                TIMESTAMPTZ  NOT NULL,
    account_balance     NUMERIC(18,6) NOT NULL,
    account_equity      NUMERIC(18,6) NOT NULL,
    unrealized_pl       NUMERIC(18,6) NOT NULL DEFAULT 0,
    open_position_count INTEGER      DEFAULT 0,
    daily_pl            NUMERIC(18,6),
    peak_equity         NUMERIC(18,6),
    drawdown_pct        NUMERIC(8,4)
);

SELECT create_hypertable('equity_curve', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ============================================================
-- SYSTEM EVENTS (Heartbeat, errors, state changes)
-- ============================================================
CREATE TABLE IF NOT EXISTS system_events (
    id           BIGSERIAL    NOT NULL,
    event_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    event_type   VARCHAR(32)  NOT NULL,
    severity     VARCHAR(8)   NOT NULL CHECK (severity IN ('INFO','WARN','ERROR','CRITICAL')),
    component    VARCHAR(32)  NOT NULL DEFAULT 'SYSTEM',
    message      TEXT,
    metadata     JSONB
);

SELECT create_hypertable('system_events', 'event_at',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);
ALTER TABLE system_events ADD CONSTRAINT system_events_pkey PRIMARY KEY (id, event_at);
CREATE INDEX IF NOT EXISTS ix_sysevents_type_time
    ON system_events (event_type, event_at DESC);

-- ============================================================
-- REGIME HISTORY
-- ============================================================
CREATE TABLE IF NOT EXISTS regime_history (
    time             TIMESTAMPTZ  NOT NULL,
    instrument       VARCHAR(12),
    regime           VARCHAR(12)  NOT NULL,
    confidence       NUMERIC(5,4),
    transition_from  VARCHAR(12)
);

SELECT create_hypertable('regime_history', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- ============================================================
-- SLIPPAGE RECORDS
-- ============================================================
CREATE TABLE IF NOT EXISTS slippage_records (
    id              BIGSERIAL    PRIMARY KEY,
    recorded_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    instrument      VARCHAR(12)  NOT NULL,
    session         VARCHAR(8)   NOT NULL,
    lot_size        NUMERIC(18,2) NOT NULL,
    expected_price  NUMERIC(18,6) NOT NULL,
    fill_price      NUMERIC(18,6) NOT NULL,
    slippage_pips   NUMERIC(10,4) NOT NULL
);

