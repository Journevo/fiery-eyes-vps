"""Recommendation Ledger — Task 6 of Fiery Eyes v5.1

Logs every recommendation with full state snapshot for backtesting.
Provides /ledger command to review past recommendations vs outcomes.
"""

import json
import requests
from datetime import datetime, timedelta, timezone
from config import COINGECKO_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("rec_ledger")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}



# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_tables():
    """Create recommendations and state_snapshots tables."""
    execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            token TEXT NOT NULL,
            action TEXT NOT NULL,
            price_at_rec REAL,
            score TEXT,
            conviction TEXT,
            btc_price REAL,
            bear_progress REAL,
            fg_index INTEGER,
            deploy_pct INTEGER,
            signals TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS state_snapshots (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            snapshot JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Logging recommendations
# ---------------------------------------------------------------------------
def log_recommendation(
    token: str,
    action: str,
    price: float,
    btc_price: float,
    bear_progress: float,
    score: str | None = None,
    conviction: str = "MEDIUM",
    fg_index: int | None = None,
    deploy_pct: int | None = None,
    signals: list | None = None,
):
    """Log a single recommendation to the database."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    signals_json = json.dumps(signals) if signals else None

    execute("""
        INSERT INTO recommendations (date, token, action, price_at_rec, score, conviction,
                                     btc_price, bear_progress, fg_index, deploy_pct, signals)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (today, token, action, price, score, conviction,
          btc_price, bear_progress, fg_index, deploy_pct, signals_json))
    log.info("Logged recommendation: %s %s at $%s", action, token, price)


