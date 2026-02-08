-- ArBot ClickHouse Schema
-- Requires ClickHouse 24+

-- 오더북 스냅샷
CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    timestamp       DateTime64(3),
    exchange        LowCardinality(String),
    symbol          LowCardinality(String),
    bids            Array(Tuple(price Float64, qty Float64)),
    asks            Array(Tuple(price Float64, qty Float64)),
    mid_price       Float64,
    spread          Float64
) ENGINE = MergeTree()
ORDER BY (exchange, symbol, timestamp)
TTL timestamp + INTERVAL 90 DAY;

-- 체결 데이터
CREATE TABLE IF NOT EXISTS tick_trades (
    timestamp       DateTime64(3),
    exchange        LowCardinality(String),
    symbol          LowCardinality(String),
    price           Float64,
    quantity        Float64,
    side            LowCardinality(String)
) ENGINE = MergeTree()
ORDER BY (exchange, symbol, timestamp)
TTL timestamp + INTERVAL 180 DAY;

-- 스프레드 데이터 (사전 계산)
CREATE TABLE IF NOT EXISTS spread_history (
    timestamp       DateTime64(3),
    symbol          LowCardinality(String),
    exchange_a      LowCardinality(String),
    exchange_b      LowCardinality(String),
    price_a         Float64,
    price_b         Float64,
    spread_pct      Float64,
    net_spread_pct  Float64
) ENGINE = MergeTree()
ORDER BY (symbol, exchange_a, exchange_b, timestamp)
TTL timestamp + INTERVAL 365 DAY;
