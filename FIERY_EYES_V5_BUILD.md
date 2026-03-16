# FIERY GOLDEN EYES v5.1 — BUILD SPECIFICATION
## Lean-first: prove the core edge, then widen
## Date: 2026-03-14
## For: Claude Code on VPS 89.167.83.21

---

## PHASE 1: MUST-SHIP (Week 1-2)
## Prove the core edge works before adding complexity.

### TASK 1: BTC Cycle Tracker
**Priority: HIGHEST | Time: 30 min | Dependencies: CoinGecko API**

The single highest-value feature. One calculation, massive insight.

**What to build:**
- Store historical cycle data as constants:
  ```python
  CYCLES = [
      {"bottom": "2011-11-01", "peak": "2013-12-01", "bottom_price": 2, "peak_price": 1127, "drawdown": 85},
      {"bottom": "2015-01-01", "peak": "2017-12-01", "bottom_price": 172, "peak_price": 19783, "drawdown": 84},
      {"bottom": "2018-12-01", "peak": "2021-11-01", "bottom_price": 3200, "peak_price": 69000, "drawdown": 77},
      {"bottom": "2022-11-21", "peak": "2025-10-06", "bottom_price": 15479, "peak_price": 126000, "drawdown": None},
  ]
  ```
- Calculate daily: days since peak, bear progress %, current drawdown %
- Calculate drawdown scenarios: -52% ($60K consensus), -60% ($50K diminishing), -77% ($29K full cycle)
- Average peak-to-bottom: ~380 days → estimated bottom date
- Store in `btc_cycle` table: date, btc_price, days_since_peak, drawdown_pct, bear_progress_pct

**Output for Telegram:**
```
📊 BTC CYCLE
  Peak: $126K (Oct 6) | Now: $70.8K (-44%)
  Bear: ████████░░░░░░░░░░░░ 42% (~215 days to est. bottom)
  Scenarios: -52% = $60K | -60% = $50K | -77% = $29K
  Funding: -0.01% (14d negative — longest since Dec 2022 bottom)
```

**Test:** Output should show 159 days since peak, 42% progress, with scenarios.

---

### TASK 2: Watchlist Price Tracker (4 Core + BTC + ISA proxies)
**Priority: HIGHEST | Time: 45 min | Dependencies: CoinGecko Demo API or DexScreener**

Start with 4 core tokens only (JUP, HYPE, RENDER, BONK) + SOL/BTC + MSTR/COIN. Not 9 — prove it works first, expand later.

**What to build:**
- Every 4 hours, fetch for each token: price, 24h change, 7d change
- Calculate: % from ATH, zone (Deep Value >70% down | Mid Range 30-70% | Near ATH <30%)
- For MSTR: also calculate mNAV = market_cap / (btc_held * btc_price). Use btc_held = 713502, avg_cost = 76052
- Store in `watchlist_status` table: token, price, ath, pct_from_ath, zone, change_24h, change_7d, timestamp
- When zone shifts (e.g., Deep → Mid) → trigger alert

**Hardcoded ATH values:**
```python
ATH = {
    "BTC": 126000,    # Oct 6, 2025
    "SOL": 295,       # Nov 2024
    "JUP": 2.00,      # Jan 2025
    "HYPE": 59,       # Dec 2025
    "RENDER": 13.59,  # Mar 2024
    "BONK": 0.000059, # Nov 2024
    "MSTR": 457,      # Late 2024
    "COIN": 238,      # Estimate
}
```

**Output for Telegram (watchlist section of daily report):**
```
Token    Price     24h    %ATH   Zone        Score
BTC      $70,800   +5%    -44%   Bear 42%    —
SOL      $89       -3%    -70%   🟢 Deep     43/60
JUP      $0.166    +2%    -92%   🟢 Deep     52/60
HYPE     $37       -2%    -37%   🟡 Mid      50/60
RENDER   $1.79     +1%    -87%   🟢 Deep     47/60
BONK     $5.9e-6   -4%    -90%   🟢 Deep     44/60
MSTR     $125      -1%    -73%   mNAV: 1.08  ISA
COIN     $165      -2%    -31%   🟡 Mid      ISA
```

