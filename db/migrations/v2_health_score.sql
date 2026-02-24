-- Fiery Eyes v2 — Health Score & Execution Layer Migration
-- Run: sudo -u postgres psql -d fiery_eyes -f db/migrations/v2_health_score.sql

-- ============================================
-- 1. KOL Wallets
-- ============================================
CREATE TABLE IF NOT EXISTS kol_wallets (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    wallet_address VARCHAR(64) NOT NULL UNIQUE,
    tier INTEGER NOT NULL DEFAULT 2, -- 1=conviction auto-execute, 2=monitor only, 3=dynamic/platform
    style VARCHAR(50), -- 'conviction', 'high_frequency', 'narrative_caller'
    avg_position_size_sol DECIMAL,
    trades_per_day INTEGER,
    win_rate DECIMAL,
    total_profit_usd DECIMAL,
    conviction_filter_min_usd DECIMAL DEFAULT 500,
    conviction_filter_min_hold_sec INTEGER DEFAULT 600,
    is_active BOOLEAN DEFAULT true,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kol_wallets_tier ON kol_wallets(tier);
CREATE INDEX IF NOT EXISTS idx_kol_wallets_active ON kol_wallets(is_active);

-- ============================================
-- 2. KOL Transactions
-- ============================================
CREATE TABLE IF NOT EXISTS kol_transactions (
    id SERIAL PRIMARY KEY,
    kol_wallet_id INTEGER REFERENCES kol_wallets(id),
    token_address VARCHAR(64) NOT NULL,
    token_symbol VARCHAR(20),
    tx_signature VARCHAR(128) UNIQUE,
    action VARCHAR(10) NOT NULL, -- 'buy' or 'sell'
    amount_sol DECIMAL,
    amount_usd DECIMAL,
    token_amount DECIMAL,
    detected_at TIMESTAMP DEFAULT NOW(),
    is_conviction_buy BOOLEAN DEFAULT false,
    alert_sent BOOLEAN DEFAULT false,
    trade_executed BOOLEAN DEFAULT false,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_kol_tx_wallet ON kol_transactions(kol_wallet_id);
CREATE INDEX IF NOT EXISTS idx_kol_tx_token ON kol_transactions(token_address);
CREATE INDEX IF NOT EXISTS idx_kol_tx_detected ON kol_transactions(detected_at);

-- ============================================
-- 3. Health Scores
-- ============================================
CREATE TABLE IF NOT EXISTS health_scores (
    id SERIAL PRIMARY KEY,
    token_address VARCHAR(64) NOT NULL,
    token_symbol VARCHAR(20),
    scored_at TIMESTAMP DEFAULT NOW(),
    -- Signal scores
    volume_score DECIMAL DEFAULT 0,
    price_score DECIMAL DEFAULT 0,
    kol_score DECIMAL DEFAULT 0,
    social_score DECIMAL DEFAULT 0,
    holder_score DECIMAL DEFAULT 0,
    -- Totals
    raw_score DECIMAL DEFAULT 0,
    max_possible DECIMAL DEFAULT 70,
    scaled_score DECIMAL DEFAULT 0,
    -- Data confidence
    volume_data_state VARCHAR(10) DEFAULT 'missing',
    price_data_state VARCHAR(10) DEFAULT 'missing',
    kol_data_state VARCHAR(10) DEFAULT 'missing',
    social_data_state VARCHAR(10) DEFAULT 'missing',
    holder_data_state VARCHAR(10) DEFAULT 'missing',
    confidence_pct DECIMAL DEFAULT 0,
    -- Context
    token_tier VARCHAR(20),
    regime_state VARCHAR(20),
    liquidity_ratio DECIMAL,
    lp_direction VARCHAR(20),
    -- Action
    recommended_action VARCHAR(20),
    auto_action_enabled BOOLEAN DEFAULT false,
    UNIQUE(token_address, scored_at)
);

CREATE INDEX IF NOT EXISTS idx_health_token ON health_scores(token_address);
CREATE INDEX IF NOT EXISTS idx_health_scored ON health_scores(scored_at);

-- ============================================
-- 4. Shadow Trades
-- ============================================
CREATE TABLE IF NOT EXISTS shadow_trades (
    id SERIAL PRIMARY KEY,
    token_address VARCHAR(64) NOT NULL,
    token_symbol VARCHAR(20),
    entry_source VARCHAR(30) NOT NULL,
    entry_reason TEXT,
    -- Entry
    entry_time TIMESTAMP DEFAULT NOW(),
    entry_price DECIMAL,
    entry_mcap DECIMAL,
    entry_health_score DECIMAL,
    entry_confidence DECIMAL,
    position_size_pct DECIMAL,
    -- Current
    current_price DECIMAL,
    current_health_score DECIMAL,
    current_pnl_pct DECIMAL,
    -- Exit
    exit_time TIMESTAMP,
    exit_price DECIMAL,
    exit_reason VARCHAR(50),
    final_pnl_pct DECIMAL,
    -- Status
    status VARCHAR(20) DEFAULT 'open',
    phases_entered INTEGER DEFAULT 1,
    confirmation_received BOOLEAN DEFAULT false,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_shadow_token ON shadow_trades(token_address);
CREATE INDEX IF NOT EXISTS idx_shadow_status ON shadow_trades(status);

-- ============================================
-- 5. Telegram Calls
-- ============================================
CREATE TABLE IF NOT EXISTS telegram_calls (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL,
    message_text TEXT,
    token_address VARCHAR(64),
    token_symbol VARCHAR(20),
    detected_at TIMESTAMP DEFAULT NOW(),
    safety_result VARCHAR(20),
    safety_flags TEXT,
    action_taken VARCHAR(30),
    health_score_at_call DECIMAL,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_tg_calls_token ON telegram_calls(token_address);
CREATE INDEX IF NOT EXISTS idx_tg_calls_detected ON telegram_calls(detected_at);

-- ============================================
-- 6. Add columns to existing tokens table
-- ============================================
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS token_tier VARCHAR(20) DEFAULT 'hatchling';
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS last_health_score DECIMAL;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS last_health_confidence DECIMAL;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS health_state VARCHAR(20);
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS monitoring_interval_min INTEGER DEFAULT 15;
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS kol_trigger_wallet VARCHAR(64);
ALTER TABLE tokens ADD COLUMN IF NOT EXISTS entry_source VARCHAR(30);
