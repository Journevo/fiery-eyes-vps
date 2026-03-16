# FIERY EYES V6 — DEFINITIVE ARCHITECTURE SPEC
## Date: 16 March 2026
## For: Claude Code deployment on 89.167.83.21

---

## THE SYSTEM'S ONE JOB

Help the user decide WHEN to be in/out of the market and WHAT to hold.
Everything feeds this decision. Nothing else matters.

---

## THE CIRCLE

```
📊 INTEL (collect)
    ↓
🐋 SIGNALS (analyse per token)
    ↓
🔥 FIERY EYES (synthesise + decide)
    ↓
📓 NOTEBOOK → paste to Opus → updated scores + in/out call
    ↓
💼 PORTFOLIO (positions + P&L + risk)
    ↓
Review: did signals predict correctly? → feeds back to 🐋
    ↓
Back to 📊 (next day)
```

---

## 5 MAIN TELEGRAM BUTTONS

```
📊 Intel  |  🐋 Signals  |  🔥 Fiery Eyes  |  💼 Portfolio  |  ⚙️ System
```

Every `send_message` and `reply_text` call MUST include this 5-button 
persistent keyboard. No exceptions.

---

## BUTTON 1: 📊 INTEL (raw world data)

Sub-buttons: [YouTube] [Macro] [Liquidity] [Chain]

### [YouTube] — dashboard summary of today's videos

```
📺 YOUTUBE TODAY — {X} videos ({Y} Sonnet, {Z} Haiku)

TOP THEMES:
• {theme 1}: {count} videos — {consensus}
• {theme 2}: {count} videos — {consensus}
• {theme 3}: {count} videos — {consensus}

SONNET HIGHLIGHTS:
📺 {Channel} — "{Title}" — {one-line takeaway}
📺 {Channel} — "{Title}" — {one-line takeaway}
📺 {Channel} — "{Title}" — {one-line takeaway}
📺 {Channel} — "{Title}" — {one-line takeaway}
📺 {Channel} — "{Title}" — {one-line takeaway}

CONSENSUS: {what most voices agree on}
DISAGREEMENT: {where voices clash}
```

This is a SUMMARY DASHBOARD — not full analyses. Full analyses arrive 
as individual Telegram messages throughout the day (Sonnet only).

Data source: YouTube analyses DB, auto-generated from last 24h.

### [Macro] — business cycle + economic indicators

```
🌐 MACRO DASHBOARD — {time} UTC

BUSINESS CYCLE: {SPRING/SUMMER/AUTUMN/WINTER}
├── 🇺🇸 PMI: {val} | Infl: {val}% | UE: {val}%
├── 🇪🇺 PMI: {val} | Infl: {val}% | UE: {val}%
├── 🇨🇳 PMI: {val} | Infl: {val}% | UE: {val}%
├── 🇯🇵 PMI: {val} | Infl: {val}% | UE: —
├── 🇬🇧 PMI: {val} | Infl: {val}% | UE: {val}%
└── AVG PMI: {val}

LEADING INDICATORS: {SUPPORT X/4}
├── Yield Curve: {val} — {steep/inverted/flat}
├── HY Spreads: {val} — {direction}
├── Claims: {val}k — {direction}
└── Sahm Rule: {val}

RATES:
├── Real Rate: {val}% | FFR: {val}%
├── VIX: {val} ({LOW/ELEVATED/HIGH/EXTREME})
├── M2 YoY: {val}% | BEV: {val}%
└── Oil: ${val} ({status})

MACRO VERDICT: {TAILWIND / NEUTRAL / HEADWIND}
{One sentence explaining why}
```

Data source: Nimbus sync (05:45 UTC daily). All data from TradingView 
indicators already built. FRED supplements for US data.

### [Liquidity] — money flows