**Test:** All prices should return, ATH calculations should match our known values.

---

### TASK 3: On-Chain Large Swap Detection (replaces broken Grok as primary signal)
**Priority: HIGHEST | Time: 60 min | Dependencies: DexScreener API or Birdeye API**

**CRITICAL DESIGN CHANGE FROM v5:** Don't start with Grok tweet parsing. Start with on-chain swap detection. This bypasses the entire X/Grok fragility chain and gives you raw financial data directly.

**What to build:**
- Monitor the actual DEX liquidity pools for 4 core tokens (JUP, HYPE, RENDER, BONK) + SOL
- Detect swaps >$50K for core tokens (high liquidity) via DexScreener or Birdeye API
- For each detected swap: token, direction (buy/sell), amount_usd, amount_as_pct_of_mcap, pool, timestamp
- Store in `large_swaps` table

**CRITICAL — The Whale Latency Rule:**
```python
# If token MCap < $200M, DO NOT treat whale buy as entry signal
# You CANNOT beat the speed. The candle has already spiked.
# Whale buy on small cap = "watch for pullback" not "chase"
if token_mcap < 200_000_000 and direction == "BUY":
    alert_type = "WHALE_WATCHING"  # Do NOT recommend entry
    note = "Small cap whale buy — watch for pullback, do not chase"
else:
    alert_type = "WHALE_SIGNAL"  # Valid entry signal consideration
    note = "Deep liquidity token — whale buy is actionable"
```

**Alert format:**
```
🐋 LARGE SWAP — $JUP
  $340K BUY detected on Raydium pool
  = 0.06% of MCap ($580M) — meaningful for DeFi token
  Token: JUP | Zone: 🟢 Deep Value (-92% ATH)
  Type: WHALE_SIGNAL (deep liquidity, actionable)
```

**Data sources (try in this order):**
1. DexScreener API — free, covers Solana pairs. Check: `https://api.dexscreener.com/latest/dex/tokens/{address}`
2. Birdeye API — free tier, Solana-specific. Better for trade history.
3. Helius — webhooks on specific token accounts (already have 1M free credits)

**Test:** Run for 24h. Compare detected swaps against StalkHQ/SunFlow tweets from the same period. Did on-chain detection catch the same signals?

---

### TASK 4: FRED Liquidity Tracker
**Priority: HIGH | Time: 30 min | Dependencies: FRED API key**

Copy the functions directly from Jingubang's app.py. The code already exists and works.

**What to copy from Jingubang (app.py):**
```python
# These functions already exist on VPS 134.209.176.180:
fetch_fred_series(series_id)           # Fetch latest FRED value
fetch_fred_series_historical(series_id, days_ago)  # Fetch historical
compute_fred_regime()                  # 30-day slope → EXPANDING/STALL/CONTRACTING
fetch_us_net_liquidity()               # Fed BS - TGA - RRP
fetch_global_net_liquidity()           # Fed + ECB + BOJ
fetch_global_m2()                      # M2 money supply
```

**Additional calculations to build:**
- M2 lag tracker: days since M2 inflection (Oct 2025), status (PENDING/WINDOW/EXPIRED)
- Liquidity alignment: are US liq, global liq, M2, DXY all pointing same direction?

**CRITICAL — Feed the synthesis engine VELOCITY, not static values:**
```python
def format_for_synthesis(series_name, current, previous, prev_month):
    """Format liquidity data with rate of change for synthesis engine."""
    mom_change = ((current - previous) / abs(previous)) * 100 if previous else 0
    direction = "↗ rising" if mom_change > 0.05 else ("↘ falling" if mom_change < -0.05 else "→ flat")
    # Example output: "US Net Liq: $5.70T (→ flat, +0.02% MoM, 3rd consecutive flat month)"
    return f"{series_name}: ${current:.2f}T ({direction}, {mom_change:+.2f}% MoM)"
```

