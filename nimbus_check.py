"""Nimbus Staleness Check — Fix 9

Checks nimbus_data.py as_of_date on Jingubang VPS.
Warns in every report if >30 days stale.
BTC circuit breaker: if BTC moves >20% from price at last Nimbus update.
"""

from datetime import datetime, date
from config import get_logger

log = get_logger("nimbus_check")

# Hardcoded from Jingubang — updated when nimbus_data.py is edited
NIMBUS_AS_OF_DATE = "2026-02-18"
NIMBUS_BTC_PRICE_AT_UPDATE = 83000  # Approximate BTC price on Feb 18

STALENESS_WARN_DAYS = 30
BTC_CIRCUIT_BREAKER_PCT = 20


def check_nimbus_staleness(current_btc_price: float | None = None) -> dict:
    """Check Nimbus data staleness and BTC circuit breaker."""
    as_of = datetime.strptime(NIMBUS_AS_OF_DATE, "%Y-%m-%d").date()
    days_stale = (date.today() - as_of).days
    is_stale = days_stale > STALENESS_WARN_DAYS

    # BTC circuit breaker
    btc_breaker = False
    btc_move_pct = None
    if current_btc_price and NIMBUS_BTC_PRICE_AT_UPDATE:
        btc_move_pct = abs(current_btc_price - NIMBUS_BTC_PRICE_AT_UPDATE) / NIMBUS_BTC_PRICE_AT_UPDATE * 100
        btc_breaker = btc_move_pct >= BTC_CIRCUIT_BREAKER_PCT

    result = {
        "as_of_date": NIMBUS_AS_OF_DATE,
        "days_stale": days_stale,
        "is_stale": is_stale,
        "btc_circuit_breaker": btc_breaker,
        "btc_move_pct": round(btc_move_pct, 1) if btc_move_pct else None,
    }

    if is_stale:
        log.warning("Nimbus data %d days stale (as_of: %s)", days_stale, NIMBUS_AS_OF_DATE)
    if btc_breaker:
        log.warning("BTC circuit breaker: moved %.1f%% from Nimbus update price", btc_move_pct)

    return result


def format_nimbus_warning(check: dict) -> str | None:
    """Format warning for daily report. Returns None if no warning needed."""
    warnings = []
    if check["is_stale"]:
        warnings.append(f"⚠️ Nimbus data {check['days_stale']}d stale (last: {check['as_of_date']})")
    if check["btc_circuit_breaker"]:
        warnings.append(f"🔴 BTC moved {check['btc_move_pct']}% since Nimbus update — regime gates to minimum")
    return "\n".join(warnings) if warnings else None
