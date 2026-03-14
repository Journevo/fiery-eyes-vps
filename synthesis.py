"""Synthesis Engine — Task 13 of Fiery Eyes v5.1

Daily Claude Sonnet call connecting all 7 analytical layers.
Input: all data WITH velocity/rate-of-change.
Output: narratives, causal chains, contradictions, actionable insight.

Cost: ~$0.05-0.10/call = ~$2-3/month on Sonnet.
"""

import json
import anthropic
import requests
from datetime import datetime, timezone
from config import ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("synthesis")

SONNET_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Data collection — gather ALL layers for the prompt
# ---------------------------------------------------------------------------
def collect_all_layers() -> dict:
    """Collect data from all analytical layers for synthesis."""
    layers = {}

    # Layer 2: BTC Cycle
    try:
        from btc_cycle import fetch_btc_price, calculate_cycle
        btc_price = fetch_btc_price()
        if btc_price:
            layers["btc_cycle"] = calculate_cycle(btc_price)
    except Exception as e:
        log.error("Layer 2 (BTC cycle) failed: %s", e)

    # Layer 4: Liquidity
    try:
        from liquidity import run_liquidity_tracker
        layers["liquidity"] = run_liquidity_tracker()
    except Exception as e:
        log.error("Layer 4 (liquidity) failed: %s", e)

    # Layer 5: Sentiment / Market Structure
    try:
        from market_structure import run_market_structure
        layers["market_structure"] = run_market_structure()
    except Exception as e:
        log.error("Layer 5 (market structure) failed: %s", e)

    # Layer 6: Watchlist fundamentals
    try:
        from watchlist import fetch_prices
        layers["watchlist"] = fetch_prices()
    except Exception as e:
        log.error("Layer 6 (watchlist) failed: %s", e)

    # Layer 6b: DeFi market
    try:
        from defi_llama import collect_defi_data
        layers["defi"] = collect_defi_data()
    except Exception as e:
        log.error("Layer 6b (DeFi) failed: %s", e)

    # Layer 6c: Supply flow
    try:
        from supply_flow import calc_hype_supply_flow, calc_pump_cliff, get_distribution_penalties
        layers["supply"] = {
            "hype": calc_hype_supply_flow(),
            "pump_cliff": calc_pump_cliff(),
            "penalties": get_distribution_penalties(),
        }
    except Exception as e:
        log.error("Layer 6c (supply) failed: %s", e)

    # Smart money signals (last 24h)
    try:
        from social.grok_poller import get_recent_x_signals
        signals = get_recent_x_signals(hours=24, min_strength="medium")
        layers["smart_money"] = [
            {"source": s["source_handle"], "type": s["parsed_type"],
             "token": s["token_symbol"], "amount": s["amount_usd"],
             "strength": s["signal_strength"], "category": s.get("signal_category")}
            for s in signals[:15]
        ]
    except Exception as e:
        log.error("Smart money signals failed: %s", e)

    # YouTube intel
    try:
        from youtube_intel import get_recent_youtube_intel
        yt = get_recent_youtube_intel(hours=48)
        layers["youtube"] = {
            "videos": yt["videos"],
            "watchlist_mentions": [
                {"symbol": w["symbol"], "mentions": w["total_mentions"],
                 "bullish": w["bullish"], "bearish": w["bearish"],
                 "conviction": w["weighted_conviction"],
                 "channels": w["channels"][:3]}
                for w in yt.get("watchlist_mentions", [])
            ],
            "convergence": yt.get("convergence", []),
        }
    except Exception as e:
        log.error("YouTube intel failed: %s", e)

    # On-chain large swaps (last 24h)
    try:
        swaps = execute("""
            SELECT token, direction, amount_usd, pct_of_mcap, alert_type
            FROM large_swaps WHERE timestamp > NOW() - INTERVAL '24 hours'
            ORDER BY amount_usd DESC LIMIT 5
        """, fetch=True)
        if swaps:
            layers["large_swaps"] = [
                {"token": r[0], "direction": r[1], "amount": r[2],
                 "pct_mcap": r[3], "type": r[4]}
                for r in swaps
            ]
    except Exception as e:
        log.error("Large swaps query failed: %s", e)

    return layers


