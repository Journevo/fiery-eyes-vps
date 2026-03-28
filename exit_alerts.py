"""Exit Alert System — Task 20 of Fiery Eyes v5.1

Per-position exit management:
- Stop loss triggers per tier (Core -30%, Satellite -20%, Lottery -40%)
- Take profit levels (2x sell 20%, 3x sell 30%)
- Thesis review after 25% drawdown for 30+ days
- Portfolio circuit breaker: total deployed down 25% → regime gates to minimum
"""

import requests
from datetime import datetime, timedelta, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("exit_alerts")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# ---------------------------------------------------------------------------
# Exit rules per tier
# ---------------------------------------------------------------------------
EXIT_RULES = {
    "core": {
        "tokens": ["JUP", "HYPE", "RENDER", "BONK"],
        "stop_loss_pct": -30,
        "take_profit": [
            {"multiple": 2, "sell_pct": 20},
            {"multiple": 3, "sell_pct": 30},
        ],
        "thesis_review_drawdown": -25,
        "thesis_review_days": 30,
    },
    "satellite": {
        "tokens": ["PUMP", "PENGU"],
        "stop_loss_pct": -20,
        "take_profit": [
            {"multiple": 2, "sell_pct": 30},
            {"multiple": 3, "sell_pct": 40},
        ],
        "thesis_review_drawdown": -15,
        "thesis_review_days": 14,
    },
    "lottery": {
        "tokens": ["FARTCOIN"],
        "stop_loss_pct": -40,
        "take_profit": [
            {"multiple": 3, "sell_pct": 50},
        ],
        "thesis_review_drawdown": None,
        "thesis_review_days": None,
    },
}