```
💧 LIQUIDITY — {time} UTC

US Net Liquidity: ${val}T — {EXPANDING/STALL/CONTRACTING}
├── 1M: {val}% | 3M: {val}% | 6M: {val}%
└── Slope: {val}%

Global Net Liquidity: ${val}T — {status}
├── 🇺🇸 {status} | 🇪🇺 {status} | 🇯🇵 {status}
├── 🇨🇳 {status} | 🇬🇧 {status}
├── Quality: {score} ({pct}%)
└── 12W Lead: {direction}

Global M2: ${val}T — {FLAT/EXPANDING/CONTRACTING}
├── Slope: {val}%
├── 1M: {change} | 3M: {change} | 6M: {change}
└── M2 leads crypto ~10-12 weeks → {implication}

Stablecoin Flows: {weekly net} ({direction})
DXY: {val} ({direction})

LIQUIDITY VERDICT: {SUPPORTIVE / NEUTRAL / HEADWIND}
{One sentence}
```

Data source: Nimbus sync for headline numbers. Full table detail comes 
from manual screenshots in /notebook (user provides 2 screenshots daily).

### [Chain] — on-chain metrics

```
⛓️ CHAIN DATA — {time} UTC

DeFi TVL: ${val}B ({change 24h})
├── Solana TVL: ${val}B ({change})
├── Ethereum TVL: ${val}B ({change})
└── Top mover: {protocol} ({change})

Active Addresses (24h):
├── Solana: {val} ({change vs 7d avg})
└── Ethereum: {val} ({change vs 7d avg})

Stablecoin Supply: ${val}B ({weekly change})
Gas: SOL {val} | ETH {val} gwei
```

Data source: DeFiLlama (auto), CoinGecko (auto).

---

## BUTTON 2: 🐋 SIGNALS (analysis per watchlist token)

Sub-buttons: [Market] [Whale] [Discovery] [Unlocks]

### [Market] — market structure indicators

```
📊 MARKET STRUCTURE — {time} UTC

BTC Dominance: {val}% ({rising/falling/flat} — {implication})
Fear & Greed: {val} ({FEAR/NEUTRAL/GREED})
VIX: {val} ({LOW/ELEVATED/HIGH})
CBBI: {val}/100 ({zone description})

FUNDING RATES (top watchlist tokens):
├── BTC: {val}% ({longs/shorts paying})
├── SOL: {val}%
├── BONK: {val}%
└── JUP: {val}%

LIQUIDATIONS (24h):
├── Total: ${val}M ({longs/shorts dominant})
├── Largest: ${val}M {LONG/SHORT} on {exchange}
└── Watchlist impact: {summary}

OPEN INTEREST:
├── BTC: ${val}B ({change 24h})
└── SOL: ${val}M ({change 24h})

MARKET VERDICT: {RISK-ON / NEUTRAL / RISK-OFF / CAPITULATION}
{One sentence — e.g. "Negative funding + fear = contrarian buy zone"}
```

Data source: 
- F&G: alternative.me ✅ auto
- VIX: Nimbus sync ✅ auto  
- CBBI: Nimbus sync ✅ auto
- BTC Dominance: ❌ BUILD (CoinGecko free API)
- Funding rates: ❌ BUILD (Coinglass free tier)
- Liquidations: ❌ BUILD (Coinglass free tier)
- Open interest: ❌ BUILD (Coinglass free tier)

### [Whale] — smart money per watchlist token

```
🐋 WHALE ACTIVITY — {time} UTC

SunFlow (multi-timeframe):
{Latest SunFlow data for watchlist tokens}

Smart Money Summary:
├── BTC: {accumulation/distribution} — {detail}
├── SOL: {status}
├── BONK: {status}
└── JUP: {status}
```

Data source: SunFlow Telegram scrape ✅ auto.

### [Discovery] — new tokens bubbling up

```
🔍 DISCOVERY — {time} UTC

EMERGING (3+ YouTube mentions in 48h):
🆕 FET — 5 mentions (aixbt, Coin Bureau, CryptosRUs, Bankless, All-In)
   Sentiment: 4 bullish, 1 neutral
   Narrative: AI infrastructure, Bitwise ETF filing
   Status: ⏳ Auto deep dive queued

🆕 ONDO — 3 mentions (Real Vision, InvestAnswers, Bitcoin Magazine)
   Sentiment: 3 bullish
   Narrative: RWA tokenisation, Treasury yields
   Status: ✅ Deep dive complete — scored 72

WATCHING (2 mentions):
👀 ASTER — 2 mentions (but unlock pressure flagged)
👀 VIRTUAL — 2 mentions (AI agent infrastructure)

GRADUATED TO WATCHLIST:
✅ ONDO (scored 72) — added {date}
```

