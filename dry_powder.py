"""Dry Powder Yield Monitoring — Task 19 of Fiery Eyes v5.1

Tracks if USDC dry powder is earning yield.
Sources: DeFiLlama yields API for Solana USDC pools.
Monitors: Kamino, marginfi, and top Solana USDC opportunities.
Shows in daily report: idle USDC should be earning.
"""

import requests
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("dry_powder")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# Protocols we trust for USDC yield
TRUSTED_PROTOCOLS = {"kamino-lend", "kamino-liquidity", "marginfi", "marginfi-lst", "drift-protocol", "drift-staked-sol", "save", "save-sol", "solend"}

# Minimum TVL to consider a pool safe
MIN_TVL_USD = 5_000_000


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS yield_rates (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            protocol TEXT NOT NULL,
            pool_symbol TEXT,
            apy REAL,
            tvl_usd REAL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
def fetch_usdc_yields() -> list:
    """Fetch Solana USDC yield opportunities from DeFiLlama."""
    try:
        resp = requests.get("https://yields.llama.fi/pools", timeout=20)
        resp.raise_for_status()
        pools = resp.json().get("data", [])

        results = []

        # Filter for Solana USDC pools on trusted protocols
        for p in pools:
            if p.get("chain") != "Solana":
                continue
            symbol = p.get("symbol") or ""
            if "USDC" not in symbol:
                continue
            project = (p.get("project") or "").lower()
            tvl = p.get("tvlUsd", 0) or 0
            apy = p.get("apy", 0) or 0

            # Two categories: trusted lending (single-sided) and LP pools
            is_trusted = project in TRUSTED_PROTOCOLS
            is_lending = symbol == "USDC"  # Pure USDC lending, no IL

            if tvl < MIN_TVL_USD:
                continue

            results.append({
                "protocol": p.get("project", "unknown"),
                "symbol": symbol,
                "apy": round(apy, 2),
                "tvl_usd": tvl,
                "is_lending": is_lending,
                "is_trusted": is_trusted,
                "pool_id": p.get("pool"),
            })

        # Sort: trusted lending first, then by APY
        results.sort(key=lambda x: (not x["is_lending"], not x["is_trusted"], -x["apy"]))

        log.info("Found %d Solana USDC pools (TVL>$%dM)", len(results), MIN_TVL_USD // 1_000_000)
        return results

    except Exception as e:
        log.error("Yield fetch failed: %s", e)
        return []


def store_yields(yields: list):
    """Store yield snapshots."""
    ensure_table()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for y in yields[:10]:  # Top 10 only
        execute("""
            INSERT INTO yield_rates (date, protocol, pool_symbol, apy, tvl_usd)
            VALUES (%s, %s, %s, %s, %s)
        """, (today, y["protocol"], y["symbol"], y["apy"], y["tvl_usd"]))


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt_usd(v):
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.0f}M"
    return f"${v:,.0f}"


def format_yields_telegram(yields: list) -> str:
    """Format yield opportunities for Telegram."""
    if not yields:
        return "💰 No Solana USDC yield opportunities found"

    lines = ["💰 <b>DRY POWDER YIELDS</b> (Solana USDC)", ""]

    # Trusted lending (single-sided, no IL)
    lending = [y for y in yields if y["is_lending"] and y["is_trusted"]]
    if lending:
        lines.append("<b>Lending (no IL):</b>")
        for y in lending[:5]:
            lines.append(f"  {y['protocol']}: {y['apy']:.2f}% APY ({_fmt_usd(y['tvl_usd'])} TVL)")

    # Best LP opportunities (higher yield, some IL risk)
    lps = [y for y in yields if not y["is_lending"] and y["apy"] > 5][:5]
    if lps:
        lines.append("\n<b>LP pools (IL risk):</b>")
        for y in lps:
            lines.append(f"  {y['protocol']} {y['symbol']}: {y['apy']:.1f}% APY ({_fmt_usd(y['tvl_usd'])})")

    # Summary
    if lending:
        best = lending[0]
        lines.append(f"\n📌 Best safe yield: {best['protocol']} {best['apy']:.2f}%")
        lines.append(f"Idle USDC earning nothing = {best['apy']:.2f}% opportunity cost")

    return "\n".join(lines)


def format_for_report(yields: list) -> str | None:
    """One-line for daily report."""
    lending = [y for y in yields if y["is_lending"] and y["is_trusted"]]
    if not lending:
        return None
    best = lending[0]
    return f"  Dry powder: {best['protocol']} USDC {best['apy']:.2f}% APY ({_fmt_usd(best['tvl_usd'])} TVL)"


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_yield_monitor(send_to_telegram: bool = False) -> list:
    """Fetch and report USDC yield opportunities."""
    yields = fetch_usdc_yields()
    if yields:
        store_yields(yields)
    msg = format_yields_telegram(yields)
    log.info("Yields:\n%s", msg)
    if send_to_telegram:
        send_telegram(msg)
    return yields


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    yields = run_yield_monitor(send_to_telegram=send_tg)
    print(format_yields_telegram(yields))
