"""Watchlist Price Tracker — Task 2 of Fiery Eyes v5.1

Tracks 4 core tokens (JUP, HYPE, RENDER, BONK) + BTC/SOL + ISA proxies (MSTR, COIN).
Fetches prices from CoinGecko, calculates ATH distance and zone classification.
Stores in watchlist_status table. Alerts on zone shifts.
"""

import html
import requests
from datetime import datetime, timezone
from config import COINGECKO_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("watchlist")

# ---------------------------------------------------------------------------
# Token configuration
# ---------------------------------------------------------------------------
# CoinGecko IDs mapped to our symbols
TOKENS = {
    "BTC": {"cg_id": "bitcoin", "ath": 126000},
    "SOL": {"cg_id": "solana", "ath": 295},
    "JUP": {"cg_id": "jupiter-exchange-solana", "ath": 2.00},
    "HYPE": {"cg_id": "hyperliquid", "ath": 59},
    "RENDER": {"cg_id": "render-token", "ath": 13.59},
    "BONK": {"cg_id": "bonk", "ath": 0.000059},
    "MSTR": {"cg_id": "microstrategy", "ath": 457},
    "COIN": {"cg_id": "coinbase-global-inc", "ath": 238},
}

# MSTR mNAV calculation constants
MSTR_BTC_HELD = 713502
MSTR_AVG_COST = 76052

CORE_TOKENS = ["JUP", "HYPE", "RENDER", "BONK"]
ISA_TOKENS = ["MSTR", "COIN"]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    """Create watchlist_status table if it doesn't exist."""
    execute("""
        CREATE TABLE IF NOT EXISTS watchlist_status (
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
        )
    """)


# ---------------------------------------------------------------------------
# Zone classification
# ---------------------------------------------------------------------------
def classify_zone(pct_from_ath: float) -> str:
    """Classify token zone based on distance from ATH."""
    if pct_from_ath <= -70:
        return "🟢 Deep"
    elif pct_from_ath <= -30:
        return "🟡 Mid"
    else:
        return "🔴 Near ATH"


# ---------------------------------------------------------------------------
# Price fetching
# ---------------------------------------------------------------------------
def fetch_prices() -> dict:
    """Fetch prices for all watchlist tokens from CoinGecko."""
    cg_ids = [t["cg_id"] for t in TOKENS.values()]
    ids_str = ",".join(cg_ids)

    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": ids_str,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_7d_change": "true",
            "include_market_cap": "true",
        }
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-key"] = COINGECKO_API_KEY

        resp = requests.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        results = {}
        for symbol, cfg in TOKENS.items():
            cg_id = cfg["cg_id"]
            if cg_id not in data:
                log.warning("No data for %s (%s)", symbol, cg_id)
                continue

            token_data = data[cg_id]
            price = token_data.get("usd")
            if price is None:
                continue

            ath = cfg["ath"]
            pct_from_ath = round((price - ath) / ath * 100, 1)
            zone = classify_zone(pct_from_ath)
            change_24h = token_data.get("usd_24h_change")
            change_7d = token_data.get("usd_7d_change")  # May be None on demo tier
            mcap = token_data.get("usd_market_cap")

            results[symbol] = {
                "price": price,
                "ath": ath,
                "pct_from_ath": pct_from_ath,
                "zone": zone,
                "change_24h": round(change_24h, 1) if change_24h else None,
                "change_7d": round(change_7d, 1) if change_7d else None,
                "mcap": mcap,
            }

        log.info("Fetched prices for %d/%d tokens", len(results), len(TOKENS))
        return results

    except Exception as e:
        log.error("Failed to fetch prices: %s", e)
        return {}


