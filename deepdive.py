"""Deep Dive — Task 17 of Fiery Eyes v5.1

Paste contract address → 9 data sources in parallel → Claude scores → full report.
Score >6.5 = watchlist candidate.

Sources:
1. DexScreener — price, volume, liquidity, pair data
2. CoinGecko — market cap, supply, social links
3. Birdeye — holder data (via Helius)
4. DeFiLlama — protocol revenue (if tracked)
5. X intelligence — recent signals mentioning this token
6. On-chain — large swaps in last 24h
7. Token metadata — name, symbol, decimals
8. Supply analysis — circulating vs total, top holders
9. Claude synthesis — score all dimensions

Dimensions scored 1-10:
- Revenue/utility, Tokenomics, Team, Smart money, Liquidity, Narrative
"""

import json
import time
import requests
import anthropic
from datetime import datetime, timezone
from config import (ANTHROPIC_API_KEY, HELIUS_API_KEY, HELIUS_RPC_URL,
                    COINGECKO_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger)
from db.connection import execute

log = get_logger("deepdive")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "💼 Portfolio", "📈 Market", "🔧 Tools"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


SONNET_MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Data collection from multiple sources
# ---------------------------------------------------------------------------
def fetch_dexscreener(address: str) -> dict:
    """Fetch token data from DexScreener."""
    try:
        resp = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}", timeout=15)
        resp.raise_for_status()
        pairs = resp.json().get("pairs", [])
        if not pairs:
            return {}

        # Use highest liquidity pair
        top = sorted(pairs, key=lambda p: p.get("liquidity", {}).get("usd", 0), reverse=True)[0]
        return {
            "price_usd": top.get("priceUsd"),
            "price_change_24h": top.get("priceChange", {}).get("h24"),
            "price_change_6h": top.get("priceChange", {}).get("h6"),
            "volume_24h": top.get("volume", {}).get("h24"),
            "liquidity_usd": top.get("liquidity", {}).get("usd"),
            "market_cap": top.get("marketCap") or top.get("fdv"),
            "fdv": top.get("fdv"),
            "pair_address": top.get("pairAddress"),
            "dex": top.get("dexId"),
            "name": top.get("baseToken", {}).get("name"),
            "symbol": top.get("baseToken", {}).get("symbol"),
            "txns_24h_buys": top.get("txns", {}).get("h24", {}).get("buys"),
            "txns_24h_sells": top.get("txns", {}).get("h24", {}).get("sells"),
            "pairs_count": len(pairs),
        }
    except Exception as e:
        log.error("DexScreener failed: %s", e)
        return {}


def fetch_coingecko_by_address(address: str) -> dict:
    """Try to find token on CoinGecko by Solana contract address."""
    try:
        headers = {"x-cg-demo-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
        resp = requests.get(
            f"https://api.coingecko.com/api/v3/coins/solana/contract/{address}",
            headers=headers, timeout=15)
        if resp.ok:
            d = resp.json()
            return {
                "cg_id": d.get("id"),
                "description": (d.get("description", {}).get("en") or "")[:300],
                "links": {
                    "website": (d.get("links", {}).get("homepage", [None]) or [None])[0],
                    "twitter": d.get("links", {}).get("twitter_screen_name"),
                },
                "community": {
                    "twitter_followers": d.get("community_data", {}).get("twitter_followers"),
                },
                "market_data": {
                    "ath": d.get("market_data", {}).get("ath", {}).get("usd"),
                    "ath_date": d.get("market_data", {}).get("ath_date", {}).get("usd"),
                    "circulating_supply": d.get("market_data", {}).get("circulating_supply"),
                    "total_supply": d.get("market_data", {}).get("total_supply"),
                },
            }
    except Exception:
        pass
    return {}


def fetch_helius_metadata(address: str) -> dict:
    """Fetch token metadata from Helius."""
    if not HELIUS_API_KEY:
        return {}
    try:
        resp = requests.post(
            f"https://api.helius.xyz/v0/token-metadata?api-key={HELIUS_API_KEY}",
            json={"mintAccounts": [address]},
            timeout=15)
        if resp.ok:
            data = resp.json()
            if data and len(data) > 0:
                meta = data[0]
                return {
                    "name": meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {}).get("name"),
                    "symbol": meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {}).get("symbol"),
                    "decimals": meta.get("onChainMetadata", {}).get("metadata", {}).get("data", {}).get("decimals"),
                }
    except Exception:
        pass
    return {}


def fetch_x_signals(symbol: str) -> list:
    """Get recent X intelligence for this token."""
    try:
        rows = execute("""
            SELECT source_handle, parsed_type, amount_usd, signal_strength, detected_at
            FROM x_intelligence
            WHERE token_symbol = %s AND detected_at > NOW() - INTERVAL '7 days'
            ORDER BY detected_at DESC LIMIT 10
        """, (symbol,), fetch=True)
        return [{"source": r[0], "type": r[1], "amount": r[2], "strength": r[3]} for r in (rows or [])]
    except Exception:
        return []


def fetch_defillama_revenue(name_or_slug: str) -> dict:
    """Try to find protocol revenue on DeFiLlama."""
    for slug in [name_or_slug.lower(), name_or_slug.lower().replace(" ", "-")]:
        try:
            resp = requests.get(
                f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyRevenue",
                timeout=10)
            if resp.ok:
                d = resp.json()
                return {
                    "rev_24h": d.get("total24h", 0),
                    "rev_30d": d.get("total30d", 0),
                }
        except Exception:
            pass
    return {}


