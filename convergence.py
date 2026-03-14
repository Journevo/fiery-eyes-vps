"""Convergence Detector — Task 8 of Fiery Eyes v5.1

Detects when Grok X signals and on-chain swap detection agree on the same token.
Convergence = higher conviction signal.

Logic:
- When Grok catches a signal that on-chain ALSO detected → CONVERGENCE (higher conviction)
- When Grok catches a signal on-chain MISSED → supplementary context (add to report)
- When on-chain detects a swap Grok didn't mention → raw data (still valuable)
"""

import requests
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("convergence")

# Watchlist tokens we care about for convergence
WATCHLIST_SYMBOLS = {"JUP", "HYPE", "RENDER", "BONK", "SOL"}


def detect_convergence(hours: int = 12) -> list:
    """Find tokens with both Grok X signals AND on-chain large swaps in the window.

    Returns list of convergence dicts with combined evidence.
    """
    # Get X signals from Grok for watchlist tokens
    x_signals = execute("""
        SELECT token_symbol, source_handle, parsed_type, amount_usd,
               signal_strength, detected_at
        FROM x_intelligence
        WHERE detected_at > NOW() - INTERVAL '%s hours'
          AND token_symbol IS NOT NULL
          AND signal_strength IN ('medium', 'strong')
        ORDER BY detected_at DESC
    """ % int(hours), fetch=True)

    # Get on-chain swaps
    chain_signals = execute("""
        SELECT token, direction, amount_usd, pct_of_mcap, pool,
               alert_type, timestamp
        FROM large_swaps
        WHERE timestamp > NOW() - INTERVAL '%s hours'
        ORDER BY timestamp DESC
    """ % int(hours), fetch=True)

    # Build sets of tokens with each type of signal
    x_tokens = {}
    for row in (x_signals or []):
        symbol = row[0].upper() if row[0] else None
        if symbol:
            if symbol not in x_tokens:
                x_tokens[symbol] = []
            x_tokens[symbol].append({
                "source": row[1],
                "type": row[2],
                "amount_usd": row[3],
                "strength": row[4],
                "time": row[5],
            })

    chain_tokens = {}
    for row in (chain_signals or []):
        symbol = row[0].upper() if row[0] else None
        if symbol:
            if symbol not in chain_tokens:
                chain_tokens[symbol] = []
            chain_tokens[symbol].append({
                "direction": row[1],
                "amount_usd": row[2],
                "pct_mcap": row[3],
                "pool": row[4],
                "alert_type": row[5],
                "time": row[6],
            })

    convergences = []

    # Check for convergence: token appears in BOTH X signals and on-chain
    for symbol in set(x_tokens.keys()) & set(chain_tokens.keys()):
        x_data = x_tokens[symbol]
        chain_data = chain_tokens[symbol]

        # Determine combined direction
        x_buys = sum(1 for s in x_data if s["type"] in ("accumulation", "whale_buy",
                                                          "whale_flow", "transaction")
                     and "sell" not in (s.get("type") or ""))
        chain_buys = sum(1 for s in chain_data if s["direction"] == "BUY")

        direction = "BUY" if (x_buys + chain_buys) > (len(x_data) + len(chain_data)) / 2 else "MIXED"

        # Combine amounts
        x_total = sum(s["amount_usd"] or 0 for s in x_data)
        chain_total = sum(s["amount_usd"] or 0 for s in chain_data)

        convergences.append({
            "token": symbol,
            "type": "CONVERGENCE",
            "direction": direction,
            "x_signal_count": len(x_data),
            "chain_signal_count": len(chain_data),
            "x_sources": list(set(s["source"] for s in x_data)),
            "x_total_usd": x_total,
            "chain_total_usd": chain_total,
            "combined_total_usd": x_total + chain_total,
            "conviction": "HIGH",
            "in_watchlist": symbol in WATCHLIST_SYMBOLS,
        })

    # Also note X-only signals on watchlist tokens (supplementary context)
    for symbol in set(x_tokens.keys()) - set(chain_tokens.keys()):
        if symbol in WATCHLIST_SYMBOLS:
            x_data = x_tokens[symbol]
            strong = [s for s in x_data if s["strength"] == "strong"]
            if strong:
                convergences.append({
                    "token": symbol,
                    "type": "X_ONLY",
                    "direction": "UNKNOWN",
                    "x_signal_count": len(x_data),
                    "chain_signal_count": 0,
                    "x_sources": list(set(s["source"] for s in x_data)),
                    "x_total_usd": sum(s["amount_usd"] or 0 for s in x_data),
                    "chain_total_usd": 0,
                    "combined_total_usd": sum(s["amount_usd"] or 0 for s in x_data),
                    "conviction": "MEDIUM",
                    "in_watchlist": True,
                })

    # Sort by conviction then combined amount
    conv_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    convergences.sort(key=lambda c: (conv_order.get(c["conviction"], 9), -c["combined_total_usd"]))

    if convergences:
        log.info("Detected %d convergence signals", len(convergences))
    else:
        log.debug("No convergence signals in last %dh", hours)

    return convergences


def format_convergence_telegram(convergences: list) -> str | None:
    """Format convergence signals for Telegram. Returns None if no signals."""
    if not convergences:
        return None

    lines = ["🔀 <b>SIGNAL CONVERGENCE</b>", ""]

    for c in convergences:
        emoji = "🔴" if c["type"] == "CONVERGENCE" else "📡"
        watchlist_tag = " ⭐" if c["in_watchlist"] else ""

        if c["type"] == "CONVERGENCE":
            sources = ", ".join(c["x_sources"][:3])
            lines.append(
                f"{emoji} <b>${c['token']}</b> — CONVERGENCE [{c['conviction']}]{watchlist_tag}\n"
                f"  X: {c['x_signal_count']} signals ({sources})\n"
                f"  On-chain: {c['chain_signal_count']} swaps\n"
                f"  Direction: {c['direction']}"
            )
        else:  # X_ONLY
            sources = ", ".join(c["x_sources"][:3])
            lines.append(
                f"{emoji} <b>${c['token']}</b> — X signal only [{c['conviction']}]{watchlist_tag}\n"
                f"  {c['x_signal_count']} signals ({sources})"
            )

    return "\n".join(lines)


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


def run_convergence_check(hours: int = 12, send_to_telegram: bool = False) -> list:
    """Run convergence detection and optionally alert."""
    convergences = detect_convergence(hours)

    if convergences and send_to_telegram:
        msg = format_convergence_telegram(convergences)
        if msg:
            send_telegram(msg)

    return convergences


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    hours = 24  # Check wider window for testing
    results = run_convergence_check(hours, send_to_telegram=send_tg)
    if results:
        msg = format_convergence_telegram(results)
        if msg:
            print(msg)
    else:
        print("No convergence signals detected")
