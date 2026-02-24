"""Shadow Trading — logs every trade the system WOULD have made.

No real execution. Phantom PnL tracking.

Updates shadow_trades table:
- On entry signal: create shadow trade with entry price
- Every 15min: update current_price and current_pnl_pct
- On exit signal: close trade with final_pnl_pct
- Daily summary: total phantom PnL, win rate, best/worst
"""

from datetime import datetime, timezone
from decimal import Decimal
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("shadow.tracker")


def open_shadow_trade(token_address: str, entry_source: str, entry_reason: str,
                      entry_price: float, entry_mcap: float,
                      health_score: float | None, confidence: float | None,
                      position_size_pct: float) -> int | None:
    """Open a new shadow trade.

    Returns: trade_id or None
    """
    # Get token symbol
    symbol = _get_symbol(token_address)

    try:
        row = execute_one(
            """INSERT INTO shadow_trades
               (token_address, token_symbol, entry_source, entry_reason,
                entry_time, entry_price, entry_mcap,
                entry_health_score, entry_confidence,
                position_size_pct, current_price, current_pnl_pct,
                status, phases_entered)
               VALUES (%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s,0,'open',1)
               RETURNING id""",
            (token_address, symbol, entry_source, entry_reason,
             Decimal(str(entry_price)) if entry_price else None,
             Decimal(str(entry_mcap)) if entry_mcap else None,
             Decimal(str(health_score)) if health_score else None,
             Decimal(str(confidence)) if confidence else None,
             Decimal(str(position_size_pct)),
             Decimal(str(entry_price)) if entry_price else None),
        )
        trade_id = row[0] if row else None
        log.info("Shadow trade opened: #%s %s %s at $%.8f",
                 trade_id, symbol or '?', entry_source, entry_price)
        return trade_id
    except Exception as e:
        log.error("Failed to open shadow trade: %s", e)
        return None


