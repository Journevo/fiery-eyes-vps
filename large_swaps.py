"""On-Chain Large Swap Detection — Task 3 of Fiery Eyes v5.1

Monitors DEX liquidity pools for large swaps on core tokens via DexScreener API.
Detects swaps >$50K, applies whale latency rule, stores and alerts.
"""

import requests
import time
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("large_swaps")

# ---------------------------------------------------------------------------
# Token addresses (Solana) + CoinGecko IDs for non-Solana
# ---------------------------------------------------------------------------
TOKENS = {
    "SOL": {
        "address": "So11111111111111111111111111111111111111112",
        "chain": "solana",
    },
    "JUP": {
        "address": "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        "chain": "solana",
    },
    "BONK": {
        "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "chain": "solana",
    },
    "RENDER": {
        "address": "rndrizKT3MK1iimdxRdWabcF7Zg7AR5T4nud4EkHBof",
        "chain": "solana",
    },
    # HYPE is on Hyperliquid chain — use DexScreener search
    "HYPE": {
        "address": "0x0000000000000000000000000000000000000001",  # placeholder
        "chain": "hyperliquid",
    },
}

# Minimum swap size to track (USD)
MIN_SWAP_USD = 50_000

# Whale latency rule threshold
SMALL_CAP_THRESHOLD = 200_000_000  # $200M MCap


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    """Create large_swaps table if it doesn't exist."""
    execute("""
        CREATE TABLE IF NOT EXISTS large_swaps (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount_usd REAL,
            pct_of_mcap REAL,
            pool TEXT,
            alert_type TEXT,
            note TEXT,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# DexScreener API
# ---------------------------------------------------------------------------
def fetch_token_pairs(token_address: str, chain: str = "solana") -> list:
    """Fetch trading pairs for a token from DexScreener."""
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs", [])
        if pairs:
            log.info("Found %d pairs for %s", len(pairs), token_address[:12])
        return pairs
    except Exception as e:
        log.error("DexScreener fetch failed for %s: %s", token_address[:12], e)
        return []


def fetch_recent_trades(pair_address: str, chain: str = "solana") -> list:
    """Fetch recent trades for a specific pair from DexScreener.
    Note: DexScreener doesn't expose individual trades via free API.
    We use txns data from pairs endpoint instead."""
    # DexScreener pairs already include txns counts and volume
    # For individual trade detection, we'd need Birdeye or Helius
    return []


def analyze_pairs_for_large_activity(symbol: str, pairs: list) -> list:
    """Analyze pair data for signs of large swap activity.
    
    DexScreener pairs include:
    - txns: buy/sell counts for m5, h1, h6, h24
    - volume: USD volume for m5, h1, h6, h24
    - priceChange: percentage for m5, h1, h6, h24
    
    We detect large swaps by looking at volume spikes relative to typical activity.
    """
    signals = []

    for pair in pairs[:3]:  # Check top 3 pairs by liquidity
        try:
            txns = pair.get("txns", {})
            volume = pair.get("volume", {})
            liquidity = pair.get("liquidity", {})
            price_change = pair.get("priceChange", {})
            dex_id = pair.get("dexId", "unknown")
            pair_address = pair.get("pairAddress", "")

            # Get market cap
            mcap = pair.get("marketCap") or pair.get("fdv") or 0

            # Analyze h1 volume vs h24 average
            vol_h1 = volume.get("h1", 0) or 0
            vol_h24 = volume.get("h24", 0) or 0
            avg_h1 = vol_h24 / 24 if vol_h24 > 0 else 0

            # Detect volume spike (h1 volume > 3x average hourly)
            if vol_h1 > MIN_SWAP_USD and avg_h1 > 0 and vol_h1 > avg_h1 * 3:
                buys_h1 = txns.get("h1", {}).get("buys", 0) or 0
                sells_h1 = txns.get("h1", {}).get("sells", 0) or 0
                total_txns = buys_h1 + sells_h1
                change_h1 = price_change.get("h1", 0) or 0

                # If few txns but high volume = whale activity
                if total_txns > 0 and total_txns < 50 and vol_h1 > MIN_SWAP_USD:
                    avg_trade_size = vol_h1 / total_txns
                    if avg_trade_size > MIN_SWAP_USD:
                        direction = "BUY" if change_h1 > 0 else "SELL"
                        pct_of_mcap = round(vol_h1 / mcap * 100, 4) if mcap > 0 else 0

                        # Whale latency rule
                        if mcap < SMALL_CAP_THRESHOLD and direction == "BUY":
                            alert_type = "WHALE_WATCHING"
                            note = "Small cap whale buy — watch for pullback, do not chase"
                        else:
                            alert_type = "WHALE_SIGNAL"
                            note = "Deep liquidity token — whale buy is actionable"

                        signals.append({
                            "token": symbol,
                            "direction": direction,
                            "amount_usd": vol_h1,
                            "pct_of_mcap": pct_of_mcap,
                            "pool": f"{dex_id} {pair_address[:12]}",
                            "alert_type": alert_type,
                            "note": note,
                            "mcap": mcap,
                            "avg_trade_size": avg_trade_size,
                            "txn_count": total_txns,
                            "price_change_h1": change_h1,
                        })

            # Also check m5 for very recent large activity
            vol_m5 = volume.get("m5", 0) or 0
            buys_m5 = txns.get("m5", {}).get("buys", 0) or 0
            sells_m5 = txns.get("m5", {}).get("sells", 0) or 0
            total_m5 = buys_m5 + sells_m5

            if vol_m5 > MIN_SWAP_USD and total_m5 > 0 and total_m5 < 10:
                avg_m5 = vol_m5 / total_m5
                if avg_m5 > MIN_SWAP_USD:
                    change_m5 = price_change.get("m5", 0) or 0
                    direction = "BUY" if change_m5 > 0 else "SELL"
                    pct_of_mcap = round(vol_m5 / mcap * 100, 4) if mcap > 0 else 0

                    if mcap < SMALL_CAP_THRESHOLD and direction == "BUY":
                        alert_type = "WHALE_WATCHING"
                        note = "Small cap whale buy — watch for pullback, do not chase"
                    else:
                        alert_type = "WHALE_SIGNAL"
                        note = "Deep liquidity token — whale activity is actionable"

                    # Avoid duplicate if already caught in h1
                    if not any(s["pool"] == f"{dex_id} {pair_address[:12]}" for s in signals):
                        signals.append({
                            "token": symbol,
                            "direction": direction,
                            "amount_usd": vol_m5,
                            "pct_of_mcap": pct_of_mcap,
                            "pool": f"{dex_id} {pair_address[:12]}",
                            "alert_type": alert_type,
                            "note": note,
                            "mcap": mcap,
                            "avg_trade_size": avg_m5,
                            "txn_count": total_m5,
                            "price_change_h1": change_m5,
                        })

        except Exception as e:
            log.error("Error analyzing pair for %s: %s", symbol, e)

    return signals


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def store_swap(swap: dict):
    """Store a detected large swap in the database."""
    execute("""
        INSERT INTO large_swaps (token, direction, amount_usd, pct_of_mcap, pool, alert_type, note)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (swap["token"], swap["direction"], swap["amount_usd"],
          swap["pct_of_mcap"], swap["pool"], swap["alert_type"], swap["note"]))


