"""Performance Tracker — logs alerts with feature vectors, auto-checks price
   at 7d/30d/90d, categorizes outcomes, generates weekly report.

Outcome categories:
  moon:   >5x from alert price
  strong: >2x
  profit: >1.2x
  flat:   0.8x–1.2x
  loss:   <0.8x
  rekt:   <0.5x
"""

import json
from datetime import date, datetime, timedelta, timezone
from config import get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json
from telegram_bot.alerts import _send

log = get_logger("reports.performance")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

def _classify_outcome(entry_price: float, current_price: float) -> str:
    """Classify price change into outcome category."""
    if entry_price <= 0:
        return "unknown"
    ratio = current_price / entry_price
    if ratio >= 5.0:
        return "moon"
    if ratio >= 2.0:
        return "strong"
    if ratio >= 1.2:
        return "profit"
    if ratio >= 0.8:
        return "flat"
    if ratio >= 0.5:
        return "loss"
    return "rekt"


# ---------------------------------------------------------------------------
# Alert logging
# ---------------------------------------------------------------------------

def log_alert(token_id: int, alert_score: float, price_at_alert: float,
              feature_vector: dict):
    """Log an alert with complete feature vector for future performance tracking."""
    try:
        execute(
            """INSERT INTO performance_log
               (token_id, alert_date, alert_score, price_at_alert)
               VALUES (%s, NOW(), %s, %s)""",
            (token_id, alert_score, price_at_alert),
        )
        # Also store feature vector in alerts table
        execute(
            """INSERT INTO alerts
               (token_id, type, severity, feature_vector_json, price_at_alert)
               VALUES (%s, 'performance_log', 'info', %s, %s)""",
            (token_id, json.dumps(feature_vector, default=str), price_at_alert),
        )
        log.info("Performance log entry created for token_id=%d (score=%.1f, price=%.6f)",
                 token_id, alert_score, price_at_alert)
    except Exception as e:
        log.error("Failed to log performance alert for token_id=%d: %s", token_id, e)


# ---------------------------------------------------------------------------
# Price check + outcome update
# ---------------------------------------------------------------------------

def _fetch_current_price(mint: str) -> float | None:
    """Get current price from DexScreener."""
    try:
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        return float(best.get("priceUsd") or 0)
    except Exception as e:
        log.debug("Price fetch failed for %s: %s", mint, e)
        return None


def update_performance_prices():
    """Auto-check prices at 7d, 30d, 90d after each logged alert."""
    log.info("=== Updating performance prices ===")

    now = datetime.now(timezone.utc)
    updated = 0

    # Find entries needing 7d update
    try:
        rows = execute(
            """SELECT pl.id, t.contract_address, pl.alert_date, pl.price_at_alert
               FROM performance_log pl
               JOIN tokens t ON t.id = pl.token_id
               WHERE pl.price_7d IS NULL
                 AND pl.alert_date <= NOW() - INTERVAL '7 days'""",
            fetch=True,
        )
        for pl_id, mint, alert_date, entry_price in (rows or []):
            price = _fetch_current_price(mint)
            if price is not None:
                outcome = _classify_outcome(entry_price, price)
                execute(
                    "UPDATE performance_log SET price_7d = %s WHERE id = %s",
                    (price, pl_id),
                )
                updated += 1
    except Exception as e:
        log.error("7d price update error: %s", e)

    # Find entries needing 30d update
    try:
        rows = execute(
            """SELECT pl.id, t.contract_address, pl.price_at_alert
               FROM performance_log pl
               JOIN tokens t ON t.id = pl.token_id
               WHERE pl.price_30d IS NULL
                 AND pl.alert_date <= NOW() - INTERVAL '30 days'""",
            fetch=True,
        )
        for pl_id, mint, entry_price in (rows or []):
            price = _fetch_current_price(mint)
            if price is not None:
                execute(
                    "UPDATE performance_log SET price_30d = %s WHERE id = %s",
                    (price, pl_id),
                )
                updated += 1
    except Exception as e:
        log.error("30d price update error: %s", e)

    # Find entries needing 90d update + final outcome
    try:
        rows = execute(
            """SELECT pl.id, t.contract_address, pl.price_at_alert
               FROM performance_log pl
               JOIN tokens t ON t.id = pl.token_id
               WHERE pl.price_90d IS NULL
                 AND pl.alert_date <= NOW() - INTERVAL '90 days'""",
            fetch=True,
        )
        for pl_id, mint, entry_price in (rows or []):
            price = _fetch_current_price(mint)
            if price is not None:
                outcome = _classify_outcome(entry_price, price)
                execute(
                    "UPDATE performance_log SET price_90d = %s, outcome_category = %s WHERE id = %s",
                    (price, outcome, pl_id),
                )
                updated += 1
    except Exception as e:
        log.error("90d price update error: %s", e)

    log.info("Updated %d performance price entries", updated)