def build_synthesis_prompt(layers: dict) -> str:
    """Build the synthesis prompt from all collected layers."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Format each layer as context
    sections = []

    # BTC Cycle
    btc = layers.get("btc_cycle", {})
    if btc:
        sections.append(
            f"BTC CYCLE: Price ${btc.get('btc_price', 0):,.0f}, peak ${btc.get('peak_price', 0):,} ({btc.get('peak_date', '')}), "
            f"drawdown {btc.get('drawdown_pct', 0)}%, bear progress {btc.get('bear_progress_pct', 0)}%, "
            f"~{btc.get('days_remaining', 0)} days to estimated bottom. "
            f"Historical scenarios: -52% = $60K, -60% = $50K, -77% = $29K."
        )

    # Liquidity
    liq = layers.get("liquidity", {})
    if liq:
        sections.append(
            f"LIQUIDITY: US Net Liq ${liq.get('us_net_liq', 0)}T, regime {liq.get('fred_regime', 'UNKNOWN')} "
            f"(slope {liq.get('fred_slope', 0):+.1f}%), Global ${liq.get('global_net_liq', 0)}T, "
            f"M2 ${liq.get('global_m2', 0)}T, DXY {liq.get('dxy', 0)}, "
            f"M2 lag {liq.get('m2_lag_days', 0)}d ({liq.get('m2_lag_status', '')}), "
            f"Alignment: {liq.get('alignment', '')}."
        )

    # Market Structure
    mkt = layers.get("market_structure", {})
    if mkt:
        oi = mkt.get("oi", {})
        funding = mkt.get("funding", {})
        fg = mkt.get("fear_greed", {})
        ls = mkt.get("long_short", {})
        sections.append(
            f"MARKET STRUCTURE: BTC OI ${oi.get('oi_usd', 0)/1e9:.1f}B (Binance), "
            f"funding {funding.get('current_pct', 0):+.4f}% ({funding.get('streak_days', 0):.0f}d {funding.get('streak_direction', '')}), "
            f"L/S ratio {ls.get('ratio', 0)}, "
            f"Fear & Greed {fg.get('value', 0)} ({fg.get('label', '')})."
        )

    # Watchlist
    wl = layers.get("watchlist", {})
    if wl:
        wl_lines = []
        for sym, d in wl.items():
            wl_lines.append(f"{sym}: ${d['price']}, {d['pct_from_ath']:+.0f}% ATH, {d['zone']}")
        sections.append("WATCHLIST: " + " | ".join(wl_lines))

    # DeFi
    defi = layers.get("defi", {})
    if defi:
        revs = defi.get("revenues", {})
        rev_parts = []
        for sym in ["HYPE", "JUP", "PUMP"]:
            r = revs.get(sym, {})
            if r.get("rev_24h"):
                rev_parts.append(f"{sym} ${r['rev_24h']:,.0f}/d (${r.get('annualised', 0):,.0f}/yr)")
        sections.append(
            f"DeFi: Total TVL ${defi.get('total_tvl', 0)/1e9:.1f}B, "
            f"SOL TVL ${defi.get('sol_tvl', 0)/1e9:.1f}B (#{defi.get('sol_tvl_rank', 0)}), "
            f"SOL DEX share {defi.get('sol_dex_share', 0)}%, "
            f"Stablecoins ${defi.get('total_stablecoins', 0)/1e9:.0f}B. "
            f"Revenue: {', '.join(rev_parts)}."
        )

    # Supply
    supply = layers.get("supply", {})
    if supply:
        hype = supply.get("hype", {})
        pump = supply.get("pump_cliff", {})
        sections.append(
            f"SUPPLY: HYPE net {'positive' if hype.get('net_positive') else 'negative'} "
            f"(buyback ${hype.get('daily_buyback_usd', 0):,.0f}/d vs emissions ${hype.get('daily_emission_usd', 0):,.0f}/d). "
            f"JUP zero emissions. PUMP cliff {pump.get('days_remaining', 0)}d (Jul 12, 41% unlock). "
            f"BONK 94% circ, burns only."
        )

    # Smart money
    sm = layers.get("smart_money", [])
    if sm:
        sm_lines = [f"{s['source']} {s['type']} ${s['token'] or '?'} "
                    f"{'$'+str(int(s['amount'])) if s.get('amount') else ''} [{s['strength']}]"
                    for s in sm[:8]]
        sections.append("SMART MONEY (24h): " + " | ".join(sm_lines))

    # YouTube
    yt = layers.get("youtube", {})
    if yt and yt.get("watchlist_mentions"):
        yt_lines = [f"{w['symbol']}: {w['mentions']} mentions, {w['bullish']} bullish, conv {w['conviction']:.0f}/10"
                    for w in yt["watchlist_mentions"][:5]]
        sections.append("YOUTUBE (48h): " + " | ".join(yt_lines))
        if yt.get("convergence"):
            for c in yt["convergence"]:
                sections.append(f"YT CONVERGENCE: ${c['symbol']} — {c['count']} channels bullish")

    # Large swaps
    swaps = layers.get("large_swaps", [])
    if swaps:
        swap_lines = [f"{s['token']} {s['direction']} ${s['amount']:,.0f} ({s['pct_mcap']:.2f}% MCap)"
                      for s in swaps]
        sections.append("LARGE SWAPS (24h): " + " | ".join(swap_lines))

    data_block = "\n\n".join(sections)

    prompt = f"""You are a senior crypto research analyst writing a morning briefing. Your reader holds positions in JUP, HYPE, RENDER, BONK on Solana, with BTC/SOL as benchmarks. They have 50%+ dry powder in USDC.

