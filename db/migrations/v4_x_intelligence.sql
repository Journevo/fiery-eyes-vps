-- v4: X/Twitter smart money intelligence table
-- Stores parsed signals from 4 smart money X accounts via Grok API

CREATE TABLE IF NOT EXISTS x_intelligence (
    id              SERIAL PRIMARY KEY,
    source_handle   TEXT NOT NULL,
    tweet_id        TEXT UNIQUE,
    tweet_text      TEXT,
    parsed_type     TEXT,
    token_address   TEXT,
    token_symbol    TEXT,
    wallet_address  TEXT,
    amount_usd      REAL,
    signal_strength TEXT,
    raw_data        JSONB,
    detected_at     TIMESTAMPTZ DEFAULT NOW(),
    processed       BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_x_intel_source   ON x_intelligence(source_handle);
CREATE INDEX IF NOT EXISTS idx_x_intel_token    ON x_intelligence(token_address);
CREATE INDEX IF NOT EXISTS idx_x_intel_detected ON x_intelligence(detected_at);
CREATE INDEX IF NOT EXISTS idx_x_intel_type     ON x_intelligence(parsed_type);
