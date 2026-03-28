"""macro/threshold_monitor.py — Monitor macro thresholds and send alerts."""

import requests
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one
from macro.config import DEFAULT_THRESHOLDS

log = get_logger("macro.threshold")

KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True, "is_persistent": True,
}


def populate_defaults():
    """Insert default thresholds if not already present."""
    for t in DEFAULT_THRESHOLDS:
        execute(
            """INSERT INTO macro_thresholds (name, series_key, warning_value, alert_value, direction, source_voice, context)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (series_key, direction) DO NOTHING""",
            (t["name"], t["series_key"], t["warning_value"], t["alert_value"],
             t["direction"], t["source_voice"], t["context"]),
        )
    log.info("Populated %d default thresholds", len(DEFAULT_THRESHOLDS))


def _get_current(series_key: str):
    """Get current value from dashboard cache."""
    row = execute_one(
        "SELECT current_value FROM macro_dashboard_cache WHERE series_key = %s",
        (series_key,),
    )
    return float(row[0]) if row and row[0] is not None else None


def _get_pct_change(series_key: str, period: str):
    """Get percentage change from dashboard cache."""
    col_map = {"1m": "value_1m", "3m": "value_3m"}
    col = col_map.get(period)
    if not col:
        return None
    row = execute_one(
        f"SELECT current_value, {col} FROM macro_dashboard_cache WHERE series_key = %s",
        (series_key,),
    )
    if not row or None in row or row[1] == 0:
        return None
    return ((float(row[0]) - float(row[1])) / abs(float(row[1]))) * 100


def _check_level(current, threshold_val, direction):
    """Check if current value has crossed a threshold."""
    if current is None or threshold_val is None:
        return False
    tv = float(threshold_val)
    if direction == "above":
        return current >= tv
    elif direction == "below":
        return current <= tv
    elif direction == "below_pct_1m":
        # threshold_val is a percentage, current should be the 1m pct change
        return current is not None  # Handled separately
    elif direction == "below_pct_3m":
        return current is not None
    return False


def check_thresholds(send_alerts: bool = True) -> list[dict]:
    """Check all thresholds against current values. Returns list of triggered thresholds."""
    rows = execute(
        """SELECT id, name, series_key, warning_value, alert_value, direction,
                  source_voice, context, warning_sent, alert_sent
           FROM macro_thresholds""",
        fetch=True,
    )
    if not rows:
        return []

    triggered = []
    for r in rows:
        tid, name, skey, warn_val, alert_val, direction, voice, context, warn_sent, alert_sent = r

        # Get the value to check
        if direction in ("below_pct_1m",):
            check_val = _get_pct_change(skey, "1m")
        elif direction in ("below_pct_3m",):
            check_val = _get_pct_change(skey, "3m")
        else:
            check_val = _get_current(skey)

        if check_val is None:
            continue

        # Update last_checked
        execute("UPDATE macro_thresholds SET last_checked = NOW() WHERE id = %s", (tid,))

        is_alert = _check_level(check_val, alert_val, direction if "pct" not in direction else "below")
        is_warning = _check_level(check_val, warn_val, direction if "pct" not in direction else "below")

        if is_alert and not alert_sent:
            msg = (
                "🔴🔴🔴 <b>THRESHOLD ALERT</b> 🔴🔴🔴\n"
                "<b>%s</b>: %.2f\n"
                "→ CROSSED %.2f alert level\n"
                "→ Source: %s\n"
                "→ %s" % (name, check_val, float(alert_val), voice or "system", context or "")
            )
            if send_alerts:
                _send_telegram(msg)
            execute("UPDATE macro_thresholds SET alert_sent = TRUE, warning_sent = TRUE WHERE id = %s", (tid,))
            triggered.append({"name": name, "level": "alert", "value": check_val})

        elif is_warning and not warn_sent:
            msg = (
                "⚠️ <b>THRESHOLD WARNING</b>\n"
                "<b>%s</b>: %.2f\n"
                "→ Approaching %.2f alert level\n"
                "→ Source: %s\n"
                "→ %s" % (name, check_val, float(alert_val), voice or "system", context or "")
            )
            if send_alerts:
                _send_telegram(msg)
            execute("UPDATE macro_thresholds SET warning_sent = TRUE WHERE id = %s", (tid,))
            triggered.append({"name": name, "level": "warning", "value": check_val})

        elif not is_warning and warn_sent:
            # Reset if value moved back below warning
            execute("UPDATE macro_thresholds SET warning_sent = FALSE, alert_sent = FALSE WHERE id = %s", (tid,))

    log.info("Threshold check: %d triggered out of %d", len(triggered), len(rows))
    return triggered


def format_thresholds_telegram() -> str:
    """Format all thresholds for display."""
    rows = execute(
        """SELECT name, series_key, warning_value, alert_value, direction,
                  source_voice, warning_sent, alert_sent
           FROM macro_thresholds ORDER BY alert_sent DESC, warning_sent DESC, name""",
        fetch=True,
    )
    if not rows:
        return "No thresholds configured."

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["⚠️ <b>THRESHOLD MONITOR</b>\n📅 %s\n" % now_str]

    for r in rows:
        name, skey, warn, alert, direction, voice, w_sent, a_sent = r

        if direction in ("below_pct_1m",):
            current = _get_pct_change(skey, "1m")
            current_str = "%.1f%% 1M" % current if current else "?"
        elif direction in ("below_pct_3m",):
            current = _get_pct_change(skey, "3m")
            current_str = "%.1f%% 3M" % current if current else "?"
        else:
            current = _get_current(skey)
            current_str = _fmt(current, skey)

        warn_str = _fmt(float(warn), skey) if warn else "?"
        alert_str = _fmt(float(alert), skey) if alert else "?"

        if a_sent:
            icon = "🔴"
            status = " ALERT"
        elif w_sent:
            icon = "⚠️"
            status = " APPROACHING"
        else:
            icon = "✅"
            status = ""

        lines.append("%s %s: %s (warn: %s | alert: %s)%s" % (
            icon, name, current_str, warn_str, alert_str, status))

    return "\n".join(lines)


def _fmt(val, skey=""):
    """Format a value nicely."""
    if val is None:
        return "?"
    if abs(val) >= 10000:
        return "%dK" % (val / 1000)
    elif abs(val) >= 100:
        return "%.0f" % val
    elif abs(val) >= 1:
        return "%.2f" % val
    else:
        return "%.3f" % val


def _send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True,
                  "reply_markup": KEYBOARD_JSON},
            timeout=15,
        )
    except Exception as e:
        log.error("Telegram error: %s", e)


if __name__ == "__main__":
    populate_defaults()
    check_thresholds(send_alerts=False)
    print(format_thresholds_telegram())