Date: {today}

COMPLETE SYSTEM STATE:
{data_block}

Write a briefing that answers FOUR questions. Write like Matt Levine — insightful, specific, opinionated. NOT bullet points. Flowing analytical prose.

1. WHAT IS HAPPENING
Write a 4-5 sentence narrative connecting the most important signals. Not a data dump — a STORY. What is the market doing and why? Connect macro (oil, Fed, liquidity) to crypto (fear, funding, whale behaviour). Example tone: "Despite Iran war pushing oil to $100 and Goldman raising PCE to 2.9%, crypto is showing unexpected resilience — BTC held $70K while equities compressed from 24x to 21x PE. The divergence between extreme retail fear (F&G 16) and aggressive whale accumulation ($14.3M into RENDER across all timeframes) is the widest this cycle."

2. MARKET IMPACT
What does this MEAN for crypto over the next 1-4 weeks? Connect the dots between data points. Identify the bull case AND bear case for the current setup. What is the market not pricing in?

3. WHAT TO DO
Specific actions mapped to the watchlist. For each relevant token:
- ACCUMULATE / HOLD / PATIENCE / REDUCE / AVOID
- WHY (cite the specific data: SunFlow conviction, smart money, supply dynamics)
- AT WHAT LEVEL (entry targets if applicable)
- THE RISK (what makes this wrong)

4. OPPORTUNITIES & RISKS
What is the market not seeing? Where is the asymmetric bet? What is the biggest risk to the portfolio right now?

CRITICAL: Be opinionated. "Both sides have merit" is useless. Take a position based on the data and explain why. If RENDER has conviction 9 from whales, SAY it's the best opportunity and explain the risk that makes you wrong.

Keep total output under 600 words. No headers with asterisks — use the section names above as plain text headers."""

    return prompt


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS synthesis (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            prompt_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            raw_output TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------
def call_synthesis(prompt: str) -> dict:
    """Call Claude Sonnet for synthesis. Returns parsed result."""
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY not set")
        return {"error": "No API key"}

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )

        output_text = response.content[0].text
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens

        # Sonnet pricing: $3/M input, $15/M output
        cost = (input_tokens * 3 / 1e6) + (output_tokens * 15 / 1e6)

        log.info("Synthesis complete: %d in / %d out tokens, $%.4f", input_tokens, output_tokens, cost)

        return {
            "output": output_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost,
        }
    except Exception as e:
        log.error("Synthesis API call failed: %s", e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def format_synthesis_telegram(output: str) -> str:
    """Format synthesis output for Telegram."""
    # Add header and trim if needed
    msg = f"🧠 <b>SYNTHESIS ENGINE</b>\n\n{output}"

    # Telegram limit is 4096 chars
    if len(msg) > 4000:
        msg = msg[:3997] + "..."

    return msg


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_synthesis(send_to_telegram: bool = False) -> dict:
    """Run the full synthesis pipeline."""
    ensure_table()

    log.info("Collecting all layers for synthesis...")
    layers = collect_all_layers()

    log.info("Building synthesis prompt...")
    prompt = build_synthesis_prompt(layers)
    log.info("Prompt: %d chars", len(prompt))

    log.info("Calling Claude Sonnet...")
    result = call_synthesis(prompt)

    if "error" in result:
        log.error("Synthesis failed: %s", result["error"])
        return result

    # Store in DB
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    execute("""
        INSERT INTO synthesis (date, prompt_tokens, output_tokens, cost_usd, raw_output)
        VALUES (%s, %s, %s, %s, %s)
    """, (today, result["input_tokens"], result["output_tokens"],
          result["cost_usd"], result["output"]))

    msg = format_synthesis_telegram(result["output"])
    log.info("Synthesis:\n%s", result["output"][:500])

    if send_to_telegram:
        send_telegram(msg)

    return result


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_synthesis(send_to_telegram=send_tg)
    if "output" in result:
        print(format_synthesis_telegram(result["output"]))
        print(f"\n--- Cost: ${result['cost_usd']:.4f} ({result['input_tokens']} in / {result['output_tokens']} out) ---")
    else:
        print(f"ERROR: {result.get('error', 'Unknown')}")