Data source: YouTube analyses DB — ❌ BUILD (count token mentions 
across all analyses, flag at 3+ threshold, auto-trigger deep dive).

### [Unlocks] — token unlock calendar

```
🔓 UPCOMING UNLOCKS — watchlist tokens

⚠️ WITHIN 14 DAYS:
  {TOKEN}: {amount} ({pct}% of supply) — {date}
  
📅 WITHIN 30 DAYS:
  {TOKEN}: {amount} ({pct}% of supply) — {date}

✅ NO MAJOR UNLOCKS:
  BTC, BONK — no scheduled unlocks
```

Data source: Tokenomist.ai scrape ✅ auto.

---

## BUTTON 3: 🔥 FIERY EYES (synthesis + decision)

Sub-buttons: [Morning] [Evening] [Notebook] [Cycle]

### [Morning] — latest morning brief
Returns the most recent morning brief from DB (sent at 06:00 UTC).

### [Evening] — latest evening review  
Returns the most recent evening review from DB (sent at 20:00 UTC).

### [Notebook] — compilation for Opus paste
Triggers /notebook command (see NOTEBOOK section below).

### [Cycle] — THE KEY SCREEN: should I be in or out?

```
🔄 CYCLE ASSESSMENT — {time} UTC

━━━ DECISION LAYERS ━━━

1️⃣ BUSINESS CYCLE: {SPRING/SUMMER/AUTUMN/WINTER}
   → {implication for allocation}

2️⃣ LIQUIDITY: {EXPANDING/STALL/CONTRACTING}
   → {implication}

3️⃣ BTC CYCLE: {phase} — ~{pct}% complete
   ├── Time from bottom: {X months}
   ├── Time from halving: {X months}  
   ├── CBBI: {val}/100
   ├── Est. bottom window: Aug-Nov 2026
   └── → {implication}

4️⃣ MARKET STRUCTURE:
   ├── BTC Dominance: {val}% ({trend}) → {alt season?}
   ├── F&G: {val} ({label})
   ├── VIX: {val}
   ├── Funding: {bias}
   └── → {implication}

5️⃣ MACRO: {TAILWIND/NEUTRAL/HEADWIND}
   └── → {one line}

━━━ VERDICT ━━━

REGIME: {ACCUMULATE / HOLD / CAUTIOUS / DE-RISK}
CONVICTION: {1-10}
MAX ALLOCATION: {pct}%
BIAS: {BTC > SOL > alts / rotate to alts / defensive}

PER TOKEN:
🟢 BTC  — {ACCUMULATE/HOLD/REDUCE} — {one line reason}
🟢 SOL  — {status} — {reason}
🟡 JUP  — {status} — {reason}
🟡 BONK — {status} — {reason}
🟡 RENDER — {status} — {reason}
🔴 HYPE — {status} — {reason}
{etc for all watchlist tokens}

⚠️ KEY RISKS:
- {risk 1}
- {risk 2}
```

Data source: Composite of ALL other data. This is the synthesis screen.
Business cycle + CBBI + BTC cycle from Nimbus sync. Per-token from 
Signals. Updated after each /notebook Opus synthesis.

---

## BUTTON 4: 💼 PORTFOLIO (positions + research)

Sub-buttons: [Positions] [Deep Dive] [Trades] [Risk]

### [Positions] — current holdings

```
💼 POSITIONS — {time} UTC

S WALLET (Jingubang automated):
├── BONK: 54.08M — ${val} ({pnl}%)
├── SOL reserve: 0.0145 — ${val}
├── USDC: $0.00
└── Jingubang: Asset=BONK 100% | Erlang={status} | Mode={mode}
    Last signal: {signal} at {time}

H WALLET (manual):
├── {TOKEN}: {amount} — ${val} ({pnl}%)
├── {TOKEN}: {amount} — ${val} ({pnl}%)
└── USDC: ${val}

TOTAL CRYPTO: ${val}
```

