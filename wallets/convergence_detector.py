"""Smart Money Cross-Source Convergence Detector.

Detects when 3+ independent wallets buy the same token within a 6-hour window.
Sources: KOL wallets (kol_transactions) + GMGN wallets (gmgn_wallets) + X intelligence.

Weighted scoring:
  - Tier A (KOL Tier 1, GMGN Tier A): 1.5x
  - Tier B (KOL Tier 2, GMGN Tier B): 1.0x
  - Tier C (GMGN Tier C):             0.5x

Thresholds (weighted score):
  - 5-8:   WATCHING (DB only, no Telegram alert)
  - 8-12:  EMERGING → H-Fire (Tier 2)
  - 12+:   STRONG CONVERGENCE → H-Fire (Tier 2)

Smart Money Radar section added to Huoyan pulse.

Run: python main.py smart-radar
Schedule: every 1 hour via scanner/scheduler.py
"""

import json
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from config import get_logger
from db.connection import execute, execute_one
from telegram_bot.severity import route_alert
from monitoring.degraded import record_run_completion

log = get_logger("wallets.convergence")

# Detection parameters
CONVERGENCE_WINDOW_HOURS = 6
MIN_WALLETS = 3

# Tier weight multipliers
TIER_WEIGHTS = {
    "A": 1.5,
    "B": 1.0,
    "C": 0.5,
}

# Convergence level thresholds (on weighted score)
LEVEL_WATCHING = 5.0
LEVEL_EMERGING = 8.0
LEVEL_STRONG = 12.0


def _get_recent_kol_buys(hours: int = 6) -> list[dict]:
    """Get recent KOL wallet buy transactions."""
    try:
        rows = execute(
            """SELECT kt.token_address, kt.token_symbol, kt.amount_usd,
                      kt.detected_at, kw.wallet_address, kw.name, kw.tier
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               WHERE kt.action = 'buy'
                 AND kt.detected_at > NOW() - INTERVAL '%s hours'
                 AND kw.is_active = TRUE
               ORDER BY kt.detected_at DESC""" % hours,
            fetch=True,
        )
        return [
            {
                "token_address": r[0],
                "token_symbol": r[1],
                "amount_usd": float(r[2] or 0),
                "detected_at": r[3],
                "wallet_address": r[4],
                "display_name": r[5],
                "tier": "A" if r[6] == 1 else "B",  # KOL Tier 1→A, Tier 2→B
                "source": "kol",
            }
            for r in rows
        ] if rows else []
    except Exception as e:
        log.error("Failed to fetch KOL buys: %s", e)
        return []


def _get_recent_x_signals(hours: int = 6) -> list[dict]:
    """Get recent X intelligence buy/accumulation signals."""
    try:
        rows = execute(
            """SELECT token_address, token_symbol, amount_usd,
                      detected_at, wallet_address, source_handle,
                      signal_strength
               FROM x_intelligence
               WHERE parsed_type IN ('accumulation', 'whale_flow', 'multi_kol_buy', 'conviction_buy')
                 AND detected_at > NOW() - INTERVAL '%s hours'
                 AND token_address IS NOT NULL
                 AND token_address != ''
               ORDER BY detected_at DESC""" % hours,
            fetch=True,
        )
        return [
            {
                "token_address": r[0],
                "token_symbol": r[1],
                "amount_usd": float(r[2] or 0),
                "detected_at": r[3],
                "wallet_address": r[4] or "",
                "display_name": f"@{r[5]}",
                "tier": "A" if r[6] == "strong" else "B",
                "source": "x_intel",
            }
            for r in rows
        ] if rows else []
    except Exception as e:
        log.error("Failed to fetch X signals: %s", e)
        return []


def _get_gmgn_recent_activity(hours: int = 6) -> list[dict]:
    """Check if any GMGN-tracked wallets appear in recent KOL transactions.

    GMGN wallets are cross-referenced against kol_transactions
    (if they've been added to kol_wallets for monitoring).
    """
    try:
        rows = execute(
            """SELECT kt.token_address, kt.token_symbol, kt.amount_usd,
                      kt.detected_at, gw.wallet_address, gw.display_name, gw.tier
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               JOIN gmgn_wallets gw ON gw.wallet_address = kw.wallet_address
               WHERE kt.action = 'buy'
                 AND kt.detected_at > NOW() - INTERVAL '%s hours'
                 AND gw.is_active = TRUE
               ORDER BY kt.detected_at DESC""" % hours,
            fetch=True,
        )
        return [
            {
                "token_address": r[0],
                "token_symbol": r[1],
                "amount_usd": float(r[2] or 0),
                "detected_at": r[3],
                "wallet_address": r[4],
                "display_name": r[5],
                "tier": r[6],  # Already A/B/C from gmgn_wallets
                "source": "gmgn",
            }
            for r in rows
        ] if rows else []
    except Exception as e:
        log.debug("GMGN activity query: %s", e)
        return []