**Store in `liquidity` table:** date, us_net_liq, global_net_liq, global_m2, dxy, fred_regime, fred_slope, m2_lag_days, m2_lag_status

**Output:**
```
💧 LIQUIDITY
  US Net Liq: $5.70T (→ flat, FRED: EXPANDING slope +1.8%)
  Global: $12.52T (→ flat)
  M2: $126T ATH (↗ +0.4% MoM, rising 5 consecutive months)
  M2 lag: 155 days since inflection (EXPIRED — QT handbrake)
  DXY: ~100 (was 97, bouncing)
  Alignment: MIXED (M2 bullish, net liq flat, DXY rising)
```

**Test:** Values should roughly match what Jingubang shows in its /liquidity command.

---

### TASK 5: Daily Intelligence Report (ONE output, not four)
**Priority: HIGH | Time: 60 min | Dependencies: Tasks 1-4 complete**

**DESIGN CHANGE:** Don't build pulse + nightly + weekly + H-Fire. Build ONE daily report. Prove it's valuable. Split into multiple formats later.

**Report structure (sent once daily at 00:00 UTC + on-demand via /report command):**
```
🌙 FIERY EYES — Mar 14 2026

━━━ BTC CYCLE ━━━
Peak: $126K (Oct 6) | Now: $70.8K (-44%)
Bear: ████████░░░░░░░░░░░░ 42% (~215d to est. bottom)
Funding: -0.01% (14d neg — local bottom signal)

━━━ LIQUIDITY ━━━
US $5.70T (→) | M2 $126T ATH (↗) | DXY 100 (↗)
FRED: EXPANDING (slope +1.8%) | M2 lag: EXPIRED (QT handbrake)
Alignment: MIXED — M2 says go, net liq says wait

━━━ WATCHLIST ━━━
Token    Price     %ATH   Zone      Score  Base★  Down
JUP      $0.166    -92%   🟢 Deep   52/60  2.9x   -40%
HYPE     $37       -37%   🟡 Mid    50/60  1.6x   -30%
RENDER   $1.79     -87%   🟢 Deep   47/60  2.5x   -45%
BONK     $5.9e-6   -90%   🟢 Deep   44/60  5.3x   -50%
★Base = regime-weighted | Down = if BTC drops to $50K

━━━ ISA PROXIES ━━━
MSTR: $125 (mNAV 1.08 ⚠️ near book) | COIN: $165

━━━ LARGE SWAPS (24h) ━━━
[only if detected — otherwise omit section]
JUP: $340K buy on Raydium (0.06% MCap) — WHALE_SIGNAL
HYPE: quiet

━━━ REGIME ━━━
Deploy: 40-50% max (bear <60%, F&G 32)
Dry powder: 50-60% (earning yield on Kamino?)
Cycle says: accumulate slowly, save for $50-60K BTC zone
Macro says: early expansion building, but 6mo before it matters

━━━ RECOMMENDATION ━━━
FOCUS: JUP (highest conviction — zero emissions, deep value, whale activity)
WATCH: RENDER (AI narrative building, RenderCon Apr 16)
PATIENCE: HYPE (wait for deeper pullback or cycle progress >60%)
AVOID: adding new positions until bear >50%
```

**Rules:**
- NEVER show empty sections. If no large swaps, omit that section entirely.
- Down column is MANDATORY. Shows estimated loss if BTC drops to $50K.
- Recommendation must be specific: FOCUS (act on this), WATCH (preparing), PATIENCE (not yet), AVOID.

**Test:** Generate report manually once. Is it readable in 2 minutes? Does it tell you what to DO?

---

### TASK 6: Recommendation Ledger with Full State Snapshots
**Priority: HIGH | Time: 30 min | Dependencies: Task 5**

