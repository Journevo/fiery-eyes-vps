-- v7: Chain adoption monitoring tables
-- Run: PGPASSWORD=fiery_eyes_2024 psql -d fiery_eyes -U postgres -h localhost -f db/migrations/v7_chain_adoption.sql

-- chain_metrics: daily chain-level snapshots (TVL, DEX vol, stablecoin mcap)
CREATE TABLE IF NOT EXISTS chain_metrics (
    id          SERIAL PRIMARY KEY,
    date        DATE NOT NULL,
    chain       VARCHAR(50) NOT NULL,
    metric_name VARCHAR(50) NOT NULL,
    value       DOUBLE PRECISION,
    UNIQUE(date, chain, metric_name)
);
CREATE INDEX IF NOT EXISTS idx_chain_metrics_date ON chain_metrics(date);

-- holdings_health: per-token health snapshots (every 4h)
CREATE TABLE IF NOT EXISTS holdings_health (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMP DEFAULT NOW(),
    token           VARCHAR(20) NOT NULL,
    price_usd       DOUBLE PRECISION,
    custom_metrics  JSONB
);
CREATE INDEX IF NOT EXISTS idx_holdings_timestamp ON holdings_health(timestamp);

-- macro_regime_v2: enhanced regime snapshots (every 4h)
CREATE TABLE IF NOT EXISTS macro_regime_v2 (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMP DEFAULT NOW(),
    btc_price       DOUBLE PRECISION,
    btc_dominance   DOUBLE PRECISION,
    sol_btc_ratio   DOUBLE PRECISION,
    funding_avg     DOUBLE PRECISION,
    stablecoin_total DOUBLE PRECISION,
    regime_signal   VARCHAR(20)
);
CREATE INDEX IF NOT EXISTS idx_macro_regime_v2_ts ON macro_regime_v2(timestamp);