# Portfolio circuit breaker
CIRCUIT_BREAKER_PCT = -25


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS exit_alerts (
            id SERIAL PRIMARY KEY,
            token TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            entry_price REAL,
            current_price REAL,
            pnl_pct REAL,
            message TEXT,
            triggered_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Position checking
# ---------------------------------------------------------------------------
def get_positions_with_prices() -> list:
    """Get all portfolio positions with current prices."""
    try:
        rows = execute("""
            SELECT token, SUM(amount) as total_amount,
                   SUM(amount * entry_price) / NULLIF(SUM(amount), 0) as avg_entry,
                   MIN(entry_date) as first_entry
            FROM portfolio_positions
            WHERE amount > 0
            GROUP BY token
        """, fetch=True)

        if not rows:
            return []

        # Fetch current prices
        from watchlist import fetch_prices
        prices = fetch_prices()

        positions = []
        for token, amount, avg_entry, first_entry in rows:
            current = prices.get(token, {}).get("price")
            if not current or not avg_entry:
                continue

            pnl_pct = (current - avg_entry) / avg_entry * 100
            days_held = (datetime.now().date() - datetime.strptime(first_entry, "%Y-%m-%d").date()).days if first_entry else 0

            # Determine tier
            tier = None
            for tier_name, rules in EXIT_RULES.items():
                if token in rules["tokens"]:
                    tier = tier_name
                    break

            positions.append({
                "token": token,
                "amount": amount,
                "avg_entry": avg_entry,
                "current_price": current,
                "pnl_pct": round(pnl_pct, 1),
                "days_held": days_held,
                "value": amount * current,
                "cost": amount * avg_entry,
                "tier": tier or "unknown",
            })

        return positions
    except Exception as e:
        log.error("Position fetch failed: %s", e)
        return []


def check_exit_alerts(positions: list) -> list:
    """Check all positions against exit rules. Return triggered alerts."""
    alerts = []

    for pos in positions:
        tier = pos["tier"]
        rules = EXIT_RULES.get(tier)
        if not rules:
            continue

        token = pos["token"]
        pnl = pos["pnl_pct"]
        days = pos["days_held"]

        # Stop loss check
        if pnl <= rules["stop_loss_pct"]:
            alerts.append({
                "token": token,
                "type": "STOP_LOSS",
                "severity": "urgent",
                "pnl_pct": pnl,
                "threshold": rules["stop_loss_pct"],
                "message": (f"🚨 STOP LOSS — ${token}\n"
                           f"  PnL: {pnl:+.1f}% (threshold: {rules['stop_loss_pct']}%)\n"
                           f"  Entry: ${pos['avg_entry']:.4f} → Now: ${pos['current_price']:.4f}\n"
                           f"  Tier: {tier} | Action: EXIT position"),
            })

        # Take profit check
        for tp in rules["take_profit"]:
            if pos["current_price"] >= pos["avg_entry"] * tp["multiple"]:
                alerts.append({
                    "token": token,
                    "type": "TAKE_PROFIT",
                    "severity": "action",
                    "pnl_pct": pnl,
                    "multiple": tp["multiple"],
                    "sell_pct": tp["sell_pct"],
                    "message": (f"🎯 TAKE PROFIT — ${token}\n"
                               f"  {tp['multiple']}x reached! PnL: {pnl:+.1f}%\n"
                               f"  Entry: ${pos['avg_entry']:.4f} → Now: ${pos['current_price']:.4f}\n"
                               f"  Action: sell {tp['sell_pct']}% of position"),
                })

        # Thesis review check
        if (rules.get("thesis_review_drawdown") and
            pnl <= rules["thesis_review_drawdown"] and
            rules.get("thesis_review_days") and
            days >= rules["thesis_review_days"]):
            alerts.append({
                "token": token,
                "type": "THESIS_REVIEW",
                "severity": "warn",
                "pnl_pct": pnl,
                "days_held": days,
                "message": (f"⚠️ THESIS REVIEW — ${token}\n"
                           f"  Down {pnl:+.1f}% for {days} days\n"
                           f"  Threshold: {rules['thesis_review_drawdown']}% for {rules['thesis_review_days']}d\n"
                           f"  Action: re-evaluate position thesis"),
            })

    return alerts


def check_circuit_breaker(positions: list) -> dict | None:
    """Check portfolio-level circuit breaker."""
    if not positions:
        return None

    total_value = sum(p["value"] for p in positions)
    total_cost = sum(p["cost"] for p in positions)

    if total_cost <= 0:
        return None

    total_pnl_pct = (total_value - total_cost) / total_cost * 100

    if total_pnl_pct <= CIRCUIT_BREAKER_PCT:
        return {
            "type": "CIRCUIT_BREAKER",
            "severity": "critical",
            "total_pnl_pct": round(total_pnl_pct, 1),
            "message": (f"🔴 CIRCUIT BREAKER TRIGGERED\n"
                       f"  Portfolio down {total_pnl_pct:+.1f}% (threshold: {CIRCUIT_BREAKER_PCT}%)\n"
                       f"  Total value: ${total_value:,.0f} (cost: ${total_cost:,.0f})\n"
                       f"  Action: REGIME GATES TO MINIMUM (25% deploy max)"),
        }

    return None


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def format_exit_status_telegram(positions: list, alerts: list, circuit: dict | None) -> str:
    """Format exit alert status for Telegram."""
    if not positions:
        return "🛡️ <b>EXIT ALERTS</b>\nNo open positions."

    lines = ["🛡️ <b>EXIT ALERTS</b>", ""]

    # Position status
    lines.append("<pre>")
    lines.append(f"{'Token':<7s} {'PnL':>7s} {'Days':>5s} {'Tier':<10s} {'Status':<12s}")
    for p in positions:
        rules = EXIT_RULES.get(p["tier"], {})
        sl = rules.get("stop_loss_pct", 0)
        distance_to_sl = p["pnl_pct"] - sl

        if p["pnl_pct"] <= sl:
            status = "🚨 STOP"
        elif distance_to_sl < 10:
            status = f"⚠️ {distance_to_sl:+.0f}% to SL"
        elif p["pnl_pct"] >= 100:
            status = "🎯 2x+"
        else:
            status = "✅ OK"

        lines.append(f"{p['token']:<7s} {p['pnl_pct']:>+6.1f}% {p['days_held']:>5d} {p['tier']:<10s} {status}")
    lines.append("</pre>")

    # Active alerts
    if alerts:
        lines.append("")
        for a in alerts:
            lines.append(a["message"])

    # Circuit breaker
    if circuit:
        lines.append("")
        lines.append(circuit["message"])

    if not alerts and not circuit:
        lines.append("\n✅ No exit alerts triggered")

    return "\n".join(lines)


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        chunks = [text] if len(text) <= 4000 else [text[:4000], text[4000:]]
        for chunk in chunks:
            requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID, "text": chunk,
                "parse_mode": "HTML", "disable_web_page_preview": True,
                    "reply_markup": _KEYBOARD_JSON,
            }, timeout=15)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_exit_check(send_to_telegram: bool = False) -> dict:
    """Check all positions against exit rules."""
    ensure_table()

    positions = get_positions_with_prices()
    alerts = check_exit_alerts(positions)
    circuit = check_circuit_breaker(positions)

    # Store triggered alerts
    for a in alerts:
        pos = next((p for p in positions if p["token"] == a["token"]), {})
        execute("""
            INSERT INTO exit_alerts (token, alert_type, entry_price, current_price, pnl_pct, message)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (a["token"], a["type"], pos.get("avg_entry"), pos.get("current_price"),
              a["pnl_pct"], a["message"]))

    msg = format_exit_status_telegram(positions, alerts, circuit)
    log.info("Exit check: %d positions, %d alerts, circuit=%s",
             len(positions), len(alerts), "TRIGGERED" if circuit else "OK")

    if send_to_telegram:
        send_telegram(msg)
        # Urgent alerts get separate messages
        for a in alerts:
            if a["severity"] in ("urgent", "critical"):
                send_telegram(a["message"])
        if circuit:
            send_telegram(circuit["message"])

    return {"positions": positions, "alerts": alerts, "circuit_breaker": circuit}


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_exit_check(send_to_telegram=send_tg)
    print(format_exit_status_telegram(result["positions"], result["alerts"], result["circuit_breaker"]))