def _dedupe_by_wallet(signals: list[dict]) -> list[dict]:
    """Deduplicate signals so each wallet only counts once per token."""
    seen = set()
    deduped = []
    for s in signals:
        key = (s["wallet_address"] or s["display_name"], s["token_address"])
        if key not in seen:
            seen.add(key)
            deduped.append(s)
    return deduped


def detect_convergences() -> list[dict]:
    """Scan for cross-source convergence signals.

    Returns list of convergence events, each with:
      - token_address, token_symbol
      - wallet_count, weighted_score
      - convergence_level (WATCHING/EMERGING/STRONG CONVERGENCE)
      - wallets: list of participating wallets
    """
    # Gather signals from all sources
    kol_buys = _get_recent_kol_buys(CONVERGENCE_WINDOW_HOURS)
    x_signals = _get_recent_x_signals(CONVERGENCE_WINDOW_HOURS)
    gmgn_activity = _get_gmgn_recent_activity(CONVERGENCE_WINDOW_HOURS)

    all_signals = kol_buys + x_signals + gmgn_activity
    all_signals = _dedupe_by_wallet(all_signals)

    if not all_signals:
        return []

    # Group by token
    by_token = defaultdict(list)
    for s in all_signals:
        by_token[s["token_address"]].append(s)

    convergences = []
    for token_addr, signals in by_token.items():
        if len(signals) < MIN_WALLETS:
            continue

        # Calculate weighted score
        weighted_score = sum(TIER_WEIGHTS.get(s["tier"], 0.5) for s in signals)

        # Determine convergence level
        if weighted_score >= LEVEL_STRONG:
            level = "STRONG CONVERGENCE"
            level_emoji = "🔴🔴🔴"
        elif weighted_score >= LEVEL_EMERGING:
            level = "EMERGING"
            level_emoji = "🟡🟡"
        elif weighted_score >= LEVEL_WATCHING:
            level = "WATCHING"
            level_emoji = "👀"
        else:
            continue  # Below minimum threshold

        # Get token symbol (first non-null)
        token_symbol = next((s["token_symbol"] for s in signals if s.get("token_symbol")), None)

        # Sort by detection time
        signals.sort(key=lambda s: s["detected_at"] if s["detected_at"] else datetime.min)
        first_buy = signals[0]["detected_at"]
        last_buy = signals[-1]["detected_at"]

        # Build wallet list
        wallet_list = [
            {
                "address": s["wallet_address"],
                "display_name": s["display_name"],
                "tier": s["tier"],
                "amount_usd": s["amount_usd"],
                "source": s["source"],
                "detected_at": s["detected_at"].isoformat() if s["detected_at"] else None,
            }
            for s in signals
        ]

        # Source diversity bonus — signals from multiple sources are stronger
        sources = set(s["source"] for s in signals)
        if len(sources) >= 2:
            weighted_score *= 1.2  # 20% bonus for cross-source confirmation

        convergences.append({
            "token_address": token_addr,
            "token_symbol": token_symbol,
            "wallet_count": len(signals),
            "weighted_score": round(weighted_score, 1),
            "convergence_level": level,
            "level_emoji": level_emoji,
            "wallets": wallet_list,
            "first_buy_at": first_buy,
            "last_buy_at": last_buy,
            "sources": list(sources),
            "source_count": len(sources),
        })

    # Sort by weighted score descending
    convergences.sort(key=lambda c: c["weighted_score"], reverse=True)

    return convergences


def _store_convergence(conv: dict) -> bool:
    """Store a convergence event in DB (upsert on active token)."""
    try:
        execute(
            """INSERT INTO smart_money_convergence
               (token_address, token_symbol, wallet_count, weighted_score,
                convergence_level, wallets_json, first_buy_at, last_buy_at,
                window_hours)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (token_address) WHERE resolved_at IS NULL
               DO UPDATE SET
                 wallet_count = EXCLUDED.wallet_count,
                 weighted_score = EXCLUDED.weighted_score,
                 convergence_level = EXCLUDED.convergence_level,
                 wallets_json = EXCLUDED.wallets_json,
                 last_buy_at = EXCLUDED.last_buy_at,
                 created_at = NOW()""",
            (conv["token_address"], conv["token_symbol"], conv["wallet_count"],
             conv["weighted_score"], conv["convergence_level"],
             json.dumps(conv["wallets"]), conv["first_buy_at"],
             conv["last_buy_at"], CONVERGENCE_WINDOW_HOURS),
        )
        return True
    except Exception as e:
        log.error("Failed to store convergence for %s: %s",
                  conv["token_address"][:12], e)
        return False