# ---------------------------------------------------------------------------
# Weekly performance report
# ---------------------------------------------------------------------------

def generate_weekly_report() -> str:
    """Generate weekly performance report and send to Telegram."""
    log.info("=== Generating weekly performance report ===")

    lines = [
        "📈 <b>WEEKLY PERFORMANCE REPORT</b>",
        f"Week ending: {date.today().isoformat()}",
        "",
    ]

    # Overall stats
    lines.extend(_overall_stats_section())

    # Best picks
    lines.extend(_best_picks_section())

    # Worst picks
    lines.extend(_worst_picks_section())

    # Engine performance
    lines.extend(_engine_performance_section())

    # Outcome distribution
    lines.extend(_outcome_distribution_section())

    report = "\n".join(lines)
    _send(report)

    log.info("Weekly performance report sent")
    return report


def _overall_stats_section() -> list[str]:
    """Overall alert accuracy stats."""
    lines = ["<b>Overall Stats</b>"]

    try:
        row = execute_one(
            """SELECT
                 COUNT(*) as total,
                 COUNT(outcome_category) as with_outcome,
                 COUNT(*) FILTER (WHERE outcome_category IN ('moon','strong','profit')) as wins,
                 COUNT(*) FILTER (WHERE outcome_category IN ('loss','rekt')) as losses,
                 AVG(CASE WHEN price_7d > 0 AND price_at_alert > 0
                     THEN price_7d / price_at_alert ELSE NULL END) as avg_7d_return,
                 AVG(CASE WHEN price_30d > 0 AND price_at_alert > 0
                     THEN price_30d / price_at_alert ELSE NULL END) as avg_30d_return
               FROM performance_log""",
        )
        if row:
            total, with_outcome, wins, losses, avg_7d, avg_30d = row
            win_rate = (wins / with_outcome * 100) if with_outcome > 0 else 0
            lines.append(f"  Alerts logged: {total}")
            lines.append(f"  With outcome: {with_outcome}")
            if with_outcome > 0:
                lines.append(f"  Win rate: {win_rate:.0f}% ({wins}W / {losses}L)")
            if avg_7d:
                lines.append(f"  Avg 7d return: {avg_7d:.2f}x")
            if avg_30d:
                lines.append(f"  Avg 30d return: {avg_30d:.2f}x")
        else:
            lines.append("  No performance data yet")
    except Exception as e:
        log.error("Overall stats error: %s", e)
        lines.append("  ⚠️ Stats unavailable")

    lines.append("")
    return lines


def _best_picks_section() -> list[str]:
    """Top 3 best-performing alerts."""
    lines = ["<b>🏆 Best Picks</b>"]

    try:
        rows = execute(
            """SELECT t.symbol, pl.alert_score, pl.price_at_alert,
                      COALESCE(pl.price_90d, pl.price_30d, pl.price_7d) as latest_price,
                      pl.outcome_category
               FROM performance_log pl
               JOIN tokens t ON t.id = pl.token_id
               WHERE pl.price_at_alert > 0
                 AND COALESCE(pl.price_90d, pl.price_30d, pl.price_7d) > 0
               ORDER BY COALESCE(pl.price_90d, pl.price_30d, pl.price_7d) / pl.price_at_alert DESC
               LIMIT 3""",
            fetch=True,
        )
        if rows:
            for sym, score, entry, latest, outcome in rows:
                multiple = latest / entry if entry > 0 else 0
                lines.append(f"  <code>{sym}</code>: {multiple:.1f}x (score={score:.0f}, {outcome or 'pending'})")
        else:
            lines.append("  No completed picks yet")
    except Exception as e:
        log.error("Best picks error: %s", e)
        lines.append("  ⚠️ Data unavailable")

    lines.append("")
    return lines


