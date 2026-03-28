"""Portfolio Tracker — Task 7 of Fiery Eyes v5.1

Telegram commands: /bought, /sold, /portfolio, /pnl
Tracks actual positions vs target allocations.
"""

import requests
from datetime import datetime, timezone
from config import COINGECKO_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("portfolio")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# Target allocations (of deployed capital)
TARGET_ALLOC = {
    "JUP": 25, "HYPE": 20, "RENDER": 17, "BONK": 15,
}

# CoinGecko IDs
CG_IDS = {
    "BTC": "bitcoin", "SOL": "solana",
    "JUP": "jupiter-exchange-solana", "HYPE": "hyperliquid",
    "RENDER": "render-token", "BONK": "bonk",
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_tables():
    """Create positions and trades tables if they don't exist."""
    execute("""
        CREATE TABLE IF NOT EXISTS portfolio_positions (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            amount REAL NOT NULL,
            entry_price REAL NOT NULL,
            entry_date TEXT NOT NULL,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS portfolio_trades (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            direction TEXT NOT NULL,
            amount REAL NOT NULL,
            price REAL NOT NULL,
            trade_date TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Trade logging
# ---------------------------------------------------------------------------
def log_buy(token: str, amount: float, price: float, date_str: str | None = None):
    """Log a purchase: adds position + trade record."""
    today = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    token = token.upper()

    execute("""
        INSERT INTO portfolio_positions (token, amount, entry_price, entry_date)
        VALUES (%s, %s, %s, %s)
    """, (token, amount, price, today))

    execute("""
        INSERT INTO portfolio_trades (token, direction, amount, price, trade_date)
        VALUES (%s, 'BUY', %s, %s, %s)
    """, (token, amount, price, today))

    log.info("Bought %s %s at $%s", amount, token, price)


def log_sell(token: str, amount: float, price: float, date_str: str | None = None):
    """Log a sale: removes from positions (FIFO) + trade record."""
    today = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    token = token.upper()

    execute("""
        INSERT INTO portfolio_trades (token, direction, amount, price, trade_date)
        VALUES (%s, 'SELL', %s, %s, %s)
    """, (token, amount, price, today))

    # Reduce position (FIFO)
    remaining = amount
    positions = execute("""
        SELECT id, amount FROM portfolio_positions
        WHERE token = %s AND amount > 0
        ORDER BY entry_date ASC
    """, (token,), fetch=True)

    for pos_id, pos_amount in (positions or []):
        if remaining <= 0:
            break
        reduce = min(remaining, pos_amount)
        new_amount = pos_amount - reduce
        if new_amount <= 0:
            execute("DELETE FROM portfolio_positions WHERE id = %s", (pos_id,))
        else:
            execute("UPDATE portfolio_positions SET amount = %s WHERE id = %s", (new_amount, pos_id))
        remaining -= reduce

    log.info("Sold %s %s at $%s", amount, token, price)


# ---------------------------------------------------------------------------
# Portfolio view
# ---------------------------------------------------------------------------
def _fetch_current_prices(tokens: list) -> dict:
    """Fetch current prices for multiple tokens."""
    ids = [CG_IDS[t] for t in tokens if t in CG_IDS]
    if not ids:
        return {}
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": ",".join(ids), "vs_currencies": "usd"}
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-key"] = COINGECKO_API_KEY
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        result = {}
        for token, cg_id in CG_IDS.items():
            if cg_id in data and "usd" in data[cg_id]:
                result[token] = data[cg_id]["usd"]
        return result
    except Exception as e:
        log.error("Price fetch failed: %s", e)
        return {}


def get_portfolio() -> dict:
    """Get current portfolio summary."""
    ensure_tables()

    # Aggregate positions by token
    rows = execute("""
        SELECT token,
               SUM(amount) as total_amount,
               SUM(amount * entry_price) / NULLIF(SUM(amount), 0) as avg_entry
        FROM portfolio_positions
        WHERE amount > 0
        GROUP BY token
        ORDER BY token
    """, fetch=True)

    if not rows:
        return {"positions": [], "total_deployed": 0, "dry_powder": 0}

    tokens = [r[0] for r in rows]
    current_prices = _fetch_current_prices(tokens)

    positions = []
    total_value = 0

    for token, amount, avg_entry in rows:
        current = current_prices.get(token, avg_entry)
        value = amount * current
        cost = amount * avg_entry
        pnl = value - cost
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0

        positions.append({
            "token": token,
            "amount": amount,
            "avg_entry": avg_entry,
            "current_price": current,
            "value": value,
            "cost": cost,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
        })
        total_value += value

    # Calculate actual allocations
    for p in positions:
        p["actual_pct"] = round(p["value"] / total_value * 100, 1) if total_value > 0 else 0
        p["target_pct"] = TARGET_ALLOC.get(p["token"], 0)

    return {
        "positions": positions,
        "total_deployed": total_value,
    }


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------
def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    elif price >= 0.001:
        return f"${price:.4f}"
    else:
        return f"${price:.2e}"


def format_portfolio_telegram(portfolio: dict) -> str:
    """Format portfolio for Telegram."""
    positions = portfolio["positions"]
    if not positions:
        return "💼 <b>PORTFOLIO</b>\nNo positions. Use /bought TOKEN AMOUNT PRICE to add."

    lines = ["💼 <b>PORTFOLIO</b>", ""]
    header = f"{'Token':<7s} {'Held':>10s} {'Entry':>9s} {'Current':>9s} {'PnL':>10s} {'Tgt%':>5s} {'Act%':>5s}"
    lines.append(f"<pre>{header}")

    total_cost = 0
    total_value = 0

    for p in positions:
        amount_str = f"{p['amount']:,.0f}" if p['amount'] >= 1 else f"{p['amount']:.0f}"
        pnl_str = f"${p['pnl']:+,.0f} ({p['pnl_pct']:+.0f}%)"
        tgt = f"{p['target_pct']}%" if p['target_pct'] > 0 else "—"
        act = f"{p['actual_pct']}%"
        arrow = " ⬇" if p['target_pct'] > 0 and p['actual_pct'] < p['target_pct'] * 0.5 else ""

        lines.append(
            f"{p['token']:<7s} {amount_str:>10s} {_fmt_price(p['avg_entry']):>9s} "
            f"{_fmt_price(p['current_price']):>9s} {pnl_str:>10s} {tgt:>5s} {act:>5s}{arrow}"
        )
        total_cost += p["cost"]
        total_value += p["value"]

    lines.append("</pre>")

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    lines.append(f"Total deployed: ${total_value:,.0f} | PnL: ${total_pnl:+,.0f} ({total_pnl_pct:+.1f}%)")

    return "\n".join(lines)


def format_pnl_telegram(portfolio: dict) -> str:
    """Format PnL summary for Telegram."""
    positions = portfolio["positions"]
    if not positions:
        return "📊 No positions to show PnL"

    lines = ["📊 <b>UNREALISED PnL</b>", ""]
    for p in positions:
        emoji = "🟢" if p["pnl"] >= 0 else "🔴"
        lines.append(f"{emoji} {p['token']}: ${p['pnl']:+,.2f} ({p['pnl_pct']:+.1f}%)")

    total_pnl = sum(p["pnl"] for p in positions)
    total_cost = sum(p["cost"] for p in positions)
    total_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    lines.append(f"\n{'🟢' if total_pnl >= 0 else '🔴'} Total: ${total_pnl:+,.2f} ({total_pct:+.1f}%)")

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
# CLI / main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    ensure_tables()

    args = sys.argv[1:]
    send_tg = "--telegram" in args
    args = [a for a in args if a != "--telegram"]

    if not args:
        print("Usage:")
        print("  python portfolio.py bought TOKEN AMOUNT PRICE")
        print("  python portfolio.py sold TOKEN AMOUNT PRICE")
        print("  python portfolio.py portfolio")
        print("  python portfolio.py pnl")
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "bought" and len(args) >= 4:
        token = args[1].upper()
        amount = float(args[2])
        price = float(args[3])
        log_buy(token, amount, price)
        print(f"✅ Bought {amount} {token} at ${price}")
        if send_tg:
            send_telegram(f"✅ Bought {amount:,.0f} {token} at {_fmt_price(price)}")

    elif cmd == "sold" and len(args) >= 4:
        token = args[1].upper()
        amount = float(args[2])
        price = float(args[3])
        log_sell(token, amount, price)
        print(f"✅ Sold {amount} {token} at ${price}")
        if send_tg:
            send_telegram(f"✅ Sold {amount:,.0f} {token} at {_fmt_price(price)}")

    elif cmd == "portfolio":
        portfolio = get_portfolio()
        msg = format_portfolio_telegram(portfolio)
        print(msg)
        if send_tg:
            send_telegram(msg)

    elif cmd == "pnl":
        portfolio = get_portfolio()
        msg = format_pnl_telegram(portfolio)
        print(msg)
        if send_tg:
            send_telegram(msg)

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
