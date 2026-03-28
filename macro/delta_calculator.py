"""macro/delta_calculator.py — Calculate historical deltas and populate dashboard cache."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_logger
from db.connection import execute, execute_one
from macro.config import FRED_SERIES, YAHOO_TICKERS, DIRECTION_SENTIMENT

log = get_logger("macro.delta")

LOOKBACKS = {
    "value_1d": 1,
    "value_1w": 7,
    "value_1m": 30,
    "value_3m": 90,
    "value_6m": 180,
    "value_1y": 365,
}


def _get_closest_value(table: str, key_col: str, key_val: str,
                       val_col: str, date_col: str, target_date: str, window: int = 10):
    """Get the closest available value to target_date within a window."""
    row = execute_one(
        f"""SELECT {val_col} FROM {table}
            WHERE {key_col} = %s AND {date_col} BETWEEN %s AND %s
            ORDER BY ABS({date_col} - %s::date) ASC LIMIT 1""",
        (key_val,
         (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=window)).strftime("%Y-%m-%d"),
         (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=window)).strftime("%Y-%m-%d"),
         target_date),
    )
    return float(row[0]) if row and row[0] is not None else None


def _calc_direction(current, val_3m, series_key):
    """Calculate direction and emoji based on 3-month trend."""
    if current is None or val_3m is None or val_3m == 0:
        return "flat", "➡️"
    pct = ((current - val_3m) / abs(val_3m)) * 100
    sentiment = DIRECTION_SENTIMENT.get(series_key)

    if abs(pct) < 2:
        return "flat", "➡️"
    elif pct > 0:
        if sentiment == "down":
            return "rising", "📈⚠️"
        elif sentiment == "up":
            return "rising", "📈✅"
        return "rising", "📈"
    else:
        if sentiment == "up":
            return "falling", "📉⚠️"
        elif sentiment == "down":
            return "falling", "📉✅"
        return "falling", "📉"


def _calc_acceleration(current, val_1m, val_2m):
    """Calculate acceleration: is the trend speeding up or slowing down?"""
    if None in (current, val_1m, val_2m) or val_2m == 0:
        return "stable"
    recent = current - val_1m
    prior = val_1m - val_2m
    if abs(prior) < 0.001:
        return "stable"
    if abs(recent) > abs(prior) * 1.2:
        return "accelerating"
    elif abs(recent) < abs(prior) * 0.8:
        return "decelerating"
    return "stable"


def calculate_fred_deltas():
    """Calculate deltas for all FRED series and update dashboard cache."""
    now = datetime.now(timezone.utc)
    count = 0

    for series_id, cfg in FRED_SERIES.items():
        # Get current value
        row = execute_one(
            "SELECT value, date FROM macro_data WHERE series_id = %s ORDER BY date DESC LIMIT 1",
            (series_id,),
        )
        if not row or row[0] is None:
            continue

        current = float(row[0])
        current_date = row[1].strftime("%Y-%m-%d") if row[1] else now.strftime("%Y-%m-%d")

        # Get historical values
        vals = {}
        for key, days in LOOKBACKS.items():
            target = (now - timedelta(days=days)).strftime("%Y-%m-%d")
            vals[key] = _get_closest_value("macro_data", "series_id", series_id,
                                           "value", "date", target)

        # 2-month ago for acceleration
        target_2m = (now - timedelta(days=60)).strftime("%Y-%m-%d")
        val_2m = _get_closest_value("macro_data", "series_id", series_id,
                                    "value", "date", target_2m)

        direction, _ = _calc_direction(current, vals.get("value_3m"), series_id)
        acceleration = _calc_acceleration(current, vals.get("value_1m"), val_2m)

        try:
            execute(
                """INSERT INTO macro_dashboard_cache
                   (series_key, source, name, category, country, current_value, as_of_date,
                    value_1d, value_1w, value_1m, value_3m, value_6m, value_1y,
                    direction, acceleration, updated_at)
                   VALUES (%s, 'fred', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (series_key) DO UPDATE SET
                     current_value = EXCLUDED.current_value, as_of_date = EXCLUDED.as_of_date,
                     value_1d = EXCLUDED.value_1d, value_1w = EXCLUDED.value_1w,
                     value_1m = EXCLUDED.value_1m, value_3m = EXCLUDED.value_3m,
                     value_6m = EXCLUDED.value_6m, value_1y = EXCLUDED.value_1y,
                     direction = EXCLUDED.direction, acceleration = EXCLUDED.acceleration,
                     updated_at = NOW()""",
                (series_id, cfg["name"], cfg["category"], cfg["country"],
                 current, current_date,
                 vals.get("value_1d"), vals.get("value_1w"), vals.get("value_1m"),
                 vals.get("value_3m"), vals.get("value_6m"), vals.get("value_1y"),
                 direction, acceleration),
            )
            count += 1
        except Exception as e:
            log.error("Dashboard cache error %s: %s", series_id, e)

    log.info("FRED deltas: %d series updated", count)
    return count


def calculate_yahoo_deltas():
    """Calculate deltas for all Yahoo tickers and update dashboard cache."""
    now = datetime.now(timezone.utc)
    count = 0

    for ticker, cfg in YAHOO_TICKERS.items():
        row = execute_one(
            "SELECT price, date FROM market_prices WHERE ticker = %s ORDER BY date DESC LIMIT 1",
            (ticker,),
        )
        if not row or row[0] is None:
            continue

        current = float(row[0])
        current_date = row[1].strftime("%Y-%m-%d") if row[1] else now.strftime("%Y-%m-%d")

        vals = {}
        for key, days in LOOKBACKS.items():
            target = (now - timedelta(days=days)).strftime("%Y-%m-%d")
            vals[key] = _get_closest_value("market_prices", "ticker", ticker,
                                           "price", "date", target)

        target_2m = (now - timedelta(days=60)).strftime("%Y-%m-%d")
        val_2m = _get_closest_value("market_prices", "ticker", ticker,
                                    "price", "date", target_2m)

        direction, _ = _calc_direction(current, vals.get("value_3m"), ticker)
        acceleration = _calc_acceleration(current, vals.get("value_1m"), val_2m)

        try:
            execute(
                """INSERT INTO macro_dashboard_cache
                   (series_key, source, name, category, country, current_value, as_of_date,
                    value_1d, value_1w, value_1m, value_3m, value_6m, value_1y,
                    direction, acceleration, updated_at)
                   VALUES (%s, 'yahoo', %s, %s, 'US', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                   ON CONFLICT (series_key) DO UPDATE SET
                     current_value = EXCLUDED.current_value, as_of_date = EXCLUDED.as_of_date,
                     value_1d = EXCLUDED.value_1d, value_1w = EXCLUDED.value_1w,
                     value_1m = EXCLUDED.value_1m, value_3m = EXCLUDED.value_3m,
                     value_6m = EXCLUDED.value_6m, value_1y = EXCLUDED.value_1y,
                     direction = EXCLUDED.direction, acceleration = EXCLUDED.acceleration,
                     updated_at = NOW()""",
                (ticker, cfg["name"], cfg["category"],
                 current, current_date,
                 vals.get("value_1d"), vals.get("value_1w"), vals.get("value_1m"),
                 vals.get("value_3m"), vals.get("value_6m"), vals.get("value_1y"),
                 direction, acceleration),
            )
            count += 1
        except Exception as e:
            log.error("Dashboard cache error %s: %s", ticker, e)

    log.info("Yahoo deltas: %d tickers updated", count)
    return count


def calculate_all_deltas():
    """Recalculate all deltas."""
    fred_n = calculate_fred_deltas()
    yahoo_n = calculate_yahoo_deltas()
    log.info("All deltas: %d FRED + %d Yahoo = %d total", fred_n, yahoo_n, fred_n + yahoo_n)
    return fred_n + yahoo_n


if __name__ == "__main__":
    calculate_all_deltas()
