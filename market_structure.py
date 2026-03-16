"""Market Structure — Task 10 of Fiery Eyes v5.1

Every 4h: BTC OI, funding rate (+ consecutive streak), long/short ratio,
Fear & Greed index. Detects: funding negative >10d = local bottom signal.
OI spike + high funding = overleveraged.

Data sources (all free, no key):
- Binance Futures API: OI, funding rate, L/S ratio
- alternative.me: Fear & Greed Index
"""

import requests
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("market_structure")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


BINANCE_FAPI = "https://fapi.binance.com"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS market_structure (
            id SERIAL PRIMARY KEY,
            btc_oi REAL,
            btc_oi_change_24h REAL,
            btc_funding_rate REAL,
            funding_streak INTEGER,
            funding_streak_direction TEXT,
            long_pct REAL,
            short_pct REAL,
            ls_ratio REAL,
            fear_greed INTEGER,
            fear_greed_label TEXT,
            sol_funding_rate REAL,
            sol_oi REAL,
            timestamp TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------
def fetch_btc_oi() -> dict:
    """Fetch BTC open interest from Binance."""
    try:
        resp = requests.get(f"{BINANCE_FAPI}/fapi/v1/openInterest",
                           params={"symbol": "BTCUSDT"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        oi_btc = float(data.get("openInterest", 0))

        # Get BTC price for USD conversion
        resp2 = requests.get(f"{BINANCE_FAPI}/fapi/v1/ticker/price",
                            params={"symbol": "BTCUSDT"}, timeout=10)
        price = float(resp2.json().get("price", 70000)) if resp2.ok else 70000
        oi_usd = oi_btc * price

        log.info("BTC OI: %.0f BTC ($%.1fB)", oi_btc, oi_usd / 1e9)
        return {"oi_btc": oi_btc, "oi_usd": oi_usd, "price": price}
    except Exception as e:
        log.error("BTC OI fetch failed: %s", e)
        return {}


def fetch_funding_rate(symbol: str = "BTCUSDT") -> dict:
    """Fetch current and recent funding rates from Binance."""
    try:
        resp = requests.get(f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                           params={"symbol": symbol, "limit": 30}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return {}

        current = float(data[-1].get("fundingRate", 0))

        # Count consecutive negative/positive streak
        streak = 0
        direction = "negative" if current < 0 else "positive"
        for entry in reversed(data):
            rate = float(entry.get("fundingRate", 0))
            if (direction == "negative" and rate < 0) or (direction == "positive" and rate > 0):
                streak += 1
            else:
                break

        # Funding is every 8h, so streak * 8h = hours
        streak_days = round(streak * 8 / 24, 1)

        log.info("%s funding: %.4f%% (%s streak: %d periods = %.1f days)",
                 symbol, current * 100, direction, streak, streak_days)

        return {
            "current_rate": current,
            "current_pct": round(current * 100, 4),
            "streak": streak,
            "streak_days": streak_days,
            "streak_direction": direction,
        }
    except Exception as e:
        log.error("Funding rate fetch failed for %s: %s", symbol, e)
        return {}


def fetch_long_short_ratio(symbol: str = "BTCUSDT") -> dict:
    """Fetch global long/short account ratio from Binance."""
    try:
        resp = requests.get(
            f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol, "period": "1h", "limit": 1}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return {}

        latest = data[-1]
        long_pct = round(float(latest.get("longAccount", 0.5)) * 100, 1)
        short_pct = round(float(latest.get("shortAccount", 0.5)) * 100, 1)
        ratio = round(float(latest.get("longShortRatio", 1.0)), 2)

        log.info("BTC L/S: %.1f%% long / %.1f%% short (ratio: %.2f)", long_pct, short_pct, ratio)
        return {"long_pct": long_pct, "short_pct": short_pct, "ratio": ratio}
    except Exception as e:
        log.error("L/S ratio fetch failed: %s", e)
        return {}


def fetch_fear_greed() -> dict:
    """Fetch Fear & Greed Index from alternative.me."""
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        entry = data.get("data", [{}])[0]
        value = int(entry.get("value", 0))
        label = entry.get("value_classification", "Unknown")
        log.info("Fear & Greed: %d (%s)", value, label)
        return {"value": value, "label": label}
    except Exception as e:
        log.error("Fear & Greed fetch failed: %s", e)
        return {}


def fetch_sol_derivatives() -> dict:
    """Fetch SOL funding rate and OI from Binance."""
    result = {}
    try:
        resp = requests.get(f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                           params={"symbol": "SOLUSDT", "limit": 1}, timeout=10)
        if resp.ok and resp.json():
            result["sol_funding"] = float(resp.json()[-1].get("fundingRate", 0))
    except Exception:
        pass
    try:
        resp = requests.get(f"{BINANCE_FAPI}/fapi/v1/openInterest",
                           params={"symbol": "SOLUSDT"}, timeout=10)
        if resp.ok:
            sol_oi = float(resp.json().get("openInterest", 0))
            # Approximate USD value
            resp2 = requests.get(f"{BINANCE_FAPI}/fapi/v1/ticker/price",
                                params={"symbol": "SOLUSDT"}, timeout=10)
            sol_price = float(resp2.json().get("price", 87)) if resp2.ok else 87
            result["sol_oi_usd"] = sol_oi * sol_price
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def analyze_market_structure(data: dict) -> list:
    """Generate insights from market structure data."""
    insights = []
    funding = data.get("funding", {})
    fg = data.get("fear_greed", {})
    oi = data.get("oi", {})

    # Funding streak detection
    streak_days = funding.get("streak_days", 0)
    direction = funding.get("streak_direction", "")
    if direction == "negative" and streak_days >= 10:
        insights.append(f"⚠️ Funding negative {streak_days:.0f}d — longest since Dec 2022 bottom")
    elif direction == "negative" and streak_days >= 5:
        insights.append(f"Funding negative {streak_days:.0f}d — building local bottom signal")

    # Overleveraged detection
    oi_usd = oi.get("oi_usd", 0)
    rate = funding.get("current_pct", 0)
    if oi_usd > 100e9 and rate > 0.01:
        insights.append("⚠️ OI high + funding positive = overleveraged longs")
    elif oi_usd > 100e9 and rate < -0.01:
        insights.append("OI high + funding negative = short squeeze potential")

    # Fear & Greed
    fg_val = fg.get("value", 50)
    if fg_val <= 20:
        insights.append(f"Extreme Fear ({fg_val}) — historically accumulation zone")
    elif fg_val >= 80:
        insights.append(f"Extreme Greed ({fg_val}) — take profit territory")

    return insights


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def store_market_structure(data: dict):
    """Store market structure snapshot."""
    oi = data.get("oi", {})
    funding = data.get("funding", {})
    ls = data.get("long_short", {})
    fg = data.get("fear_greed", {})
    sol = data.get("sol", {})

    execute("""
        INSERT INTO market_structure (btc_oi, btc_oi_change_24h, btc_funding_rate,
            funding_streak, funding_streak_direction, long_pct, short_pct, ls_ratio,
            fear_greed, fear_greed_label, sol_funding_rate, sol_oi)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (oi.get("oi_usd"), None, funding.get("current_rate"),
          funding.get("streak"), funding.get("streak_direction"),
          ls.get("long_pct"), ls.get("short_pct"), ls.get("ratio"),
          fg.get("value"), fg.get("label"),
          sol.get("sol_funding"), sol.get("sol_oi_usd")))
    log.info("Stored market structure snapshot")


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt_usd(v: float) -> str:
    if v >= 1e12: return f"${v/1e12:.1f}T"
    if v >= 1e9: return f"${v/1e9:.1f}B"
    if v >= 1e6: return f"${v/1e6:.1f}M"
    return f"${v:,.0f}"


def format_market_structure_telegram(data: dict) -> str:
    """Format market structure for Telegram."""
    oi = data.get("oi", {})
    funding = data.get("funding", {})
    ls = data.get("long_short", {})
    fg = data.get("fear_greed", {})
    sol = data.get("sol", {})
    insights = data.get("insights", [])

    lines = ["📈 <b>MARKET STRUCTURE</b>", ""]

    # BTC OI
    oi_str = _fmt_usd(oi["oi_usd"]) if oi.get("oi_usd") else "N/A"
    lines.append(f"BTC OI: {oi_str}")

    # Funding
    if funding.get("current_pct") is not None:
        rate = funding["current_pct"]
        streak = funding.get("streak_days", 0)
        direction = funding.get("streak_direction", "")
        streak_str = f" ({streak:.0f}d {direction})" if streak >= 2 else ""
        lines.append(f"Funding: {rate:+.4f}%{streak_str}")

    # L/S ratio
    if ls.get("long_pct"):
        lines.append(f"L/S: {ls['long_pct']}% long / {ls['short_pct']}% short (ratio {ls['ratio']})")

    # Fear & Greed
    if fg.get("value") is not None:
        lines.append(f"F&G: {fg['value']} ({fg.get('label', '')})")

    # SOL derivatives
    if sol.get("sol_funding") is not None:
        sol_rate = sol["sol_funding"] * 100
        sol_oi_str = _fmt_usd(sol["sol_oi_usd"]) if sol.get("sol_oi_usd") else "N/A"
        lines.append(f"SOL: funding {sol_rate:+.4f}% | OI {sol_oi_str}")

    # Insights
    if insights:
        lines.append("")
        for insight in insights:
            lines.append(insight)

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
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_market_structure(send_to_telegram: bool = False) -> dict:
    """Collect all market structure data."""
    ensure_table()

    data = {}
    data["oi"] = fetch_btc_oi()
    data["funding"] = fetch_funding_rate("BTCUSDT")
    data["long_short"] = fetch_long_short_ratio("BTCUSDT")
    data["fear_greed"] = fetch_fear_greed()
    data["sol"] = fetch_sol_derivatives()
    data["insights"] = analyze_market_structure(data)

    store_market_structure(data)

    msg = format_market_structure_telegram(data)
    log.info("Market structure:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)

    return data


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_market_structure(send_to_telegram=send_tg)
    print(format_market_structure_telegram(result))