**What to build:**
- Every time the daily report fires, auto-log to `recommendations` table:
  ```sql
  CREATE TABLE recommendations (
      id INTEGER PRIMARY KEY,
      date TEXT NOT NULL,
      token TEXT NOT NULL,
      action TEXT NOT NULL,  -- FOCUS/WATCH/PATIENCE/AVOID/ADD/REDUCE
      price_at_rec REAL NOT NULL,
      score_at_rec TEXT,
      conviction TEXT,  -- HIGH/MEDIUM/LOW
      btc_price REAL,
      bear_progress REAL,
      f_and_g INTEGER,
      regime_deploy_pct INTEGER,
      signals_present TEXT,  -- JSON list of what triggered this
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```

- Full state snapshot (JSON blob) for debugging:
  ```sql
  CREATE TABLE state_snapshots (
      id INTEGER PRIMARY KEY,
      date TEXT NOT NULL,
      snapshot JSON NOT NULL,  -- ALL metrics from all layers at this moment
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
  );
  ```

- Weekly review query: compare recommendations from 7/30 days ago vs actual price now
  ```python
  def review_recommendations(days_ago=7):
      recs = db.query("SELECT * FROM recommendations WHERE date = ?", days_ago_date)
      for rec in recs:
          current_price = get_current_price(rec.token)
          pnl_pct = (current_price - rec.price_at_rec) / rec.price_at_rec * 100
          # Was the recommendation right?
  ```

- Telegram command: /ledger — shows last 10 recommendations with outcomes

**Test:** After first daily report, verify recommendation is logged. After 7 days, verify /ledger shows outcomes.

---

### TASK 7: Portfolio Tracker
**Priority: HIGH | Time: 30 min | Dependencies: Task 2 (prices)**

**Telegram commands:**
- `/bought JUP 1000 0.166` — log a purchase
- `/sold JUP 500 0.25` — log a sale
- `/portfolio` — show current positions vs targets
- `/pnl` — unrealised PnL

