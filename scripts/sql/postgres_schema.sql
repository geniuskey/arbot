-- ArBot PostgreSQL Schema
-- Requires PostgreSQL 16+

-- 거래소 설정
CREATE TABLE IF NOT EXISTS exchanges (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) UNIQUE NOT NULL,
    tier            SMALLINT NOT NULL DEFAULT 2,  -- 1, 2, 3
    is_active       BOOLEAN NOT NULL DEFAULT true,
    maker_fee_pct   DECIMAL(6,4) NOT NULL,
    taker_fee_pct   DECIMAL(6,4) NOT NULL,
    config_json     JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- 거래 기록
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       UUID NOT NULL,
    exchange        VARCHAR(50) NOT NULL,
    symbol          VARCHAR(20) NOT NULL,
    side            VARCHAR(4) NOT NULL,          -- BUY / SELL
    order_type      VARCHAR(10) NOT NULL,         -- LIMIT / MARKET / IOC
    requested_qty   DECIMAL(20,8) NOT NULL,
    filled_qty      DECIMAL(20,8),
    requested_price DECIMAL(20,8),
    filled_price    DECIMAL(20,8),
    fee             DECIMAL(20,8),
    fee_asset       VARCHAR(10),
    status          VARCHAR(20) NOT NULL,          -- PENDING / FILLED / PARTIAL / CANCELLED / FAILED
    execution_mode  VARCHAR(10) NOT NULL,          -- BACKTEST / PAPER / LIVE
    latency_ms      DECIMAL(10,2),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    filled_at       TIMESTAMPTZ
);

-- 차익거래 시그널
CREATE TABLE IF NOT EXISTS arbitrage_signals (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy        VARCHAR(20) NOT NULL,         -- SPATIAL / TRIANGULAR / STATISTICAL
    buy_exchange    VARCHAR(50),
    sell_exchange   VARCHAR(50),
    symbol          VARCHAR(20) NOT NULL,
    gross_spread    DECIMAL(10,6) NOT NULL,
    net_spread      DECIMAL(10,6) NOT NULL,
    estimated_pnl   DECIMAL(20,8),
    actual_pnl      DECIMAL(20,8),
    status          VARCHAR(20) NOT NULL,          -- DETECTED / EXECUTED / MISSED / REJECTED
    detected_at     TIMESTAMPTZ NOT NULL,
    executed_at     TIMESTAMPTZ,
    metadata_json   JSONB
);

-- 포트폴리오 스냅샷
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL,
    exchange        VARCHAR(50) NOT NULL,
    asset           VARCHAR(10) NOT NULL,
    balance         DECIMAL(20,8) NOT NULL,
    usd_value       DECIMAL(20,4) NOT NULL
);

-- 일일 성과
CREATE TABLE IF NOT EXISTS daily_performance (
    date            DATE NOT NULL,
    execution_mode  VARCHAR(10) NOT NULL,
    total_signals   INTEGER DEFAULT 0,
    executed_trades INTEGER DEFAULT 0,
    total_pnl       DECIMAL(20,8) DEFAULT 0,
    total_fees      DECIMAL(20,8) DEFAULT 0,
    net_pnl         DECIMAL(20,8) DEFAULT 0,
    sharpe_ratio    DECIMAL(10,4),
    max_drawdown    DECIMAL(10,6),
    win_rate        DECIMAL(6,4),
    PRIMARY KEY (date, execution_mode)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_trades_signal_id ON trades (signal_id);
CREATE INDEX IF NOT EXISTS idx_trades_exchange_symbol ON trades (exchange, symbol);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades (created_at);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);

CREATE INDEX IF NOT EXISTS idx_signals_strategy ON arbitrage_signals (strategy);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON arbitrage_signals (symbol);
CREATE INDEX IF NOT EXISTS idx_signals_detected_at ON arbitrage_signals (detected_at);
CREATE INDEX IF NOT EXISTS idx_signals_status ON arbitrage_signals (status);

CREATE INDEX IF NOT EXISTS idx_portfolio_timestamp ON portfolio_snapshots (timestamp);
CREATE INDEX IF NOT EXISTS idx_portfolio_exchange_asset ON portfolio_snapshots (exchange, asset);