def _send_convergence_alert(conv: dict):
    """Send convergence alert to H-Fire channel via severity system."""
    symbol_str = f"${conv['token_symbol']}" if conv["token_symbol"] else conv["token_address"][:12]
    sources_str = " + ".join(conv["sources"])

    wallet_lines = []
    for w in conv["wallets"][:5]:
        tier_emoji = {"A": "🟢", "B": "🟡", "C": "⚪"}.get(w["tier"], "⚪")
        amount_str = f" (${w['amount_usd']:,.0f})" if w["amount_usd"] else ""
        wallet_lines.append(f"  {tier_emoji} {w['display_name']}{amount_str} [{w['source']}]")
    if len(conv["wallets"]) > 5:
        wallet_lines.append(f"  ... +{len(conv['wallets']) - 5} more")

    alert = (
        f"{conv['level_emoji']} <b>SMART MONEY {conv['convergence_level']}</b>\n"
        f"\n"
        f"Token: {symbol_str}\n"
        f"📋 CA: <code>{conv['token_address']}</code>\n"
        f"Wallets: {conv['wallet_count']} independent buys in {CONVERGENCE_WINDOW_HOURS}h\n"
        f"Score: {conv['weighted_score']:.1f} ({sources_str})\n"
        f"\n"
        + "\n".join(wallet_lines)
    )

    # Only EMERGING and STRONG get Telegram alerts (H-Fire Tier 2)
    # WATCHING is DB-only — no Telegram noise
    if conv["convergence_level"] == "WATCHING":
        log.info("WATCHING convergence for %s (score=%.1f) — DB only, no alert",
                 symbol_str, conv["weighted_score"])
        return
    route_alert(2, alert)


def _expire_old_convergences():
    """Resolve convergences older than 24 hours."""
    try:
        execute(
            """UPDATE smart_money_convergence
               SET resolved_at = NOW()
               WHERE resolved_at IS NULL
                 AND created_at < NOW() - INTERVAL '24 hours'""",
        )
    except Exception as e:
        log.debug("Convergence expiry: %s", e)


def run_convergence_check() -> dict:
    """Main entry: detect and alert on smart money convergences.

    Returns:
        Summary dict with convergences found.
    """
    log.info("=== Smart Money Convergence Check ===")

    # Expire old convergences
    _expire_old_convergences()

    # Detect new convergences
    convergences = detect_convergences()

    if not convergences:
        log.debug("No smart money convergences detected")
        return {"convergences": [], "total": 0}

    # Store and alert
    new_alerts = 0
    for conv in convergences:
        stored = _store_convergence(conv)

        # Check if we already alerted for this token
        try:
            row = execute_one(
                """SELECT alert_sent FROM smart_money_convergence
                   WHERE token_address = %s AND resolved_at IS NULL""",
                (conv["token_address"],),
            )
            already_alerted = row and row[0]
        except Exception:
            already_alerted = False

        if stored and not already_alerted:
            _send_convergence_alert(conv)
            new_alerts += 1
            try:
                execute(
                    """UPDATE smart_money_convergence
                       SET alert_sent = TRUE
                       WHERE token_address = %s AND resolved_at IS NULL""",
                    (conv["token_address"],),
                )
            except Exception:
                pass

    log.info("Convergence check: %d convergences found, %d new alerts",
             len(convergences), new_alerts)

    return {
        "convergences": convergences,
        "total": len(convergences),
        "new_alerts": new_alerts,
    }


def get_active_convergences() -> list[dict]:
    """Get all active (unresolved) convergences from DB.

    Used by Huoyan pulse for Smart Money Radar section.
    """
    try:
        rows = execute(
            """SELECT token_address, token_symbol, wallet_count, weighted_score,
                      convergence_level, wallets_json, first_buy_at, last_buy_at,
                      created_at
               FROM smart_money_convergence
               WHERE resolved_at IS NULL
               ORDER BY weighted_score DESC
               LIMIT 10""",
            fetch=True,
        )
        if not rows:
            return []

        results = []
        for r in rows:
            wallets = r[5] if isinstance(r[5], list) else json.loads(r[5] or "[]")
            results.append({
                "token_address": r[0],
                "token_symbol": r[1],
                "wallet_count": r[2],
                "weighted_score": float(r[3] or 0),
                "convergence_level": r[4],
                "wallets": wallets,
                "first_buy_at": r[6],
                "last_buy_at": r[7],
                "created_at": r[8],
            })
        return results
    except Exception as e:
        log.error("Failed to fetch active convergences: %s", e)
        return []


def get_radar_summary() -> dict:
    """Get summary for Smart Money Radar section in Huoyan pulse."""
    convergences = get_active_convergences()
    if not convergences:
        return {"active": 0, "convergences": []}

    return {
        "active": len(convergences),
        "strong": sum(1 for c in convergences if c["convergence_level"] == "STRONG CONVERGENCE"),
        "emerging": sum(1 for c in convergences if c["convergence_level"] == "EMERGING"),
        "watching": sum(1 for c in convergences if c["convergence_level"] == "WATCHING"),
        "convergences": convergences,
    }


if __name__ == "__main__":
    result = run_convergence_check()
    print(f"\nSmart Money Convergence Check:")
    print(f"  Total convergences: {result['total']}")
    if result.get("convergences"):
        for c in result["convergences"]:
            print(f"  {c['level_emoji']} {c['token_symbol'] or c['token_address'][:12]}: "
                  f"score={c['weighted_score']:.1f} wallets={c['wallet_count']} "
                  f"level={c['convergence_level']}")