**Database:**
```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    token TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    token TEXT NOT NULL,
    direction TEXT NOT NULL,  -- BUY/SELL
    amount REAL NOT NULL,
    price REAL NOT NULL,
    trade_date TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Portfolio output:**
```
💼 PORTFOLIO
Token    Held     Entry   Current  PnL      Target%  Actual%
JUP      1000     $0.166  $0.166   $0 (0%)  25%      22%
HYPE     5        $37     $37      $0 (0%)  20%      24%
RENDER   0        —       $1.79    —         17%      0% ⬇
BONK     0        —       $5.9e-6  —         15%      0% ⬇
Dry: $850 USDC (45%) | Total deployed: $1,015 (55%)
```

**Test:** /bought JUP 1000 0.166, then /portfolio should show the position.

---

## PHASE 2: ADD DEPTH (Week 3-4)
## Only start after Phase 1 is stable for 7+ days.

### TASK 8: Fix Grok Tier 1 (supplementary to on-chain detection)
Now Grok becomes CONTEXT for on-chain signals, not the primary source.
- Verify polling for 7 accounts: StalkHQ, SunFlow, aixbt, Lookonchain, Moby, KOLScan, GMGN
- Build parsers per source format
- When Grok catches a signal that on-chain ALSO detected → CONVERGENCE (higher conviction)
- When Grok catches a signal on-chain MISSED → supplementary context (add to report)
- aixbt special: parse each line separately (structured multi-token format)

### TASK 9: DeFiLlama Revenue (auto-updates projections)
- Daily pull: HYPE, JUP, PUMP revenue (24h, 7d, 30d)
- Annualise → feed projection engine
- Revenue trend: WoW, MoM change (with velocity for synthesis)
- If revenue drops >20% MoM → flag in report

### TASK 10: Coinglass Market Structure
- Every 4h: BTC OI, funding rate (+ consecutive streak), liquidation clusters, long/short ratio
- One line in daily report
- Detect: funding negative >10d = local bottom signal. OI spike + high funding = overleveraged.

### TASK 11: Refocus YouTube Intelligence
- Enhanced extraction prompt: price targets, reasoning, conditions, personal action
- Channel weighting by subscriber count
- Recency decay: >48h = stale
- Health check: alert if no transcripts for 24h
- Only surface in report when watchlist token mentioned >7/10 conviction

### TASK 12: Supply Flow Monitor
- HYPE: DeFiLlama revenue → buyback estimate vs known emissions (27K staking + 40K team/day)
- JUP: governance watch (discuss.jup.ag RSS). Alert on emission proposals.
- PUMP: countdown to Jul 12 cliff. Alert at 90/60/30/14/7/3/1 days.
- RENDER: monthly burn rate tracking.

---

## PHASE 3: EXPAND (Week 5+)
## Only after Phase 2 data is flowing AND recommendation ledger has 14+ entries.

### TASK 13: Synthesis Engine
Daily Claude Sonnet call. Input: all Layer 1-7 data WITH velocity/rate-of-change.
Output: narratives, causal chains, contradictions, actionable insight.

### TASK 14: Expand watchlist to 8 tokens
Add PUMP, PENGU, FARTCOIN, USELESS once core 4 are tracked and reporting well.

### TASK 15: Cross-Chain Monitoring
Weekly: SOL vs SUI vs ETH vs Base on active addresses, DEX volume, TVL, revenue.

### TASK 16: Split into Multiple Telegram Outputs
Now that the daily report is proven, split into: pulse (4h), nightly (detailed), weekly (review), H-Fire (alerts).

### TASK 17: /deepdive Command
Paste CA → 9 sources → Claude scores → full report in 60 seconds.

### TASK 18: Score Auto-Update
Split static (40%) / dynamic (60%). Dynamic auto-recalculates from DeFiLlama revenue, price momentum, smart money activity.

### TASK 19: Dry Powder Yield Monitoring
Track if USDC is deployed on Kamino/marginfi. Show yield in report.

### TASK 20: Exit Alert System
Stop loss triggers per position. Take profit levels. Thesis review after 25% drawdown.

---

## KEY TECHNICAL DETAILS

### Database: PostgreSQL (fiery_eyes) on VPS 89.167.83.21

**New tables for v5.1:**
```sql
-- BTC cycle tracking
CREATE TABLE btc_cycle (
    date TEXT PRIMARY KEY,
    btc_price REAL,
    days_since_peak INTEGER,
    drawdown_pct REAL,
    bear_progress_pct REAL
);

-- Watchlist prices
CREATE TABLE watchlist_status (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL,
    price REAL,
    ath REAL,
    pct_from_ath REAL,
    zone TEXT,
    change_24h REAL,
    change_7d REAL,
    mcap REAL,
    timestamp TIMESTAMP DEFAULT NOW()
);

-- On-chain large swaps
CREATE TABLE large_swaps (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount_usd REAL,
    pct_of_mcap REAL,
    pool TEXT,
    alert_type TEXT,  -- WHALE_SIGNAL or WHALE_WATCHING
    timestamp TIMESTAMP DEFAULT NOW()
);

-- Liquidity data
CREATE TABLE liquidity (
    date TEXT PRIMARY KEY,
    us_net_liq REAL,
    global_net_liq REAL,
    global_m2 REAL,
    dxy REAL,
    fred_regime TEXT,
    fred_slope REAL,
    m2_lag_days INTEGER,
    m2_lag_status TEXT,
    alignment TEXT
);

-- Smart money signals (from Grok + on-chain)
CREATE TABLE smart_money_signals (
    id SERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    token TEXT NOT NULL,
    direction TEXT,
    amount_usd REAL,
    pct_of_mcap REAL,
    wallet TEXT,
    wallet_quality TEXT,
    alert_type TEXT,
    raw_text TEXT,
    timestamp TIMESTAMP DEFAULT NOW()
);

