"""Telegram Outputs — Task 16 of Fiery Eyes v5.1

Three output formats:
1. Huoyan Pulse (every 4h) — lightweight one-screen summary
2. Nightly Strategist (00:00 UTC) — full daily report + synthesis (already built)
3. Weekly Review (Sundays 08:00) — recommendation accuracy + cross-chain + performance

H-Fire alerts are handled by existing alert routing (watchlist convergence, >$5M signals).
"""

import requests
from datetime import datetime, timedelta, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("outputs")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}



def _fmt_usd(v):
    if v is None: return "—"
    if abs(v) >= 1e9: return f"${v/1e9:.1f}B"
    if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:,.0f}"


def _fmt_price(p):
    if p >= 1000: return f"${p:,.0f}"
    if p >= 1: return f"${p:.2f}"
    if p >= 0.001: return f"${p:.4f}"
    return f"${p:.2e}"


# ---------------------------------------------------------------------------
# 1. HUOYAN PULSE (4h) — one-screen summary
# ---------------------------------------------------------------------------
def generate_pulse() -> str:
    """Lightweight 4h pulse — key numbers only, no analysis."""
    now = datetime.now(timezone.utc)
    time_str = now.strftime("%H:%M UTC")

    lines = [f"📡 <b>PULSE — {time_str}</b>", ""]

    # BTC line
    try:
        from btc_cycle import fetch_btc_price, calculate_cycle
        btc = fetch_btc_price()
        if btc:
            cycle = calculate_cycle(btc)
            lines.append(
                f"₿ ${btc:,.0f} (-{cycle['drawdown_pct']}%) | "
                f"Bear {cycle['bear_progress_pct']:.0f}%"
            )
    except Exception as e:
        log.error("Pulse BTC failed: %s", e)

    # Market structure line
    try:
        from market_structure import fetch_fear_greed, fetch_funding_rate
        fg = fetch_fear_greed()
        funding = fetch_funding_rate("BTCUSDT")
        parts = []
        if fg.get("value") is not None:
            parts.append(f"F&G: {fg['value']}")
        if funding.get("current_pct") is not None:
            streak = funding.get("streak_days", 0)
            streak_str = f" ({streak:.0f}d)" if streak >= 2 else ""
            parts.append(f"Fund: {funding['current_pct']:+.4f}%{streak_str}")
        if parts:
            lines.append(" | ".join(parts))
    except Exception as e:
        log.error("Pulse market structure failed: %s", e)

    # Watchlist prices — compact
    try:
        from watchlist import fetch_prices, CORE_TOKENS, SATELLITE_TOKENS, LOTTERY_TOKENS
        prices = fetch_prices()
        if prices:
            token_parts = []
            for sym in CORE_TOKENS + SATELLITE_TOKENS + LOTTERY_TOKENS:
                if sym in prices:
                    d = prices[sym]
                    chg = f"{d['change_24h']:+.0f}%" if d.get("change_24h") else ""
                    token_parts.append(f"{sym} {_fmt_price(d['price'])} {chg}")
            if token_parts:
                # Two per line
                for i in range(0, len(token_parts), 2):
                    pair = token_parts[i:i+2]
                    lines.append(" | ".join(pair))
    except Exception as e:
        log.error("Pulse watchlist failed: %s", e)

    # Liquidity one-liner
    try:
        from liquidity import fetch_dxy
        dxy = fetch_dxy()
        if dxy:
            from market_structure import fetch_fear_greed as _fg2
            # Already have F&G above, just add DXY to the line
            pass
    except Exception:
        pass

    # Smart money (count only)
    try:
        row = execute("""
            SELECT COUNT(*) FROM x_intelligence
            WHERE detected_at > NOW() - INTERVAL '4 hours'
              AND signal_strength IN ('medium', 'strong')
        """, fetch=True)
        count = row[0][0] if row and row[0] else 0
        if count > 0:
            lines.append(f"📡 {count} X signals (4h)")
    except Exception:
        pass

    # Whale swaps (count only)
    try:
        row = execute("""
            SELECT COUNT(*) FROM large_swaps
            WHERE timestamp > NOW() - INTERVAL '4 hours'
        """, fetch=True)
        count = row[0][0] if row and row[0] else 0
        if count > 0:
            lines.append(f"🐋 {count} large swaps (4h)")
    except Exception:
        pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. WEEKLY REVIEW (Sundays) — performance + accuracy
