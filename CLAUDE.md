# CLAUDE.md — Fiery Golden Eyes v4 Context File

**Last updated:** 2026-03-03
**For:** Claude Code / Claude API sessions working on this codebase

---

## WHAT THIS PROJECT IS

Fiery Golden Eyes ("Huoyan Jinjing") is a crypto intelligence and macro monitoring system running on Hetzner VPS. It monitors chain adoption metrics, macro indicators, YouTube/X/Discord intelligence, and smart money flows to inform infrastructure-level investment decisions on Solana. Delivered via Telegram.

The user is an active crypto investor focused on Solana infrastructure. The system serves portfolio conviction — knowing WHEN to be heavy in SOL, WHEN to rotate, WHEN to de-risk.

---

## STRATEGIC PIVOT (2026-03-03)

**FROM:** Meme coin discovery and position management (high-effort, high-noise)
**TO:** Macro intelligence and chain adoption monitoring (high-conviction, infrastructure focus)

**Portfolio target:**
- 50% SOL
- 25% Pump.fun token
- 25% JUP (or similar infrastructure)
- Small allocation for rare high-conviction meme plays (8+ convergence only)

**Why:** Memes are a distraction. Infrastructure tokens (SOL, JUP, Pump.fun) win regardless of which meme pumps. The system should tell the user whether Solana is winning, and alert when something changes.

---

## THE STRATEGY (updated 2026-03-03)

1. MONITOR: Chain adoption metrics across Solana vs competitors (Artemis, DeFiLlama, Token Terminal)
2. INTELLIGENCE: YouTube summaries, X sentiment, Discord alpha (InvestAnswers, Jupiter, Solana)
3. MACRO: BTC dominance, SOL/BTC ratio, funding rates, stablecoin flows, regime detection
4. HOLDINGS: Track SOL/JUP/Pump.fun specific health metrics
5. MEME RADAR: Keep convergence detection but ONLY alert on 8+ (strong conviction)

---

## CHAIN ADOPTION MONITORING (NEW — PRIORITY 1)

### Data Sources (all free tier)

| Source | What it provides | Frequency |
|--------|-----------------|-----------|
| Artemis (API) | Active addresses, txn count, fees, DEX vol by chain | Daily |
| DeFiLlama (API) | TVL, DEX volume, stablecoin flows by chain | Daily |
| Token Terminal (free) | Protocol revenue, P/S ratios | Weekly |
| Dune Analytics (free) | Custom queries: JUP volume share, Pump.fun metrics | Daily |
| CoinGlass (free) | Funding rates, OI, liquidations | Every 4h |

### Chain Scorecard (daily)

Track Solana vs ETH vs Base vs Sui vs others:
- Active addresses (daily + 7d trend)
- Transaction count
- DEX volume
- TVL (total + net flows)
- Fee revenue (proxy for real usage)
- Developer activity (new contracts deployed)
- Stablecoin inflows/outflows (especially USDC on Solana)

### Holdings Health (every 4h)

**SOL:** Price, SOL/BTC ratio trend, staking APY, network uptime, TPS
**JUP:** Price, 24h volume through aggregator, market share vs Raydium/Orca, fee revenue
**Pump.fun:** Daily launches, graduation rate (% that hit Raydium), fee revenue, market share of new token launches on Solana

### Macro Regime (enhanced)

- BTC price + 7d/30d trend
- BTC dominance trend (rising = risk-off, falling = alt season)
- SOL/BTC ratio (is SOL outperforming or underperforming?)
- Total crypto market cap
- Funding rates across major exchanges (neutral/greedy/fearful)
- Open interest trends
- Stablecoin total supply + flows
- DXY / rates / macro backdrop from YouTube intelligence

---

## INTELLIGENCE LAYER

### YouTube (NEEDS FIX — see Phase 7)

Current: 42 channels monitored via RSS, transcripts via cookies
Problem: Most transcripts failing silently (datacenter IP blocked), summaries not appearing in Telegram

**Priority channels for macro focus:**
- InvestAnswers (James) — SOL ecosystem, macro cycles, portfolio strategy
- Real Vision — macro, institutional flows, DeFi deep dives
- Coin Bureau — chain comparisons, infrastructure analysis
- Benjamin Cowen (Into The Cryptoverse) — on-chain metrics, ratio analysis
- Raoul Pal — macro thesis, institutional adoption
- Lyn Alden — macro/liquidity framework
- The Breakdown (NLW) — daily macro news
- Bankless — DeFi ecosystem, chain analysis
- Scott Melker (Wolf of All Streets) — market analysis
- CryptoCon — Bitcoin cycle analysis
- Crypto Crew University — TA/macro

**Fix required:**
1. Audit current pipeline — how many videos got full summaries vs title-only?
2. Add residential proxy for transcript downloads ($5-10/mo)
3. Expand channel list with macro-focused channels
4. Verify Claude Haiku summary quality — are summaries actionable?
5. Ensure summaries appear in Telegram AND nightly digest

### X/Twitter (181 accounts via Grok)

Keep all 181 accounts. Shift analysis focus from meme mentions to:
- Chain adoption narratives
- Institutional flow signals
- SOL ecosystem developments
- JUP/Pump.fun product updates
- Macro sentiment

### Discord (NEW — Phase 8)

| Server | Why | What to monitor |
|--------|-----|----------------|
| InvestAnswers | James's portfolio moves, macro analysis, community alpha | Alpha/calls channels, James's posts |
| Jupiter | Product updates, fee changes, new features | Announcements, dev updates |
| Solana Tech/Superteam | Developer activity, chain health | General, announcements |
| Pump.fun | New features, graduation metrics | Announcements |
| Marinade/Jito | Staking ecosystem health | Updates |

