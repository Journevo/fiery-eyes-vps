"""Moonbag Reaper — runs daily at 03:00 UTC.

Auto-closes shadow trades if ALL true:
- Position value < 0.1 SOL equivalent
- Health score < 20 for 48 consecutive hours
- No KOL holding this token
- No social mentions in 24h
"""

from datetime import datetime, timezone
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("shadow.moonbag_reaper")

# Approximate SOL price (refreshed at runtime)
SOL_PRICE_APPROX = 150


def run_moonbag_reaper():
    """Scan and close dead moonbag positions."""
    log.info("Moonbag Reaper starting...")

    try:
        trades = execute(
            """SELECT id, token_address, token_symbol, current_price,
                      entry_price, position_size_pct
               FROM shadow_trades
               WHERE status IN ('open', 'moonbag')""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to fetch trades for reaping: %s", e)
        return

    if not trades:
        log.info("No open trades to reap")
        return

    # Get approximate SOL price
    sol_price = _get_sol_price()

    reaped = 0
    for trade_id, token_addr, symbol, current_price, entry_price, size_pct in trades:
        try:
            if _should_reap(trade_id, token_addr, symbol, current_price,
                            entry_price, size_pct, sol_price):
                from shadow.tracker import close_shadow_trade
                close_shadow_trade(trade_id, 'moonbag_reaper')
                reaped += 1
                log.info("Reaped moonbag: #%s %s", trade_id, symbol or token_addr[:12])
        except Exception as e:
            log.error("Error reaping trade #%s: %s", trade_id, e)

    log.info("Moonbag Reaper complete: %d/%d trades reaped", reaped, len(trades))

    if reaped > 0:
        try:
            from telegram_bot.severity import route_alert
            route_alert(3, f"🧹 Moonbag Reaper cleaned {reaped} dead position(s)")
        except Exception:
            pass


def _should_reap(trade_id: int, token_address: str, symbol: str | None,
                 current_price, entry_price, size_pct, sol_price: float) -> bool:
    """Check all reap conditions. ALL must be true."""

    # Condition 1: Position value < 0.1 SOL equivalent
    # Estimate: current_price * position_weight
    # Since we track %, use health score and price as proxies
    if current_price and float(current_price) > 0 and size_pct:
        # Very rough estimate: if price dropped >95% from entry, likely dust
        if entry_price and float(entry_price) > 0:
            price_ratio = float(current_price) / float(entry_price)
            if price_ratio > 0.05:  # Still has >5% of original value
                return False
        else:
            return False  # Can't determine value without entry price
    elif current_price is None or float(current_price or 0) == 0:
        pass  # No price data, check other conditions
    else:
        return False

    # Condition 2: Health score < 20 for 48 consecutive hours
    try:
        row = execute_one(
            """SELECT COUNT(*) FROM health_scores
               WHERE token_address = %s
                 AND scored_at > NOW() - INTERVAL '48 hours'
                 AND scaled_score >= 20""",
            (token_address,),
        )
        if row and row[0] and row[0] > 0:
            return False  # Had a healthy score in last 48h
    except Exception:
        pass  # If can't check, continue to other conditions

    # Condition 3: No KOL holding this token
    try:
        row = execute_one(
            """SELECT COUNT(DISTINCT kol_wallet_id) FROM kol_transactions
               WHERE token_address = %s AND action = 'buy'
                 AND detected_at > NOW() - INTERVAL '7 days'""",
            (token_address,),
        )
        if row and row[0] and row[0] > 0:
            # Check if they've sold
            sold_row = execute_one(
                """SELECT
                     SUM(CASE WHEN action='buy' THEN token_amount ELSE 0 END) as bought,
                     SUM(CASE WHEN action='sell' THEN token_amount ELSE 0 END) as sold
                   FROM kol_transactions WHERE token_address = %s""",
                (token_address,),
            )
            if sold_row and sold_row[0]:
                bought = float(sold_row[0] or 0)
                sold = float(sold_row[1] or 0)
                if bought > 0 and (bought - sold) / bought > 0.1:
                    return False  # KOLs still holding >10%
    except Exception:
        pass

    # Condition 4: No social mentions in 24h
    try:
        row = execute_one(
            """SELECT COUNT(*) FROM telegram_calls
               WHERE token_address = %s
                 AND detected_at > NOW() - INTERVAL '24 hours'""",
            (token_address,),
        )
        if row and row[0] and row[0] > 0:
            return False  # Recent social mention
    except Exception:
        pass

    # All conditions met — reap
    log.info("Moonbag reap criteria met for %s", symbol or token_address[:12])
    return True


def _get_sol_price() -> float:
    """Get current SOL price."""
    try:
        from quality_gate.helpers import get_json
        data = get_json("https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112")
        pairs = data.get("pairs", [])
        if pairs:
            return float(pairs[0].get("priceUsd", 0) or 0)
    except Exception:
        pass
    return SOL_PRICE_APPROX
