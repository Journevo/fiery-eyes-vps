-- v5: GMGN smart money wallet leaderboard
-- Stores weekly-scraped wallets from GMGN with tier classification

CREATE TABLE IF NOT EXISTS gmgn_wallets (
    id              SERIAL PRIMARY KEY,
    wallet_address  VARCHAR(64) NOT NULL UNIQUE,
    display_name    VARCHAR(100),
    tier            CHAR(1) NOT NULL DEFAULT 'C',   -- A, B, or C
    win_rate        REAL NOT NULL DEFAULT 0,
    pnl_usd         NUMERIC DEFAULT 0,
    pnl_pct         REAL DEFAULT 0,
    trade_count     INTEGER DEFAULT 0,
    avg_hold_minutes REAL DEFAULT 0,
    last_active     TIMESTAMP,
    gmgn_score      REAL DEFAULT 0,                  -- composite quality score 0-100
    is_insider      BOOLEAN DEFAULT FALSE,
    is_active       BOOLEAN DEFAULT TRUE,
    first_seen      TIMESTAMP DEFAULT NOW(),
    last_updated    TIMESTAMP DEFAULT NOW(),
    raw_data        JSONB
);

CREATE INDEX IF NOT EXISTS idx_gmgn_wallets_tier ON gmgn_wallets(tier);
CREATE INDEX IF NOT EXISTS idx_gmgn_wallets_active ON gmgn_wallets(is_active);
CREATE INDEX IF NOT EXISTS idx_gmgn_wallets_score ON gmgn_wallets(gmgn_score DESC);

-- Track weekly scrape runs
CREATE TABLE IF NOT EXISTS gmgn_scrape_log (
    id              SERIAL PRIMARY KEY,
    scraped_at      TIMESTAMP DEFAULT NOW(),
    total_found     INTEGER DEFAULT 0,
    passed_filter   INTEGER DEFAULT 0,
    tier_a          INTEGER DEFAULT 0,
    tier_b          INTEGER DEFAULT 0,
    tier_c          INTEGER DEFAULT 0,
    new_wallets     INTEGER DEFAULT 0,
    removed_wallets INTEGER DEFAULT 0,
    error           TEXT
);