**Implementation:** Discord bot listening to specific channels, piping to DB, Claude summarizes key takeaways into Huoyan pulse.

---

## TELEGRAM CHANNELS

| Channel | Purpose | Volume |
|---------|---------|--------|
| 🔥 H-Fire | Strong convergence alerts (8+ only), major macro shifts | 0-2/day |
| 📡 Huoyan | 4-hourly intelligence pulse (chain scorecard, holdings, macro, intelligence) | 6/day |
| 🔧 System | Health monitoring, errors, daily system check | 1-2/day |
| 🤖 Jingubang S | Ocean Terminal bot (separate, existing) | Varies |

### Huoyan Pulse Structure (NEW FORMAT — every 4 hours):
```
📡 HUOYAN PULSE — [time] UTC

📊 Macro Regime
  BTC: $XX,XXX (7d: +X%) | Dominance: XX.X%
  SOL/BTC: 0.XXXX (trend: ↑/↓/→)
  Funding: neutral/greedy/fearful
  Stablecoin flows: +$XM into Solana this week

🔗 Chain Scorecard (daily snapshot)
  Solana: Active addrs XXK (7d: +X%) | DEX vol $X.XB | TVL $X.XB | Fees $XM
  vs ETH: [gaining/losing] share
  vs Base: [gaining/losing] share
  Trend: [Solana accelerating/steady/decelerating]

💰 Holdings Health
  SOL: $XX.XX (7d: X%) | SOL/BTC: 0.XXXX | Staking: X.X% APY
  JUP: $X.XX (7d: X%) | Aggregator vol: $XM | Share: XX%
  Pump.fun: XX launches today | Grad rate: X% | Fees: $XK

📺 Intelligence Summary
  [Top YouTube summaries — key macro narratives]
  [Top X signals — ecosystem developments]
  [Discord alpha — InvestAnswers, Jupiter updates]

🔥 Meme Radar (strong conviction only)
  [8+ convergence alerts only, otherwise "Quiet — no strong signals"]
```

---

## WHAT'S RUNNING ON VPS RIGHT NOW

- Telegram bot with 4 channels
- 4-hourly Huoyan pulse
- Nightly strategist report (03:00 UTC)
- YouTube intelligence (partially broken — needs fix)
- Scanner with Quality Gate
- Health score engine (Volume + Price signals live)
- Regime monitoring
- KOL wallet tracking (22 wallets via Helius)
- Grok X polling (181 accounts)
- Smart money feed polling (4 sources)
- Social verification checker
- Wallet quality classifier
- Convergence detector
- GMGN weekly scraper (Sundays)
- Bot commands (/health, /status, /regime, /shadow, etc)
- systemd fiery-eyes-v2.service

---

## PHASE PRIORITIES (updated 2026-03-03)

Phase 1-6 (Done): Full meme discovery system built and validated
Phase 7 (THIS WEEK — PRIORITY 1): YouTube pipeline fix — audit, residential proxy, expand macro channels, verify summaries
Phase 8 (THIS WEEK — PRIORITY 2): Chain adoption dashboard — Artemis + DeFiLlama + CoinGlass APIs, chain scorecard, holdings tracker
Phase 9 (NEXT WEEK): Discord monitoring — InvestAnswers, Jupiter, Solana Tech
Phase 10 (NEXT WEEK): Huoyan pulse v2 — new macro-focused format
Phase 11 (WEEK 3): Enhanced macro regime — SOL/BTC ratio, funding rates, stablecoin flows

---

## MEME SYSTEM (MAINTENANCE MODE)

The full meme discovery system is built and running. Keep it in maintenance mode:
- Convergence detector: raise threshold from 3+ to 8+ for alerts
- Smart money polling: keep running, feeds into convergence
- Wallet fleet: keep GMGN weekly refresh running
- Health scores: keep running for any tokens that hit 8+ convergence
- Quality Gate: keep running
- KOL wallets: keep 22 tracked, no expansion needed
- Individual KOL alerts: suppress unless convergence fires

Only surface meme opportunities with STRONG convergence (8+ weighted score).

---

## KEY TECHNICAL DETAILS

- VPS: Hetzner CPX22, 89.167.83.21, Ubuntu 24
- Database: PostgreSQL (fiery_eyes)
- APIs: Helius (1M free credits), CoinGecko Demo, Telegram, Claude (Haiku), Grok (X search)
- NEW APIs: Artemis (free), DeFiLlama (free), CoinGlass (free), Token Terminal (free)
- Grok config: grok_monitor_config.csv — 181 accounts (64 HIGH @ 30min, 117 MEDIUM @ 2hr)
- Smart money X feeds: @StalkHQ, @kolscan_io, @gmaborabot, @SunFlowSolana
- GitHub: Journevo/fiery-eyes-vps.git
- Service: systemd fiery-eyes-v2.service

---

## IMPORTANT RULES

- Always list key data/results in chat text, not just in downloaded files
- Social verification FIRST before any analysis
- 15-20 minute cognitive load limit per session
- ~$15-25/mo operating cost target (added residential proxy budget)
- YouTube summaries MUST be actionable — thesis, key calls, risks
- Chain data presented as trends, not just snapshots
- SOL/BTC ratio is the key metric — shows relative strength
- Meme alerts only on 8+ convergence (strong conviction)

---

## COST PROJECTIONS

| Item | Current | After Pivot |
|------|---------|-------------|
| Hetzner VPS | €7.19/mo ($8) | €7.19/mo ($8) |
| Claude API | ~$5-10/mo | ~$5-10/mo |
| Grok API | ~$2-3/mo | ~$2-3/mo |
| Residential proxy | $0 | ~$5-10/mo (NEW) |
| All data APIs | $0 | $0 (all free tier) |
| **TOTAL** | **~$15-20/mo** | **~$20-30/mo** |
