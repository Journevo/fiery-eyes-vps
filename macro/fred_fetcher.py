"""macro/fred_fetcher.py — Fetch FRED economic data series and store in DB."""

import time
import requests
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import FRED_API_KEY, get_logger
from db.connection import execute
from macro.config import FRED_SERIES, FRED_DAILY, FRED_WEEKLY, FRED_MONTHLY

log = get_logger("macro.fred")

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


def fetch_and_store(series_id: str, lookback_days: int = 14) -> int:
    """Fetch a FRED series and store in macro_data. Returns rows stored."""
    if not FRED_API_KEY:
        log.warning("FRED_API_KEY not set")
        return 0

    cfg = FRED_SERIES.get(series_id)
    if not cfg:
        log.warning("Unknown series: %s", series_id)
        return 0

    start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    try:
        resp = requests.get(FRED_BASE, params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "observation_start": start_date,
            "sort_order": "desc",
        }, timeout=30)
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
    except Exception as e:
        log.error("FRED fetch %s failed: %s", series_id, e)
        return 0

    stored = 0
    for obs in observations:
        val_str = obs.get("value", ".")
        if val_str == ".":
            continue
        try:
            val = Decimal(val_str)
        except (InvalidOperation, ValueError):
            continue

        obs_date = obs.get("date")
        if not obs_date:
            continue

        try:
            execute(
                """INSERT INTO macro_data (series_id, series_name, country, category, date, value, unit, frequency)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (series_id, date) DO UPDATE SET value = EXCLUDED.value, fetched_at = NOW()""",
                (series_id, cfg["name"], cfg["country"], cfg["category"],
                 obs_date, val, cfg.get("unit", ""), cfg["frequency"]),
            )
            stored += 1
        except Exception as e:
            log.error("DB error %s/%s: %s", series_id, obs_date, e)

    return stored


def fetch_fred_batch(series_list: list[str], lookback_days: int = 14) -> dict:
    """Fetch multiple FRED series with rate limiting."""
    results = {}
    for sid in series_list:
        try:
            n = fetch_and_store(sid, lookback_days)
            results[sid] = n
            if n > 0:
                log.debug("FRED %s: %d rows", sid, n)
        except Exception as e:
            log.error("FRED %s error: %s", sid, e)
            results[sid] = 0
        time.sleep(0.5)
    return results


def fetch_fred_daily():
    """Fetch all daily FRED series (yields, VIX, oil, gold, DXY)."""
    log.info("Fetching %d daily FRED series", len(FRED_DAILY))
    results = fetch_fred_batch(FRED_DAILY, lookback_days=14)
    total = sum(results.values())
    log.info("FRED daily: %d rows from %d series", total, len(results))
    return results


def fetch_fred_weekly():
    """Fetch weekly FRED series (jobless claims, mortgage rates)."""
    log.info("Fetching %d weekly FRED series", len(FRED_WEEKLY))
    results = fetch_fred_batch(FRED_WEEKLY, lookback_days=30)
    total = sum(results.values())
    log.info("FRED weekly: %d rows from %d series", total, len(results))
    return results


def fetch_fred_monthly():
    """Fetch monthly/quarterly FRED series."""
    log.info("Fetching %d monthly FRED series", len(FRED_MONTHLY))
    results = fetch_fred_batch(FRED_MONTHLY, lookback_days=60)
    total = sum(results.values())
    log.info("FRED monthly: %d rows from %d series", total, len(results))
    return results


def fetch_fred_all(lookback_days: int = 14):
    """Fetch all FRED series."""
    log.info("Fetching ALL %d FRED series (lookback=%dd)", len(FRED_SERIES), lookback_days)
    results = fetch_fred_batch(list(FRED_SERIES.keys()), lookback_days)
    total = sum(results.values())
    log.info("FRED all: %d rows from %d series", total, len(results))
    return results


def initial_backfill():
    """First-run: pull 2 years of history for every series."""
    log.info("=== FRED BACKFILL (2 years) ===")
    results = fetch_fred_batch(list(FRED_SERIES.keys()), lookback_days=730)
    total = sum(results.values())
    log.info("FRED backfill complete: %d total rows", total)
    return results


if __name__ == "__main__":
    import sys
    if "--backfill" in sys.argv:
        initial_backfill()
    else:
        fetch_fred_all()
