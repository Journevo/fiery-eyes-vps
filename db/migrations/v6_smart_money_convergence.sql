-- v6: Smart money convergence detection
-- Tracks when multiple independent wallets buy the same token

CREATE TABLE IF NOT EXISTS smart_money_convergence (
    id              SERIAL PRIMARY KEY,
    token_address   VARCHAR(64) NOT NULL,
    token_symbol    VARCHAR(20),
    wallet_count    INTEGER NOT NULL DEFAULT 0,
    weighted_score  REAL NOT NULL DEFAULT 0,
    convergence_level VARCHAR(20) NOT NULL DEFAULT 'WATCHING',
    wallets_json    JSONB,              -- [{address, tier, display_name, amount_usd, detected_at}]
    first_buy_at    TIMESTAMP NOT NULL,
    last_buy_at     TIMESTAMP NOT NULL,
    window_hours    REAL DEFAULT 6,
    alert_sent      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW(),
    resolved_at     TIMESTAMP           -- set when convergence expires or position opened
);

CREATE INDEX IF NOT EXISTS idx_smc_token ON smart_money_convergence(token_address);
CREATE INDEX IF NOT EXISTS idx_smc_level ON smart_money_convergence(convergence_level);
CREATE INDEX IF NOT EXISTS idx_smc_created ON smart_money_convergence(created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_smc_active_token
    ON smart_money_convergence(token_address)
    WHERE resolved_at IS NULL;