def update_shadow_trades():
    """Update all open shadow trades with current prices and PnL."""
    try:
        trades = execute(
            """SELECT id, token_address, token_symbol, entry_price
               FROM shadow_trades WHERE status = 'open'""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to fetch open shadow trades: %s", e)
        return

    if not trades:
        log.debug("No open shadow trades to update")
        return

    updated = 0
    for trade_id, token_addr, symbol, entry_price in trades:
        try:
            current_price = _get_current_price(token_addr)
            if current_price and entry_price and float(entry_price) > 0:
                pnl_pct = ((current_price - float(entry_price)) / float(entry_price)) * 100
            else:
                pnl_pct = 0

            # Get current health score
            health_score = None
            try:
                from health_score.engine import score_token
                hs = score_token(token_addr, symbol)
                health_score = hs.get('scaled_score')
            except Exception:
                pass

            execute(
                """UPDATE shadow_trades SET
                     current_price = %s,
                     current_pnl_pct = %s,
                     current_health_score = %s
                   WHERE id = %s""",
                (Decimal(str(current_price)) if current_price else None,
                 Decimal(str(round(pnl_pct, 2))),
                 Decimal(str(health_score)) if health_score else None,
                 trade_id),
            )
            updated += 1
        except Exception as e:
            log.error("Failed to update shadow trade #%s: %s", trade_id, e)

    log.info("Updated %d/%d open shadow trades", updated, len(trades))


def close_shadow_trade(trade_id: int, exit_reason: str,
                       exit_price: float | None = None):
    """Close a shadow trade with final PnL."""
    try:
        row = execute_one(
            "SELECT token_address, token_symbol, entry_price FROM shadow_trades WHERE id = %s",
            (trade_id,),
        )
        if not row:
            log.error("Shadow trade #%s not found", trade_id)
            return

        token_addr, symbol, entry_price = row

        if exit_price is None:
            exit_price = _get_current_price(token_addr) or 0

        if entry_price and float(entry_price) > 0 and exit_price:
            final_pnl = ((exit_price - float(entry_price)) / float(entry_price)) * 100
        else:
            final_pnl = 0

        execute(
            """UPDATE shadow_trades SET
                 exit_time = NOW(),
                 exit_price = %s,
                 exit_reason = %s,
                 final_pnl_pct = %s,
                 status = 'closed'
               WHERE id = %s""",
            (Decimal(str(exit_price)) if exit_price else None,
             exit_reason, Decimal(str(round(final_pnl, 2))), trade_id),
        )
        log.info("Shadow trade #%s closed: %s %s PnL=%.1f%% reason=%s",
                 trade_id, symbol or '?', token_addr[:12], final_pnl, exit_reason)
    except Exception as e:
        log.error("Failed to close shadow trade #%s: %s", trade_id, e)


def get_shadow_summary() -> dict:
    """Get shadow trading summary statistics.

    Returns: {total, open, closed, win_rate, total_pnl, best, worst}
    """
    try:
        row = execute_one(
            """SELECT
                 COUNT(*) as total,
                 COUNT(*) FILTER (WHERE status = 'open') as open_count,
                 COUNT(*) FILTER (WHERE status = 'closed') as closed_count,
                 COUNT(*) FILTER (WHERE status = 'closed' AND final_pnl_pct > 0) as wins,
                 AVG(final_pnl_pct) FILTER (WHERE status = 'closed') as avg_pnl,
                 SUM(final_pnl_pct) FILTER (WHERE status = 'closed') as total_pnl,
                 MAX(final_pnl_pct) FILTER (WHERE status = 'closed') as best,
                 MIN(final_pnl_pct) FILTER (WHERE status = 'closed') as worst
               FROM shadow_trades""",
        )
        if row:
            total, open_count, closed, wins, avg_pnl, total_pnl, best, worst = row
            win_rate = (wins / closed * 100) if closed and closed > 0 else 0
            return {
                'total': total or 0,
                'open': open_count or 0,
                'closed': closed or 0,
                'wins': wins or 0,
                'win_rate': round(win_rate, 1),
                'avg_pnl': round(float(avg_pnl or 0), 1),
                'total_pnl': round(float(total_pnl or 0), 1),
                'best': round(float(best or 0), 1),
                'worst': round(float(worst or 0), 1),
            }
    except Exception as e:
        log.error("Failed to get shadow summary: %s", e)

    return {'total': 0, 'open': 0, 'closed': 0, 'wins': 0,
            'win_rate': 0, 'avg_pnl': 0, 'total_pnl': 0, 'best': 0, 'worst': 0}


def get_shadow_report() -> str:
    """Generate formatted shadow trading report for Telegram."""
    summary = get_shadow_summary()

    lines = [
        "📊 <b>SHADOW TRADING REPORT</b>",
        "",
        f"Total trades: {summary['total']}",
        f"Open: {summary['open']} | Closed: {summary['closed']}",
        f"Win rate: {summary['win_rate']:.0f}% ({summary['wins']}/{summary['closed']})",
        f"Avg PnL: {summary['avg_pnl']:+.1f}%",
        f"Total PnL: {summary['total_pnl']:+.1f}%",
        f"Best: {summary['best']:+.1f}% | Worst: {summary['worst']:+.1f}%",
    ]

    # Add open positions
    try:
        trades = execute(
            """SELECT token_symbol, entry_source, current_pnl_pct,
                      phases_entered, position_size_pct
               FROM shadow_trades WHERE status = 'open'
               ORDER BY entry_time DESC LIMIT 5""",
            fetch=True,
        )
        if trades:
            lines.append("")
            lines.append("<b>Open Positions:</b>")
            for sym, source, pnl, phases, size in trades:
                pnl_val = float(pnl or 0)
                emoji = "🟢" if pnl_val > 0 else "🔴"
                lines.append(
                    f"  {emoji} ${sym or '?'}: {pnl_val:+.1f}% "
                    f"(P{phases}, {float(size or 0):.0f}%)"
                )
    except Exception:
        pass

    return "\n".join(lines)


def _get_current_price(token_address: str) -> float | None:
    """Get current price from DexScreener."""
    try:
        from quality_gate.helpers import get_json
        data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
        pairs = data.get("pairs", [])
        if pairs:
            return float(pairs[0].get("priceUsd", 0) or 0)
    except Exception:
        pass
    return None


def _get_symbol(token_address: str) -> str | None:
    """Get token symbol from DB or DexScreener."""
    try:
        row = execute_one(
            "SELECT symbol FROM tokens WHERE contract_address = %s",
            (token_address,),
        )
        if row:
            return row[0]
    except Exception:
        pass

    try:
        from quality_gate.helpers import get_json
        data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
        pairs = data.get("pairs", [])
        if pairs:
            return pairs[0].get("baseToken", {}).get("symbol")
    except Exception:
        pass
    return None
