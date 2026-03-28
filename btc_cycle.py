"""BTC Cycle Tracker — Task 1 of Fiery Eyes v5.1

Tracks BTC's position in the current bear market cycle:
- Days since peak, drawdown %, bear progress %
- Drawdown scenarios (-52%, -60%, -77%)
- Estimated bottom date based on historical averages
- Stores daily snapshots in btc_cycle table
"""

import html
import requests
from datetime import datetime, date, timezone
from config import COINGECKO_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("btc_cycle")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# ---------------------------------------------------------------------------
# Historical cycle data
# ---------------------------------------------------------------------------
CYCLES = [
    {"bottom": "2011-11-01", "peak": "2013-12-01", "bottom_price": 2, "peak_price": 1127, "drawdown": 85, "peak_to_bottom_days": 400},
    {"bottom": "2015-01-01", "peak": "2017-12-01", "bottom_price": 172, "peak_price": 19783, "drawdown": 84, "peak_to_bottom_days": 365},
    {"bottom": "2018-12-01", "peak": "2021-11-01", "bottom_price": 3200, "peak_price": 69000, "drawdown": 77, "peak_to_bottom_days": 375},
    {"bottom": "2022-11-21", "peak": "2025-10-06", "bottom_price": 15479, "peak_price": 126000, "drawdown": None, "peak_to_bottom_days": None},
]

CURRENT_CYCLE = CYCLES[-1]
PEAK_DATE = datetime.strptime(CURRENT_CYCLE["peak"], "%Y-%m-%d").date()
PEAK_PRICE = CURRENT_CYCLE["peak_price"]

# Average peak-to-bottom duration from completed cycles
AVG_PEAK_TO_BOTTOM = round(sum(c["peak_to_bottom_days"] for c in CYCLES[:-1]) / len(CYCLES[:-1]))

# Drawdown scenarios
SCENARIOS = [
    {"label": "consensus", "drawdown_pct": 52, "target_price": round(PEAK_PRICE * 0.48)},
    {"label": "diminishing", "drawdown_pct": 60, "target_price": round(PEAK_PRICE * 0.40)},
    {"label": "full cycle", "drawdown_pct": 77, "target_price": round(PEAK_PRICE * 0.23)},
]


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    """Create btc_cycle table if it doesn't exist."""
    execute("""
        CREATE TABLE IF NOT EXISTS btc_cycle (
            date TEXT PRIMARY KEY,
            btc_price REAL,
            days_since_peak INTEGER,
            drawdown_pct REAL,
            bear_progress_pct REAL
        )
    """)


# ---------------------------------------------------------------------------
# Price fetching
# ---------------------------------------------------------------------------
def fetch_btc_price() -> float | None:
    """Fetch current BTC price from CoinGecko."""
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "bitcoin", "vs_currencies": "usd"}
        headers = {}
        if COINGECKO_API_KEY:
            # Demo API key uses x-cg-demo-key header
            headers["x-cg-demo-key"] = COINGECKO_API_KEY
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        price = data.get("bitcoin", {}).get("usd")
        if price:
            log.info("BTC price: $%s", f"{price:,.0f}")
            return float(price)
        log.warning("BTC price not found in response: %s", data)
        return None
    except Exception as e:
        log.error("Failed to fetch BTC price: %s", e)
        return None


# ---------------------------------------------------------------------------
# Cycle calculations
# ---------------------------------------------------------------------------
def calculate_cycle(btc_price: float, today: date | None = None) -> dict:
    """Calculate all cycle metrics for a given BTC price."""
    if today is None:
        today = date.today()

    days_since_peak = (today - PEAK_DATE).days
    drawdown_pct = round((1 - btc_price / PEAK_PRICE) * 100, 1)
    bear_progress_pct = round(min(days_since_peak / AVG_PEAK_TO_BOTTOM * 100, 100), 1)
    days_remaining = max(0, AVG_PEAK_TO_BOTTOM - days_since_peak)

    return {
        "date": today.isoformat(),
        "btc_price": btc_price,
        "peak_price": PEAK_PRICE,
        "peak_date": PEAK_DATE.isoformat(),
        "days_since_peak": days_since_peak,
        "drawdown_pct": drawdown_pct,
        "bear_progress_pct": bear_progress_pct,
        "avg_peak_to_bottom": AVG_PEAK_TO_BOTTOM,
        "days_remaining": days_remaining,
        "scenarios": SCENARIOS,
    }


def store_cycle(cycle: dict):
    """Upsert today's cycle data into the database."""
    execute("""
        INSERT INTO btc_cycle (date, btc_price, days_since_peak, drawdown_pct, bear_progress_pct)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            btc_price = EXCLUDED.btc_price,
            days_since_peak = EXCLUDED.days_since_peak,
            drawdown_pct = EXCLUDED.drawdown_pct,
            bear_progress_pct = EXCLUDED.bear_progress_pct
    """, (cycle["date"], cycle["btc_price"], cycle["days_since_peak"],
          cycle["drawdown_pct"], cycle["bear_progress_pct"]))
    log.info("Stored cycle data for %s", cycle["date"])


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _progress_bar(pct: float, length: int = 20) -> str:
    """Generate a text progress bar."""
    filled = round(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


def format_cycle_telegram(cycle: dict) -> str:
    """Format cycle data for Telegram (HTML parse mode)."""
    bar = _progress_bar(cycle["bear_progress_pct"])
    scenarios_str = " | ".join(
        f"-{s['drawdown_pct']}% = ${s['target_price']:,}" for s in cycle["scenarios"]
    )

    price_str = f"${cycle['btc_price']:,.0f}"
    peak_str = f"${cycle['peak_price']:,.0f}"
    peak_date_str = datetime.strptime(cycle["peak_date"], "%Y-%m-%d").strftime("%b %-d")
    days_remaining = cycle["days_remaining"]

    msg = (
        f"📊 <b>BTC CYCLE</b>\n"
        f"  Peak: {peak_str} ({peak_date_str}) | Now: {price_str} (-{cycle['drawdown_pct']}%)\n"
        f"  Bear: {bar} {cycle['bear_progress_pct']:.0f}% (~{days_remaining}d to est. bottom)\n"
        f"  Scenarios: {scenarios_str}"
    )
    return msg


def send_telegram(text: str):
    """Send a message via Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram not configured, skipping send")
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
        if resp.status_code == 200:
            log.info("Telegram message sent")
        else:
            log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
    except Exception as e:
        log.error("Telegram send error: %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_cycle_tracker(send_to_telegram: bool = False) -> dict | None:
    """Fetch BTC price, calculate cycle, store, optionally send to Telegram."""
    ensure_table()

    btc_price = fetch_btc_price()
    if btc_price is None:
        log.error("Cannot run cycle tracker without BTC price")
        return None

    cycle = calculate_cycle(btc_price)
    store_cycle(cycle)

    msg = format_cycle_telegram(cycle)
    log.info("Cycle report:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)

    return cycle


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_cycle_tracker(send_to_telegram=send_tg)
    if result:
        print(format_cycle_telegram(result))
    else:
        print("ERROR: Failed to run cycle tracker")
        sys.exit(1)
