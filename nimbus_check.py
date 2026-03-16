"""Nimbus Staleness Check — v2

Uses nimbus_sync DB instead of hardcoded values.
Checks as_of_date freshness and BTC circuit breaker.
"""

from datetime import datetime, date
from config import get_logger

log = get_logger("nimbus_check")

STALENESS_WARN_DAYS = 30
BTC_CIRCUIT_BREAKER_PCT = 20


def check_nimbus_staleness(current_btc_price: float | None = None) -> dict:
    """Check Nimbus data staleness and BTC circuit breaker."""
    try:
        from nimbus_sync import get_staleness, get_nimbus_section
        staleness = get_staleness()
        as_of_str = staleness["as_of_date"]
        days_stale = staleness["days_stale"]
        is_stale = staleness["is_stale"]
    except Exception:
        # Fallback to old hardcoded method if nimbus_sync not available
        as_of_str = "unknown"
        days_stale = 999
        is_stale = True

    # BTC circuit breaker
    btc_breaker = False
    btc_move_pct = None
    if current_btc_price:
        try:
            from nimbus_sync import get_nimbus_section
            crypto = get_nimbus_section("crypto")
            if crypto and crypto.get("btc_price"):
                nimbus_btc = crypto["btc_price"][-1]
                if nimbus_btc:
                    btc_move_pct = abs(current_btc_price - nimbus_btc) / nimbus_btc * 100
                    btc_breaker = btc_move_pct >= BTC_CIRCUIT_BREAKER_PCT
        except Exception:
            pass

    result = {
        "as_of_date": as_of_str,
        "days_stale": days_stale,
        "is_stale": is_stale,
        "btc_circuit_breaker": btc_breaker,
        "btc_move_pct": round(btc_move_pct, 1) if btc_move_pct else None,
    }

    if is_stale:
        log.warning("Nimbus data %d days stale (as_of: %s)", days_stale, as_of_str)
    if btc_breaker:
        log.warning("BTC circuit breaker: moved %.1f%% since Nimbus update", btc_move_pct)

    return result


def format_nimbus_warning(check: dict) -> str | None:
    """Format warning for daily report. Returns None if no warning needed."""
    warnings = []
    if check["is_stale"]:
        warnings.append(f"⚠️ Nimbus data {check['days_stale']}d stale (last: {check['as_of_date']})")
    if check["btc_circuit_breaker"]:
        warnings.append(f"🔴 BTC moved {check['btc_move_pct']}% since Nimbus update — regime gates to minimum")
    return "\n".join(warnings) if warnings else None
