# CLAUDE.md — Fiery Golden Eyes v5.1
**Updated:** 2026-03-14 | **Full spec:** FIERY_EYES_V5_BUILD.md (READ THIS FIRST)

## SYSTEM: Crypto intelligence engine. 4 core tokens (JUP, HYPE, RENDER, BONK) + BTC cycle + ISA proxies. Advisory only — user executes manually. Jingubang (separate VPS 134.209.176.180) auto-trades SOL.

## CONTEXT: BTC peaked $126K Oct 2025, now ~$70.8K (-44%). Bear 42% complete. Bottom est. Aug-Nov 2026. Iran war active, oil $100. Funding negative 14d. Macro: early expansion. Cycle: ~6mo pain remains.

## BUILD ORDER (lean-first, prove edge then expand):
Phase 1 (Week 1-2): Tasks 1-7 — core infrastructure
  1. BTC cycle tracker (30min)
  2. Watchlist price tracker — 4 tokens + BTC + MSTR/COIN (45min)
  3. On-chain large swap detection via DexScreener/Birdeye — NOT Grok (60min)
  4. FRED liquidity tracker — copy functions from Jingubang app.py (30min)
  5. ONE daily intelligence report (60min)
  6. Recommendation ledger + full state snapshots (30min)
  7. Portfolio tracker /bought /sold /portfolio (30min)

Phase 2 (Week 3-4): Tasks 8-12 — add depth
Phase 3 (Week 5+): Tasks 13-20 — expand

## KEY RULES:
- Read FIERY_EYES_V5_BUILD.md for ALL implementation details
- Start Task 1, test with real data, then Task 2, etc.
- NEVER show empty Telegram sections
- Every projection needs DOWNSIDE case (BTC to $50K)
- Whale buy on <$200M MCap token = "watch for pullback" NOT entry signal
- Feed synthesis engine VELOCITY (rate of change) not static numbers
- Full state snapshot on every recommendation
- Bear <60% = max 50% deployed. 30% dry powder always.
- Git commit after each working task. Restart daemon after changes.