Data source: 
- S wallet: Jingubang app.py (auto, reads on-chain balances)
- H wallet: ❌ needs manual config or on-chain read

### [Deep Dive] — research library

Triggers /deepdive command. Shows scorecard:

```
📚 DEEP DIVE LIBRARY — 12 tokens

 # │ Token   │ Score │ Rating       │ EV
 1 │ BTC     │ 90    │ EXCEPTIONAL  │ 2.85x
 2 │ SOL     │ 82    │ STRONG       │ 4.9x
 3 │ HYPE    │ 79    │ STRONG       │ 3.1x
 4 │ JUP     │ 78    │ STRONG       │ 12.7x
 5 │ RENDER  │ 72    │ WATCHLIST    │ 6.5x
 6 │ SUI     │ 71    │ WATCHLIST    │ 6.6x
 7 │ BONK    │ 64    │ WATCHLIST    │ 5.6x
 8 │ PUMP    │ 55    │ SPECULATIVE  │ 6.5x
 9 │ USELESS │ 45    │ PROVISIONAL  │ TBD
10 │ PENGU   │ 44    │ SPECULATIVE  │ 3.5x
11 │ FARTCOIN│ 25    │ AVOID        │ NCRA
12 │ DEEP    │ TBD   │ RESEARCH     │ queued

Last updated: {date} via Opus synthesis
Score changes: JUP 68→78, BONK 52→64
```

Use /deepdive {TOKEN} for full research.
Use /deepdive {TOKEN} full for complete document.

### [Trades] — Jingubang performance from Google Sheets

```
📊 TRADE LOG — Jingubang

Last 5 trades:
{date} | BONK | LONG  | ${entry} → ${exit} | {+/-}%
{date} | SOL  | EXIT  | ${entry} → ${exit} | {+/-}%
{etc}

PERFORMANCE:
├── Win rate: {pct}%
├── Total P&L: ${val} ({pct}%)
├── Best trade: {token} +{pct}%
├── Worst trade: {token} -{pct}%
├── Profit factor: {val}
└── Trades this month: {count}
```

Data source: Google Sheets ✅ auto (Jingubang logs all trades).

### [Risk] — allocation and concentration

```
⚠️ RISK TABLE — {time} UTC

ALLOCATION:
├── S Wallet: ${val} ({pct}% of crypto)
├── H Wallet: ${val} ({pct}% of crypto)
└── Cash (USDC): ${val} ({pct}%)

CONCENTRATION:
├── Largest position: {TOKEN} at {pct}% — {OK/WARNING}
├── Top 3 positions: {pct}% of portfolio
└── Dry powder: {pct}% — target 30%

RULES CHECK:
├── Max per Hatchling (<$5M): 10% — {PASS/FAIL}
├── Max per Runner ($5-50M): 30% — {PASS/FAIL}
├── Dry powder >30%: {PASS/FAIL}
└── Max 2 Hatchlings: {PASS/FAIL}
```

---

## BUTTON 5: ⚙️ SYSTEM

Sub-buttons: [Health] [YT Health] [Costs] [Help]

### [Health] — full system health across ALL sections

```
⚙️ SYSTEM HEALTH — {time} UTC

📊 AUTO DATA:
  YouTube     {✅/⚠️/❌} {count} videos today
  Prices      {✅/⚠️/❌} Last update {time}
  F&G         {✅/⚠️/❌} {value}
  Nimbus sync {✅/⚠️/❌} Last sync {time}
  SunFlow     {✅/⚠️/❌} Last update {time}
  DeFi TVL    {✅/⚠️/❌} Last update {time}
  Unlocks     {✅/⚠️/❌} Last update {time}
  Jingubang   {✅/⚠️/❌} Bot status {running/stopped}

📸 MANUAL DATA:
  Nimbus screenshot    {✅ uploaded today / ❌ NOT UPLOADED}
  Liquidity screenshot {✅ uploaded today / ❌ NOT UPLOADED}
  Last uploaded: {date time}

🚧 NOT BUILT:
  Funding rates    ❌ Coinglass needed
  Liquidations     ❌ Coinglass needed
  Open Interest    ❌ Coinglass needed
  BTC Dominance    ❌ CoinGecko needed
  Discovery        ❌ YouTube mention counter needed
  Signal accuracy  ❌ Tracking needed
```