def is_duplicate(swap: dict) -> bool:
    """Check if we already logged a similar swap recently (within 1 hour)."""
    row = execute_one("""
        SELECT id FROM large_swaps
        WHERE token = %s AND direction = %s AND pool = %s
          AND timestamp > NOW() - INTERVAL '1 hour'
    """, (swap["token"], swap["direction"], swap["pool"]))
    return row is not None


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt_usd(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1e6:.1f}M"
    if value >= 1_000:
        return f"${value / 1e3:.0f}K"
    return f"${value:,.0f}"


def _fmt_mcap(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1e9:.1f}B"
    if value >= 1_000_000:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def format_swap_alert(swap: dict) -> str:
    """Format a swap alert for Telegram."""
    from watchlist import classify_zone, TOKENS as WL_TOKENS
    ath = WL_TOKENS.get(swap["token"], {}).get("ath")
    zone_str = ""
    if ath and swap.get("mcap"):
        # We don't have price here directly, but we can note the zone from watchlist
        zone_str = f"\n  Token: {swap['token']} | Zone: check /watchlist"

    return (
        f"🐋 <b>LARGE SWAP — ${swap['token']}</b>\n"
        f"  {_fmt_usd(swap['amount_usd'])} {swap['direction']} detected on {swap['pool']}\n"
        f"  = {swap['pct_of_mcap']:.2f}% of MCap ({_fmt_mcap(swap.get('mcap', 0))})\n"
        f"  Type: {swap['alert_type']} ({swap['note']})"
    )


def send_telegram(text: str):
    """Send a message via Telegram bot."""
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
            log.error("Telegram send failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram send error: %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_swap_detection(send_to_telegram: bool = False) -> list:
    """Scan all tokens for large swap activity."""
    ensure_table()

    all_signals = []

    for symbol, cfg in TOKENS.items():
        if cfg["chain"] != "solana":
            log.debug("Skipping %s (chain: %s)", symbol, cfg["chain"])
            continue

        log.info("Checking %s for large swaps...", symbol)
        pairs = fetch_token_pairs(cfg["address"], cfg["chain"])

        if not pairs:
            log.info("No pairs found for %s", symbol)
            continue

        signals = analyze_pairs_for_large_activity(symbol, pairs)

        for signal in signals:
            if is_duplicate(signal):
                log.debug("Skipping duplicate swap for %s", symbol)
                continue

            store_swap(signal)
            all_signals.append(signal)
            log.info("🐋 Large swap detected: %s %s %s on %s (%.2f%% MCap)",
                     signal["direction"], _fmt_usd(signal["amount_usd"]),
                     symbol, signal["pool"], signal["pct_of_mcap"])

            if send_to_telegram:
                send_telegram(format_swap_alert(signal))

        # Rate limit between tokens (DexScreener free tier)
        time.sleep(1)

    if not all_signals:
        log.info("No large swaps detected this scan")

    return all_signals


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    signals = run_swap_detection(send_to_telegram=send_tg)
    if signals:
        for s in signals:
            print(format_swap_alert(s))
    else:
        print("No large swaps detected")