def _worst_picks_section() -> list[str]:
    """Bottom 3 worst-performing alerts."""
    lines = ["<b>📉 Worst Picks</b>"]

    try:
        rows = execute(
            """SELECT t.symbol, pl.alert_score, pl.price_at_alert,
                      COALESCE(pl.price_90d, pl.price_30d, pl.price_7d) as latest_price,
                      pl.outcome_category
               FROM performance_log pl
               JOIN tokens t ON t.id = pl.token_id
               WHERE pl.price_at_alert > 0
                 AND COALESCE(pl.price_90d, pl.price_30d, pl.price_7d) > 0
               ORDER BY COALESCE(pl.price_90d, pl.price_30d, pl.price_7d) / pl.price_at_alert ASC
               LIMIT 3""",
            fetch=True,
        )
        if rows:
            for sym, score, entry, latest, outcome in rows:
                multiple = latest / entry if entry > 0 else 0
                lines.append(f"  <code>{sym}</code>: {multiple:.2f}x (score={score:.0f}, {outcome or 'pending'})")
        else:
            lines.append("  No completed picks yet")
    except Exception as e:
        log.error("Worst picks error: %s", e)
        lines.append("  ⚠️ Data unavailable")

    lines.append("")
    return lines


def _engine_performance_section() -> list[str]:
    """Per-engine performance (which engine's high scores correlate with good outcomes)."""
    lines = ["<b>🔧 Engine Performance</b>"]

    try:
        # This is a simplified proxy: avg return for tokens with high momentum vs high adoption etc
        for engine, col in [("Momentum", "momentum_score"), ("Adoption", "adoption_score"),
                            ("Infra", "infra_score")]:
            row = execute_one(
                f"""SELECT AVG(CASE WHEN pl.price_30d > 0 AND pl.price_at_alert > 0
                        THEN pl.price_30d / pl.price_at_alert ELSE NULL END),
                       COUNT(*)
                    FROM performance_log pl
                    JOIN scores_daily s ON s.token_id = pl.token_id
                        AND s.date = pl.alert_date::date
                    WHERE s.{col} >= 70""",
            )
            if row and row[1] > 0:
                avg_ret = row[0]
                count = row[1]
                if avg_ret:
                    lines.append(f"  {engine} (≥70): {avg_ret:.2f}x avg return ({count} signals)")
                else:
                    lines.append(f"  {engine} (≥70): {count} signals, returns pending")
            else:
                lines.append(f"  {engine}: insufficient data")
    except Exception as e:
        log.error("Engine performance error: %s", e)
        lines.append("  ⚠️ Engine data unavailable")

    lines.append("")
    return lines


def _outcome_distribution_section() -> list[str]:
    """Distribution of outcomes across categories."""
    lines = ["<b>📊 Outcome Distribution</b>"]

    try:
        rows = execute(
            """SELECT outcome_category, COUNT(*)
               FROM performance_log
               WHERE outcome_category IS NOT NULL
               GROUP BY outcome_category
               ORDER BY COUNT(*) DESC""",
            fetch=True,
        )
        if rows:
            icons = {"moon": "🌙", "strong": "💪", "profit": "✅",
                     "flat": "➡️", "loss": "📉", "rekt": "💀"}
            for category, count in rows:
                icon = icons.get(category, "❓")
                lines.append(f"  {icon} {category}: {count}")
        else:
            lines.append("  No categorized outcomes yet")
    except Exception as e:
        log.error("Outcome distribution error: %s", e)
        lines.append("  ⚠️ Data unavailable")

    return lines
