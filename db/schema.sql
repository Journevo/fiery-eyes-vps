-- Fiery Eyes — Database Schema
-- Run: psql -U postgres -d fiery_eyes -f db/schema.sql

-- ============================================
-- 1. Tokens
-- ============================================
CREATE TABLE IF NOT EXISTS tokens (
    id              SERIAL PRIMARY KEY,
    symbol          VARCHAR(32) NOT NULL,
    name            VARCHAR(128),
    chain           VARCHAR(16) NOT NULL DEFAULT 'solana',
    contract_address VARCHAR(64) NOT NULL UNIQUE,
    category        VARCHAR(16) CHECK (category IN ('meme', 'adoption', 'infra')),
    launch_date     TIMESTAMPTZ,
    safety_score    REAL,
    quality_gate_pass BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tokens_contract ON tokens(contract_address);
CREATE INDEX IF NOT EXISTS idx_tokens_category ON tokens(category);

-- ============================================
-- 2. Daily Snapshots
-- ============================================
CREATE TABLE IF NOT EXISTS snapshots_daily (
    id              SERIAL PRIMARY KEY,
    token_id        INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    price           DOUBLE PRECISION,
    mcap            DOUBLE PRECISION,
    volume          DOUBLE PRECISION,
    liquidity_depth_10k  REAL,
    liquidity_depth_50k  REAL,
    holders_raw          INTEGER,
    holders_quality_adjusted INTEGER,
    retention_7d         REAL,
    retention_30d        REAL,
    median_wallet_balance DOUBLE PRECISION,
    fees                 DOUBLE PRECISION,
    revenue              DOUBLE PRECISION,
    stablecoin_inflow    DOUBLE PRECISION,
    dev_commits          INTEGER,
    dev_active           BOOLEAN,
    top10_pct            REAL,
    top50_pct            REAL,
    gini                 REAL,
    unlock_next_30d_usd  DOUBLE PRECISION,
    unlock_to_volume_ratio REAL,
    social_velocity      REAL,
    smart_money_netflow  DOUBLE PRECISION,
    fresh_wallet_pct     REAL,
    sybil_risk_score     REAL,
    UNIQUE(token_id, date)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_token_date ON snapshots_daily(token_id, date);

-- ============================================
-- 3. Daily Scores
-- ============================================
CREATE TABLE IF NOT EXISTS scores_daily (
    id              SERIAL PRIMARY KEY,
    token_id        INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    date            DATE NOT NULL,
    momentum_score  REAL,
    adoption_score  REAL,
    infra_score     REAL,
    composite_score REAL,
    confidence_score REAL,
    regime_multiplier REAL,
    final_score     REAL,
    UNIQUE(token_id, date)
);

CREATE INDEX IF NOT EXISTS idx_scores_token_date ON scores_daily(token_id, date);

-- ============================================
-- 4. Alerts
-- ============================================
CREATE TABLE IF NOT EXISTS alerts (
    id                SERIAL PRIMARY KEY,
    token_id          INTEGER REFERENCES tokens(id) ON DELETE SET NULL,
    timestamp         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    type              VARCHAR(32) NOT NULL,
    severity          VARCHAR(16) CHECK (severity IN ('info', 'warning', 'critical')),
    feature_vector_json JSONB,
    price_at_alert    DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_alerts_token ON alerts(token_id);
CREATE INDEX IF NOT EXISTS idx_alerts_time ON alerts(timestamp);

-- ============================================
-- 5. Positions
-- ============================================
CREATE TABLE IF NOT EXISTS positions (
    id              SERIAL PRIMARY KEY,
    token_id        INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    entry_date      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    entry_price     DOUBLE PRECISION NOT NULL,
    size_pct        REAL NOT NULL,
    tier            SMALLINT CHECK (tier BETWEEN 1 AND 5),
    thesis          TEXT,
    invalidation_rules_json JSONB,
    status          VARCHAR(16) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'stopped'))
);

CREATE INDEX IF NOT EXISTS idx_positions_token ON positions(token_id);

-- ============================================
-- 6. Performance Log
-- ============================================
CREATE TABLE IF NOT EXISTS performance_log (
    id              SERIAL PRIMARY KEY,
    token_id        INTEGER NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
    alert_date      TIMESTAMPTZ NOT NULL,
    alert_score     REAL,
    price_at_alert  DOUBLE PRECISION,
    price_7d        DOUBLE PRECISION,
    price_30d       DOUBLE PRECISION,
    price_90d       DOUBLE PRECISION,
    outcome_category VARCHAR(32)
);

CREATE INDEX IF NOT EXISTS idx_perflog_token ON performance_log(token_id);

-- ============================================
-- 7. Wallet Reputation
-- ============================================
CREATE TABLE IF NOT EXISTS wallet_reputation (
    wallet_address  VARCHAR(64) PRIMARY KEY,
    tracked_since   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    total_entries   INTEGER DEFAULT 0,
    positive_entries INTEGER DEFAULT 0,
    avg_return      REAL,
    last_dump_date  TIMESTAMPTZ,
    reputation_score REAL DEFAULT 50.0
);

-- ============================================
-- 8. Regime Snapshots
-- ============================================
CREATE TABLE IF NOT EXISTS regime_snapshots (
    id                  SERIAL PRIMARY KEY,
    date                DATE NOT NULL UNIQUE,
    btc_trend_score     REAL,
    stablecoin_supply_delta DOUBLE PRECISION,
    liquidity_proxy     REAL,
    risk_appetite       REAL,
    regime_multiplier   REAL
);

CREATE INDEX IF NOT EXISTS idx_regime_date ON regime_snapshots(date);