-- Portfolio positions
CREATE TABLE positions (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_date TEXT NOT NULL,
    notes TEXT
);

-- Trade log
CREATE TABLE trades (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL,
    direction TEXT NOT NULL,
    amount REAL NOT NULL,
    price REAL NOT NULL,
    trade_date TEXT NOT NULL
);

-- Recommendations with full state
CREATE TABLE recommendations (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    token TEXT NOT NULL,
    action TEXT NOT NULL,
    price_at_rec REAL,
    score TEXT,
    conviction TEXT,
    btc_price REAL,
    bear_progress REAL,
    fg_index INTEGER,
    deploy_pct INTEGER,
    signals TEXT,  -- JSON
    created_at TIMESTAMP DEFAULT NOW()
);

-- Full state snapshots for backtesting
CREATE TABLE state_snapshots (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    snapshot JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### APIs needed in .env:
```
# Already have:
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID_HFIRE=xxx
TELEGRAM_CHAT_ID_HUOYAN=xxx
TELEGRAM_CHAT_ID_SYSTEM=xxx
GROK_API_KEY=xxx
CLAUDE_API_KEY=xxx
HELIUS_API_KEY=xxx
COINGECKO_API_KEY=xxx  # Demo tier

# Need to add:
FRED_API_KEY=xxx  # Copy from Jingubang .env
# DexScreener — no key needed (free)
# DeFiLlama — no key needed (free)
# alternative.me — no key needed (free)
# Coinglass — check if key needed
```

### Jingubang reference (VPS 134.209.176.180):
- FRED functions to copy: in app.py lines ~250-500
- Key functions: fetch_fred_series, fetch_fred_series_historical, compute_fred_regime, fetch_us_net_liquidity, fetch_global_net_liquidity, fetch_global_m2
- Nimbus data (stale): nimbus_data.py — manual update needed
- FRED_API_KEY: in Jingubang's .env file

### Contract addresses for on-chain monitoring:
```python
TOKENS = {
    "SOL": "So11111111111111111111111111111111111111112",
    "JUP": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "USELESS": "2weMjPLLybRMMva1fM3U31goWWrCpF59CHWNhnCJ9Vyh",
    "RENDER": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
    # HYPE is on Hyperliquid chain, not Solana — use CoinGecko for price
    # PUMP, PENGU, FARTCOIN — add in Phase 3
}
```

---

## DESIGN PRINCIPLES (non-negotiable)

1. **Fix pipes before features.** On-chain swap detection must work before anything else.
2. **Show the pain.** Every projection includes DOWNSIDE (BTC to $50K scenario).
3. **Cycle gates everything.** Bear <60% = max 50% deployed.
4. **Whale latency rule.** <$200M MCap whale buy = "watch for pullback" NOT "chase."
5. **Feed velocity, not static.** Synthesis engine gets rate-of-change, not just numbers.
6. **Full state snapshots.** Every recommendation logged with complete system state for backtesting.
7. **Never show empty sections.** If no data, omit entirely.
8. **3-4 active trades max.** Don't pretend you'll manage 8 positions.
9. **Dry powder earns yield.** Idle USDC should be on Kamino/marginfi.
10. **Prove edge before expanding.** 14 days stable + 10 logged recommendations before Phase 2.

---

## HARD RULES FOR CLAUDE CODE

- Read this ENTIRE document before writing any code
- Start with Task 1 (BTC cycle tracker) — it's 30 minutes and immediately valuable
- After each task, test with real data before moving to the next
- Commit to git after each working task
- Restart the daemon (systemctl restart fiery-eyes-v2) after code changes
- If a data source API doesn't work, log the error and skip gracefully — don't crash
- All Telegram messages: HTML parse mode, escape user data with html.escape()
- Never hardcode API keys — always from .env
- PostgreSQL connection: use the existing fiery_eyes database