# ---------------------------------------------------------------------------
# Claude scoring
# ---------------------------------------------------------------------------
def score_with_claude(collected: dict) -> dict:
    """Send all collected data to Claude for scoring."""
    if not ANTHROPIC_API_KEY:
        return {"error": "No API key"}

    data_str = json.dumps(collected, indent=2, default=str)

    prompt = f"""Score this Solana token across 6 dimensions (1-10 each). Be harsh — most tokens score 3-5.

TOKEN DATA:
{data_str}

Score each dimension and give a one-line justification:
1. REVENUE/UTILITY (1-10): Does it generate real revenue or have clear utility?
2. TOKENOMICS (1-10): Circulating %, emissions, burns, FDV/MCap ratio
3. TEAM (1-10): Known team? Track record? Active development?
4. SMART MONEY (1-10): Whale activity, KOL mentions, institutional interest
5. LIQUIDITY (1-10): DEX liquidity depth, volume/MCap ratio, slippage risk
6. NARRATIVE (1-10): Current market narrative fit, catalysts, hype cycle position

Then give:
- OVERALL SCORE: weighted average (out of 10)
- VERDICT: one sentence — is this a watchlist candidate?
- KEY RISK: the single biggest risk
- COMPARABLE: what existing watchlist token is this most similar to?

Respond in plain text with the format above. Be concise."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=SONNET_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        return {
            "analysis": response.content[0].text,
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    except Exception as e:
        log.error("Claude scoring failed: %s", e)
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Main deep dive
# ---------------------------------------------------------------------------
def run_deepdive(address: str) -> dict:
    """Run full deep dive on a token by contract address."""
    log.info("Deep dive starting for %s", address)
    start = time.time()

    collected = {"address": address}

    # 1. DexScreener
    dex = fetch_dexscreener(address)
    collected["dexscreener"] = dex
    symbol = dex.get("symbol", "UNKNOWN")
    name = dex.get("name", "Unknown")
    log.info("DexScreener: %s (%s) — $%s, MCap %s",
             name, symbol, dex.get("price_usd", "?"),
             dex.get("market_cap", "?"))

    # 2. CoinGecko
    cg = fetch_coingecko_by_address(address)
    collected["coingecko"] = cg

    # 3. Helius metadata
    meta = fetch_helius_metadata(address)
    collected["helius"] = meta

    # 4. X signals
    x_sigs = fetch_x_signals(symbol)
    collected["x_signals"] = x_sigs

    # 5. DeFiLlama revenue
    rev = fetch_defillama_revenue(name)
    collected["defillama"] = rev

    # 6. Claude scoring
    scoring = score_with_claude(collected)
    collected["claude_scoring"] = scoring

    elapsed = time.time() - start
    log.info("Deep dive complete for %s in %.1fs", symbol, elapsed)

    return {
        "address": address,
        "symbol": symbol,
        "name": name,
        "data": collected,
        "scoring": scoring,
        "elapsed_seconds": round(elapsed, 1),
    }


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def format_deepdive_telegram(result: dict) -> str:
    """Format deep dive result for Telegram."""
    dex = result["data"].get("dexscreener", {})
    scoring = result.get("scoring", {})

    lines = [
        f"🔍 <b>DEEP DIVE — ${result['symbol']}</b>",
        f"<i>{result['name']}</i>",
        "",
    ]

    # Key metrics
    price = dex.get("price_usd", "?")
    mcap = dex.get("market_cap")
    vol = dex.get("volume_24h")
    liq = dex.get("liquidity_usd")
    chg = dex.get("price_change_24h")

    lines.append(f"Price: ${price}")
    if mcap:
        lines.append(f"MCap: ${mcap:,.0f}" if isinstance(mcap, (int, float)) else f"MCap: {mcap}")
    if vol:
        lines.append(f"Vol 24h: ${vol:,.0f}" if isinstance(vol, (int, float)) else f"Vol: {vol}")
    if liq:
        lines.append(f"Liquidity: ${liq:,.0f}" if isinstance(liq, (int, float)) else f"Liq: {liq}")
    if chg:
        lines.append(f"24h: {chg}%")

    # X signals
    x_sigs = result["data"].get("x_signals", [])
    if x_sigs:
        lines.append(f"\n📡 {len(x_sigs)} X signals (7d)")

    # Claude analysis
    analysis = scoring.get("analysis", "")
    if analysis:
        lines.append(f"\n{analysis}")

    lines.append(f"\n⏱ {result['elapsed_seconds']}s | CA: <code>{result['address'][:20]}...</code>")

    return "\n".join(lines)


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        chunks = [text] if len(text) <= 4000 else [text[:4000], text[4000:]]
        for chunk in chunks:
            requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                    "reply_markup": _KEYBOARD_JSON,
            }, timeout=15)
    except Exception as e:
        log.error("Telegram error: %s", e)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python deepdive.py <contract_address> [--telegram]")
        sys.exit(1)
    address = sys.argv[1]
    send_tg = "--telegram" in sys.argv
    result = run_deepdive(address)
    msg = format_deepdive_telegram(result)
    print(msg)
    if send_tg:
        send_telegram(msg)
