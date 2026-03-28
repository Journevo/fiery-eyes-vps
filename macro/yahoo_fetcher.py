"""macro/yahoo_fetcher.py — Fetch Yahoo Finance market data and store in DB."""

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_logger
from db.connection import execute
from macro.config import YAHOO_TICKERS

log = get_logger("macro.yahoo")


def fetch_and_store_batch(lookback_days: int = 14) -> dict:
    """Fetch all Yahoo tickers using batch download and store in market_prices."""
    import yfinance as yf

    ticker_list = list(YAHOO_TICKERS.keys())
    start_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    log.info("Fetching %d Yahoo tickers (lookback=%dd)", len(ticker_list), lookback_days)

    results = {}
    # Process in batches of 20 to avoid rate limits
    batch_size = 20
    for i in range(0, len(ticker_list), batch_size):
        batch = ticker_list[i:i + batch_size]
        try:
            data = yf.download(
                batch, start=start_date, auto_adjust=True,
                threads=True, progress=False,
            )
            if data.empty:
                log.warning("Empty data for batch %d-%d", i, i + len(batch))
                continue

            for ticker in batch:
                cfg = YAHOO_TICKERS[ticker]
                try:
                    if len(batch) == 1:
                        closes = data["Close"].dropna()
                    else:
                        if ticker not in data["Close"].columns:
                            log.debug("No data for %s", ticker)
                            results[ticker] = 0
                            continue
                        closes = data["Close"][ticker].dropna()

                    stored = 0
                    prev = None
                    for dt, price in closes.items():
                        if price is None or price != price:  # NaN check
                            continue
                        try:
                            price_dec = Decimal(str(round(float(price), 4)))
                        except (InvalidOperation, ValueError):
                            continue

                        day_pct = None
                        if prev is not None and prev != 0:
                            day_pct = round(float((price_dec - prev) / prev * 100), 2)

                        date_str = dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt)[:10]
                        try:
                            execute(
                                """INSERT INTO market_prices (ticker, name, category, price, prev_close, day_change_pct, date)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                                   ON CONFLICT (ticker, date) DO UPDATE SET
                                     price = EXCLUDED.price, prev_close = EXCLUDED.prev_close,
                                     day_change_pct = EXCLUDED.day_change_pct, fetched_at = NOW()""",
                                (ticker, cfg["name"], cfg["category"], price_dec, prev, day_pct, date_str),
                            )
                            stored += 1
                        except Exception as e:
                            log.error("DB error %s/%s: %s", ticker, date_str, e)

                        prev = price_dec

                    results[ticker] = stored
                except Exception as e:
                    log.error("Error processing %s: %s", ticker, e)
                    results[ticker] = 0

        except Exception as e:
            log.error("Batch download error: %s", e)
            for t in batch:
                results[t] = 0

        if i + batch_size < len(ticker_list):
            time.sleep(2)

    total = sum(results.values())
    log.info("Yahoo: %d rows from %d tickers (%d failed)",
             total, len(results), sum(1 for v in results.values() if v == 0))
    return results


def fetch_yahoo_all(lookback_days: int = 14):
    """Fetch all Yahoo tickers."""
    return fetch_and_store_batch(lookback_days)


def initial_backfill():
    """First-run: pull 2 years of history."""
    log.info("=== YAHOO BACKFILL (2 years) ===")
    return fetch_and_store_batch(lookback_days=730)


if __name__ == "__main__":
    import sys
    if "--backfill" in sys.argv:
        initial_backfill()
    else:
        fetch_yahoo_all()