def log_state_snapshot(snapshot: dict):
    """Log a full state snapshot for backtesting."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    execute("""
        INSERT INTO state_snapshots (date, snapshot)
        VALUES (%s, %s)
    """, (today, json.dumps(snapshot)))
    log.info("Stored state snapshot for %s", today)


def log_daily_recommendations(prices: dict, cycle: dict, liq: dict | None):
    """Auto-log recommendations from the daily report.
    Called after daily_report.py generates its report."""
    from watchlist import CORE_TOKENS

    btc_price = cycle.get("btc_price", 0)
    bear_progress = cycle.get("bear_progress_pct", 0)

    # Determine actions for each core token based on zone and bear progress
    for symbol in CORE_TOKENS:
        if symbol not in prices:
            continue
        d = prices[symbol]
        pct_ath = d["pct_from_ath"]
        zone = d["zone"]

        # Determine action
        if pct_ath <= -85:
            action = "FOCUS"
            conviction = "HIGH"
        elif pct_ath <= -70:
            action = "WATCH"
            conviction = "MEDIUM"
        elif "Mid" in zone:
            if bear_progress < 60:
                action = "PATIENCE"
                conviction = "LOW"
            else:
                action = "WATCH"
                conviction = "MEDIUM"
        else:
            action = "AVOID"
            conviction = "LOW"

        signals = []
        if "Deep" in zone:
            signals.append("deep_value")
        if bear_progress > 40:
            signals.append(f"bear_{bear_progress:.0f}pct")
        if liq and liq.get("fred_regime") == "EXPANDING":
            signals.append("fred_expanding")

        log_recommendation(
            token=symbol,
            action=action,
            price=d["price"],
            btc_price=btc_price,
            bear_progress=bear_progress,
            conviction=conviction,
            signals=signals,
        )

    # Store full state snapshot
    snapshot = {
        "btc_price": btc_price,
        "bear_progress": bear_progress,
        "prices": {s: {"price": p["price"], "pct_from_ath": p["pct_from_ath"], "zone": p["zone"]}
                   for s, p in prices.items()},
        "liquidity": {
            "us_net_liq": liq.get("us_net_liq") if liq else None,
            "fred_regime": liq.get("fred_regime") if liq else None,
            "fred_slope": liq.get("fred_slope") if liq else None,
            "m2_lag_status": liq.get("m2_lag_status") if liq else None,
            "alignment": liq.get("alignment") if liq else None,
        },
    }
    log_state_snapshot(snapshot)


# ---------------------------------------------------------------------------
# Review recommendations
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


def _get_current_price(token: str) -> float | None:
    """Get current price from CoinGecko for comparison."""
    cg_ids = {
        "JUP": "jupiter-exchange-solana",
        "HYPE": "hyperliquid",
        "RENDER": "render-token",
        "BONK": "bonk",
        "BTC": "bitcoin",
        "SOL": "solana",
    }
    cg_id = cg_ids.get(token)
    if not cg_id:
        return None
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": cg_id, "vs_currencies": "usd"}
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-key"] = COINGECKO_API_KEY
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        return data.get(cg_id, {}).get("usd")
    except Exception:
        return None


def review_recommendations(days_ago: int = 7) -> list:
    """Compare recommendations from N days ago vs actual price now."""
    target_date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    rows = execute("""
        SELECT token, action, price_at_rec, conviction, btc_price, bear_progress
        FROM recommendations
        WHERE date = %s
        ORDER BY token
    """, (target_date,), fetch=True)

    if not rows:
        log.info("No recommendations found for %s", target_date)
        return []

    results = []
    for row in rows:
        token, action, price_at_rec, conviction, btc_price, bear_progress = row
        current = _get_current_price(token)
        if current and price_at_rec:
            pnl_pct = round((current - price_at_rec) / price_at_rec * 100, 1)
            results.append({
                "token": token,
                "action": action,
                "price_at_rec": price_at_rec,
                "current_price": current,
                "pnl_pct": pnl_pct,
                "conviction": conviction,
                "was_correct": (action in ("FOCUS", "WATCH") and pnl_pct > 0) or
                               (action in ("AVOID", "PATIENCE") and pnl_pct < 0),
            })

    return results


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def format_ledger_telegram(days_ago: int = 7) -> str:
    """Format ledger review for Telegram."""
    results = review_recommendations(days_ago)
    if not results:
        return f"📒 No recommendations found for {days_ago}d ago"

    lines = [f"📒 <b>LEDGER REVIEW ({days_ago}d ago)</b>", ""]
    for r in results:
        emoji = "✅" if r["was_correct"] else "❌"
        lines.append(
            f"{emoji} {r['token']}: {r['action']} at ${r['price_at_rec']:.4f} → "
            f"${r['current_price']:.4f} ({r['pnl_pct']:+.1f}%) [{r['conviction']}]"
        )

    correct = sum(1 for r in results if r["was_correct"])
    total = len(results)
    lines.append(f"\nAccuracy: {correct}/{total} ({correct/total*100:.0f}%)")

    return "\n".join(lines)


def format_recent_recs_telegram(limit: int = 10) -> str:
    """Format last N recommendations for /ledger command."""
    rows = execute("""
        SELECT date, token, action, price_at_rec, conviction
        FROM recommendations
        ORDER BY created_at DESC
        LIMIT %s
    """, (limit,), fetch=True)

    if not rows:
        return "📒 No recommendations logged yet"

    lines = [f"📒 <b>LAST {limit} RECOMMENDATIONS</b>", ""]
    for date, token, action, price, conviction in rows:
        lines.append(f"{date} | {action:<9s} {token:<6s} {_fmt_price(price)} [{conviction}]")

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
# Main entry points
# ---------------------------------------------------------------------------
def run_log_daily(send_to_telegram: bool = False):
    """Run after daily report: log recommendations and state snapshot."""
    ensure_tables()

    from btc_cycle import fetch_btc_price, calculate_cycle
    from watchlist import fetch_prices
    from liquidity import run_liquidity_tracker

    btc_price = fetch_btc_price()
    if not btc_price:
        log.error("Cannot log — BTC price unavailable")
        return

    cycle = calculate_cycle(btc_price)
    prices = fetch_prices()
    liq = run_liquidity_tracker()

    log_daily_recommendations(prices, cycle, liq)

    if send_to_telegram:
        send_telegram(format_recent_recs_telegram())


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv

    if "--review" in sys.argv:
        days = 7
        for arg in sys.argv:
            if arg.startswith("--days="):
                days = int(arg.split("=")[1])
        ensure_tables()
        print(format_ledger_telegram(days))
    elif "--ledger" in sys.argv:
        ensure_tables()
        msg = format_recent_recs_telegram()
        print(msg)
        if send_tg:
            send_telegram(msg)
    else:
        run_log_daily(send_to_telegram=send_tg)