### [YT Health] — YouTube channel scan status
Existing health check (runs at 06:00, 14:00, 22:00 UTC).
Shows ✅/❌ per video, silent channels, scan counts.

### [Costs] — API spend

```
💰 API COSTS — {month}

Claude API: ${val} (budget: $30)
├── Sonnet (YouTube): ${val}
├── Haiku (YouTube): ${val}  
├── Haiku (Intel Briefing): ${val}
└── Other: ${val}

Hetzner VPS: €7.19 ($8)
Total: ~${val}/month
```

### [Help] — all commands listed

```
📖 COMMANDS

/deepdive [TOKEN]     — research summary
/deepdive [TOKEN] full — full document
/notebook             — daily compilation for Opus
/analyse [YouTube URL] — on-demand video analysis
/status               — system health
/costs                — API spend
```

---

## /NOTEBOOK COMMAND

Triggers manually or auto-fires at 20:00 UTC.

Compiles into one paste-ready block:

```
══════════════════════════════════════════
📓 FIERY EYES NOTEBOOK — {Day Date Month Year}
══════════════════════════════════════════

PROMPT FOR CLAUDE OPUS:
You are my portfolio intelligence analyst for Fiery Eyes.
Below is today's YouTube intelligence from {X} videos 
across {Y} channels, plus my current market data, cycle 
position, and holdings.

TASKS:
1. SYNTHESIS: What did today's voices AGREE on? Where did 
   they DISAGREE? What are the unresolved tensions?

2. WATCHLIST SCORES: Based on today's intel, should any 
   scores change? Current:
   BTC: 90 | SOL: 82 | HYPE: 79 | JUP: 78 | RENDER: 72
   SUI: 71 | BONK: 64 | PUMP: 55 | PENGU: 44
   Format: [TOKEN] [OLD] → [NEW] [REASON]
   Only change if today's intel justifies it.

3. POSITION CALL: Based on all layers (business cycle, 
   liquidity, BTC cycle, market structure, macro) — should 
   I be IN or OUT? Conviction 1-10.
   Current regime: {REGIME}
   Erlang: {ATTACK/DEFEND}
   Nimbus confidence: {CONF}/100
   BTC cycle: ~{PCT}% complete
   CBBI: {VAL}

4. DEEP DIVE FLAGS: Any token where today's intel 
   materially changes the thesis? Quote the specific 
   video and claim.

5. DISCOVERY: Any non-watchlist token mentioned 3+ times 
   that deserves investigation?

6. ACTIONS: 0-3 specific things to do before tomorrow. 
   "No action" is valid.

══════════════════════════════════════════
📊 MARKET STATE
══════════════════════════════════════════

BTC: ${price} | SOL: ${price}
Erlang: {ATTACK/DEFEND} | Mode: {WUKONG/PIGSY/SANDY}
Nimbus Confidence: {val}/100
Season: {SPRING/SUMMER/AUTUMN/WINTER}
CBBI: {val} | F&G: {val} ({label}) | VIX: {val}
US Net Liq: ${val}T ({status}) | Global M2: ${val}T
BTC Cycle: ~{pct}% | Bottom est: Aug-Nov 2026
BTC Dominance: {val}% ({trend})

══════════════════════════════════════════
💼 POSITIONS
══════════════════════════════════════════

S WALLET (Jingubang):
BONK: 54.08M — ${val}
Jingubang: Asset=BONK 100% | Erlang={status} | Mode={mode}

H WALLET:
{current positions}

══════════════════════════════════════════
📺 TODAY'S YOUTUBE INTELLIGENCE ({X} videos)
══════════════════════════════════════════

--- VIDEO 1 ---
📺 {Channel} (Sonnet)
📅 {Date Time UTC}
🎬 "{Title}"

{Full analysis text from DB}

--- VIDEO 2 ---
{repeat for all videos}

══════════════════════════════════════════
END — PASTE EVERYTHING ABOVE INTO CLAUDE OPUS
══════════════════════════════════════════
```