# ---------------------------------------------------------------------------
# MSTR mNAV calculation
# ---------------------------------------------------------------------------
def calc_mstr_mnav(mstr_price: float, btc_price: float, mstr_mcap: float | None) -> float | None:
    """Calculate MSTR mNAV = market_cap / (btc_held * btc_price).
    If mcap not available, estimate from price (rough shares outstanding ~18M)."""
    if mstr_mcap and mstr_mcap > 0:
        btc_value = MSTR_BTC_HELD * btc_price
        return round(mstr_mcap / btc_value, 2) if btc_value > 0 else None
    return None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def store_watchlist(prices: dict):
    """Store all token prices in the database."""
    for symbol, data in prices.items():
        execute("""
            INSERT INTO watchlist_status (token, price, ath, pct_from_ath, zone, change_24h, change_7d, mcap)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (symbol, data["price"], data["ath"], data["pct_from_ath"],
              data["zone"], data["change_24h"], data["change_7d"], data["mcap"]))
    log.info("Stored watchlist data for %d tokens", len(prices))


def get_previous_zones() -> dict:
    """Get the most recent zone for each token (for zone shift detection)."""
    rows = execute("""
        SELECT DISTINCT ON (token) token, zone
        FROM watchlist_status
        ORDER BY token, timestamp DESC
    """, fetch=True)
    return {row[0]: row[1] for row in rows} if rows else {}


# ---------------------------------------------------------------------------
# Zone shift detection
# ---------------------------------------------------------------------------
def detect_zone_shifts(prices: dict, previous_zones: dict) -> list:
    """Detect tokens that changed zone since last check."""
    shifts = []
    for symbol, data in prices.items():
        prev = previous_zones.get(symbol)
        if prev and prev != data["zone"]:
            shifts.append({
                "token": symbol,
                "old_zone": prev,
                "new_zone": data["zone"],
                "price": data["price"],
                "pct_from_ath": data["pct_from_ath"],
            })
    return shifts


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt_price(price: float) -> str:
    """Format price for display."""
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    elif price >= 0.001:
        return f"${price:.4f}"
    else:
        return f"${price:.2e}"


def format_watchlist_telegram(prices: dict) -> str:
    """Format watchlist data for Telegram (HTML parse mode)."""
    lines = ["<b>Token    Price     24h    %ATH   Zone</b>"]

    # BTC first
    if "BTC" in prices:
        d = prices["BTC"]
        from btc_cycle import calculate_cycle
        cycle = calculate_cycle(d["price"])
        chg = f"{d['change_24h']:+.0f}%" if d["change_24h"] else "—"
        lines.append(f"BTC      {_fmt_price(d['price']):>9s}   {chg:>5s}    {d['pct_from_ath']:+.0f}%   Bear {cycle['bear_progress_pct']:.0f}%")

    # SOL
    if "SOL" in prices:
        d = prices["SOL"]
        chg = f"{d['change_24h']:+.0f}%" if d["change_24h"] else "—"
        lines.append(f"SOL      {_fmt_price(d['price']):>9s}   {chg:>5s}    {d['pct_from_ath']:+.0f}%   {d['zone']}")

    # Core tokens
    for symbol in CORE_TOKENS:
        if symbol not in prices:
            continue
        d = prices[symbol]
        chg = f"{d['change_24h']:+.0f}%" if d["change_24h"] else "—"
        lines.append(f"{symbol:<8s} {_fmt_price(d['price']):>9s}   {chg:>5s}    {d['pct_from_ath']:+.0f}%   {d['zone']}")

    # ISA proxies
    for symbol in ISA_TOKENS:
        if symbol not in prices:
            continue
        d = prices[symbol]
        chg = f"{d['change_24h']:+.0f}%" if d["change_24h"] else "—"
        extra = ""
        if symbol == "MSTR" and "BTC" in prices:
            mnav = calc_mstr_mnav(d["price"], prices["BTC"]["price"], d["mcap"])
            if mnav:
                extra = f"  mNAV: {mnav}"
        lines.append(f"{symbol:<8s} {_fmt_price(d['price']):>9s}   {chg:>5s}    {d['pct_from_ath']:+.0f}%   {'ISA'}{extra}")

    return "<pre>" + "\n".join(lines) + "</pre>"


def format_zone_shift_alert(shift: dict) -> str:
    """Format a zone shift alert for Telegram."""
    return (
        f"⚡ <b>ZONE SHIFT — ${shift['token']}</b>\n"
        f"  {shift['old_zone']} → {shift['new_zone']}\n"
        f"  Price: {_fmt_price(shift['price'])} ({shift['pct_from_ath']:+.0f}% ATH)"
    )


def send_telegram(text: str, chat_id: str | None = None):
    """Send a message via Telegram bot."""
    if not TELEGRAM_BOT_TOKEN:
        log.warning("Telegram not configured")
        return
    target = chat_id or TELEGRAM_CHAT_ID
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code == 200:
            log.info("Telegram message sent")
        else:
            log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
    except Exception as e:
        log.error("Telegram send error: %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_watchlist(send_to_telegram: bool = False) -> dict:
    """Fetch prices, calculate zones, store, detect shifts, optionally send to Telegram."""
    ensure_table()

    # Get previous zones before storing new data
    previous_zones = get_previous_zones()

    prices = fetch_prices()
    if not prices:
        log.error("No prices fetched")
        return {}

    store_watchlist(prices)

    # Detect zone shifts
    shifts = detect_zone_shifts(prices, previous_zones)
    for shift in shifts:
        log.info("Zone shift: %s %s → %s", shift["token"], shift["old_zone"], shift["new_zone"])
        if send_to_telegram:
            send_telegram(format_zone_shift_alert(shift))

    msg = format_watchlist_telegram(prices)
    log.info("Watchlist report:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)

    return prices


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_watchlist(send_to_telegram=send_tg)
    if result:
        print(format_watchlist_telegram(result))
    else:
        print("ERROR: No prices fetched")
        sys.exit(1)