# ---------------------------------------------------------------------------
def generate_weekly_review() -> str:
    """Weekly review: recommendation accuracy, cross-chain, performance."""
    lines = ["📋 <b>WEEKLY REVIEW</b>", ""]

    # Recommendation accuracy (7d lookback)
    try:
        from rec_ledger import review_recommendations
        results = review_recommendations(days_ago=7)
        if results:
            correct = sum(1 for r in results if r["was_correct"])
            total = len(results)
            pct = correct / total * 100 if total > 0 else 0
            lines.append(f"<b>Recommendation Accuracy (7d):</b> {correct}/{total} ({pct:.0f}%)")
            for r in results:
                emoji = "✅" if r["was_correct"] else "❌"
                lines.append(
                    f"  {emoji} {r['token']}: {r['action']} at {_fmt_price(r['price_at_rec'])} → "
                    f"{_fmt_price(r['current_price'])} ({r['pnl_pct']:+.1f}%)"
                )
        else:
            lines.append("<b>Recommendation Accuracy:</b> No 7-day-old recs to compare yet")
    except Exception as e:
        log.error("Weekly recs failed: %s", e)
        lines.append("Recommendation review: error")

    # Portfolio performance
    try:
        from portfolio import get_portfolio, format_pnl_telegram
        portfolio = get_portfolio()
        if portfolio.get("positions"):
            total_value = sum(p["value"] for p in portfolio["positions"])
            total_pnl = sum(p["pnl"] for p in portfolio["positions"])
            total_cost = sum(p["cost"] for p in portfolio["positions"])
            pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
            lines.append(f"\n<b>Portfolio:</b> {_fmt_usd(total_value)} ({pnl_pct:+.1f}%)")
    except Exception as e:
        log.error("Weekly portfolio failed: %s", e)

    # Cross-chain scorecard
    try:
        from cross_chain import run_cross_chain, format_cross_chain_telegram
        result = run_cross_chain()
        lines.append("")
        lines.append(format_cross_chain_telegram(result["data"], result["alerts"]))
    except Exception as e:
        log.error("Weekly cross-chain failed: %s", e)

    # BTC cycle progress
    try:
        from btc_cycle import fetch_btc_price, calculate_cycle
        btc = fetch_btc_price()
        if btc:
            cycle = calculate_cycle(btc)
            lines.append(f"\n<b>BTC Cycle:</b> {cycle['bear_progress_pct']:.0f}% through bear, ~{cycle['days_remaining']}d to est. bottom")
    except Exception as e:
        log.error("Weekly BTC cycle failed: %s", e)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Send helpers
# ---------------------------------------------------------------------------
def send_telegram(text: str, chat_id: str | None = None):
    if not TELEGRAM_BOT_TOKEN:
        return
    target = chat_id or TELEGRAM_CHAT_ID
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        # Split if too long
        chunks = [text] if len(text) <= 4000 else _split_message(text)
        for chunk in chunks:
            resp = requests.post(url, json={
                "chat_id": target,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                    "reply_markup": _KEYBOARD_JSON,
            }, timeout=15)
            if resp.status_code != 200:
                log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


def _split_message(text: str, max_len: int = 4000) -> list:
    """Split message at line boundaries."""
    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)
    return chunks


def send_pulse():
    """Generate and send 4h pulse."""
    msg = generate_pulse()
    log.info("Sending pulse")
    send_telegram(msg)


def send_weekly():
    """Generate and send weekly review."""
    msg = generate_weekly_review()
    log.info("Sending weekly review")
    send_telegram(msg)


if __name__ == "__main__":
    import sys
    if "--pulse" in sys.argv:
        print(generate_pulse())
    elif "--weekly" in sys.argv:
        print(generate_weekly_review())
    else:
        print("Usage: python outputs.py --pulse | --weekly")