---

## YOUTUBE OUTPUT — THREE MODES

### Mode 1: Individual alerts (throughout the day)
When a Sonnet video is analysed → send to Telegram immediately.
Haiku videos → only send if mentions watchlist token with >7/10 conviction.
Uses the gold standard format (segment-by-segment, bold speakers, etc).

### Mode 2: YouTube button (on-demand dashboard)
📊 Intel → [YouTube] shows summary with themes, highlights, consensus.
NOT full analyses — just the dashboard overview.

### Mode 3: Notebook compilation (evening)
/notebook at 20:00 UTC or on-demand.
ALL full analyses in one paste-ready block with Opus prompt.

---

## SCHEDULE

```
05:45  Nimbus sync from Jingubang
06:00  Morning Brief + YouTube Health
06:00  Intel Briefing (YouTube+SunFlow → Haiku summary)
10:00  Intel Briefing  
14:00  Intel Briefing + YouTube Health
18:00  Intel Briefing
20:00  /notebook auto-send + Evening Review
22:00  Intel Briefing + YouTube Health

Every 2h: YouTube scan (50 channels, store to DB, send Sonnet alerts)
Every 4h: Watchlist prices + market structure
```

---

## WHAT NEEDS BUILDING (priority order)

### PHASE 1 — THIS SESSION:
1. /notebook command with baked prompt
2. Button restructure (5 main + all sub-menus)
3. YouTube button dashboard (summary, not full analyses)
4. Cycle screen (composite decision framework)
5. Health dashboard (auto + manual data status)

### PHASE 2 — NEXT SESSION:
6. BTC Dominance tracking (CoinGecko free)
7. Coinglass integration (funding, liquidations, OI)
8. Discovery counter (YouTube mention tracking)
9. Business cycle auto-assessment (FRED PMI, claims, GDP)
10. DXY tracking

### PHASE 3 — LATER:
11. Signal accuracy tracking
12. H wallet on-chain position reading
13. Portfolio risk auto-calculation
14. Trade feedback loop (Google Sheets → signal weighting)

---

## DATA SOURCES — COMPLETE MAP

| Data | Source | Status | Cost |
|------|--------|--------|------|
| YouTube (50 channels) | RSS + Sonnet/Haiku | ✅ Auto | ~$46/mo |
| BTC/SOL prices | CoinGecko | ✅ Auto | Free |
| Fear & Greed | alternative.me | ✅ Auto | Free |
| Nimbus (macro/rates/liq) | Jingubang sync 05:45 | ✅ Auto | Free |
| CBBI | Nimbus sync | ✅ Auto | Free |
| VIX | Nimbus sync | ✅ Auto | Free |
| US Net Liquidity headline | FRED + Nimbus | ✅ Auto | Free |
| SunFlow whales | Telegram scrape | ✅ Auto | Free |
| DeFi TVL | DeFiLlama | ✅ Auto | Free |
| Token unlocks | Tokenomist scrape | ✅ Auto | Free |
| Jingubang trades | Google Sheets | ✅ Auto | Free |
| Erlang + Mode + Trend | Jingubang webhook | ✅ Auto | Free |
| Intel Briefing | YouTube+SunFlow→Haiku | ✅ Auto | ~$3/mo |
| Nimbus full detail | TradingView screenshot | 📸 Manual | Free |
| Liquidity tables | TradingView screenshot | 📸 Manual | Free |
| BTC Dominance | CoinGecko | ❌ Build | Free |
| Funding rates | Coinglass | ❌ Build | Free |
| Liquidations | Coinglass | ❌ Build | Free |
| Open interest | Coinglass | ❌ Build | Free |
| DXY | Yahoo Finance | ❌ Build | Free |
| Business cycle (PMI etc) | FRED | ❌ Build | Free |
| Discovery counter | YouTube DB | ❌ Build | Free |
| Signal accuracy | Internal | ❌ Build | Free |
| H wallet positions | On-chain or manual | ❌ Build | Free |
