"""Huoyan Jinjing (火眼金睛) — 4-hourly intelligence pulse.

Runs at 06:00, 10:00, 14:00, 18:00, 22:00, 02:00 UTC.
Max 30 lines per pulse. If nothing new -> "X quiet" (confirms monitoring active).

06:00 adds: Today's watchlist + catalysts
22:00 adds: Portfolio summary
"""

from datetime import date, datetime, timezone
from config import get_logger
from db.connection import execute, execute_one
from telegram_bot.severity import _send_to_channel, HUOYAN_CHAT_ID, flush_huoyan_batch

log = get_logger("telegram_bot.huoyan")


def generate_pulse(hour: int | None = None) -> str:
    """Generate and send the Huoyan pulse.

    Args:
        hour: Current UTC hour (auto-detected if None)
    """
    if hour is None:
        hour = datetime.now(timezone.utc).hour

    lines = [
        f"📡 <b>HUOYAN PULSE</b> — {datetime.now(timezone.utc).strftime('%H:%M')} UTC",
        "",
    ]

    # Market section
    lines.extend(_market_section())

    # Open positions health dashboard
    lines.extend(_positions_section())

    # KOL Activity
    lines.extend(_kol_activity_section())

    # YouTube Intel
    lines.extend(_youtube_section())

    # X Smart Money Intelligence
    lines.extend(_x_intelligence_section())

    # Smart Money Radar (convergence detection)
    lines.extend(_smart_money_radar_section())

    # Batched Tier 3 alerts
    batch = flush_huoyan_batch()
    if batch:
        lines.append("<b>📋 Alerts</b>")
        for item in batch[:5]:
            # Strip HTML for condensed view
            clean = item.replace("<b>", "").replace("</b>", "")
            lines.append(f"  • {clean[:80]}")
        if len(batch) > 5:
            lines.append(f"  ... +{len(batch) - 5} more")
        lines.append("")

    # Scanner summary
    lines.extend(_scanner_section())

    # 06:00 special: watchlist + catalysts
    if hour == 6:
        lines.extend(_morning_watchlist())

    # 22:00 special: portfolio summary
    if hour == 22:
        lines.extend(_portfolio_summary())

    # Cap at 30 lines
    if len(lines) > 30:
        lines = lines[:29] + ["... (truncated)"]

    # If nothing interesting, confirm we're alive
    if len(lines) <= 3:
        lines.append("😴 Quiet period — monitoring active")

    report = "\n".join(lines)

    # Send to Huoyan channel
    chat_id = HUOYAN_CHAT_ID
    if chat_id:
        _send_to_channel(chat_id, report)
        log.info("Huoyan pulse sent (%d lines)", len(lines))

    return report


def _market_section() -> list[str]:
    """BTC, SOL, regime state."""
    lines = ["<b>📊 Market</b>"]
    try:
        from regime.multiplier import get_current_regime
        regime = get_current_regime()
        if regime:
            mult = regime['regime_multiplier']
            state_map = {
                (0.8, 999): "🟢 RISK-ON",
                (0.5, 0.8): "⚪ NEUTRAL",
                (0.0, 0.5): "🔴 RISK-OFF",
            }
            state = "⚪ NEUTRAL"
            for (lo, hi), s in state_map.items():
                if lo <= mult < hi:
                    state = s
                    break
            lines.append(f"  Regime: {state} ({mult:.2f})")

        # BTC price from regime raw data
        raw = regime.get('raw_data', {}) if regime else {}
        btc_price = raw.get('btc_price')
        if btc_price:
            lines.append(f"  BTC: ${btc_price:,.0f}")
        fng = raw.get('fear_greed_value')
        if fng:
            lines.append(f"  F&G: {fng}")
    except Exception:
        lines.append("  Regime data unavailable")

    lines.append("")
    return lines


def _positions_section() -> list[str]:
    """Health dashboard for open positions (shadow or real)."""
    lines = ["<b>🏥 Positions</b>"]
    try:
        rows = execute(
            """SELECT st.token_symbol, st.current_pnl_pct, st.status,
                      st.entry_source, st.phases_entered,
                      hs.scaled_score, hs.confidence_pct, hs.recommended_action
               FROM shadow_trades st
               LEFT JOIN LATERAL (
                   SELECT scaled_score, confidence_pct, recommended_action
                   FROM health_scores
                   WHERE token_address = st.token_address
                   ORDER BY scored_at DESC LIMIT 1
               ) hs ON true
               WHERE st.status = 'open'
               ORDER BY st.entry_time DESC LIMIT 5""",
            fetch=True,
        )
        if rows:
            for sym, pnl, status, source, phases, health, conf, action in rows:
                pnl_str = f"{float(pnl or 0):+.1f}%" if pnl else "?"
                health_str = f"{float(health or 0):.0f}" if health else "?"
                conf_str = f"{float(conf or 0):.0f}%" if conf else "?"
                emoji = "🟢" if float(health or 0) >= 65 else "🟡" if float(health or 0) >= 50 else "🔴"
                lines.append(
                    f"  {emoji} ${sym or '?'}: {pnl_str} | H:{health_str} "
                    f"({conf_str}) | {action or '?'}"
                )
        else:
            lines.append("  No open positions")
    except Exception as e:
        log.debug("Positions section error: %s", e)
        lines.append("  Position data unavailable")

    lines.append("")
    return lines


def _kol_activity_section() -> list[str]:
    """Recent KOL wallet activity."""
    lines = ["<b>👤 KOL Activity</b>"]
    try:
        rows = execute(
            """SELECT kw.name, kt.token_symbol, kt.action, kt.amount_usd,
                      kt.is_conviction_buy
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               WHERE kt.detected_at > NOW() - INTERVAL '4 hours'
                 AND kt.amount_usd >= 500
               ORDER BY kt.amount_usd DESC LIMIT 5""",
            fetch=True,
        )
        if rows:
            for name, sym, action, usd, conviction in rows:
                icon = "🟢" if action == "buy" else "🔴"
                conv = " ⭐" if conviction else ""
                lines.append(
                    f"  {icon} {name}: {action} ${sym or '?'} "
                    f"(${float(usd or 0):,.0f}){conv}"
                )
        else:
            lines.append("  No KOL activity in 4h")
    except Exception as e:
        log.debug("KOL activity section: %s", e)
        lines.append("  KOL data unavailable")

    lines.append("")
    return lines


def _youtube_section() -> list[str]:
    """YouTube intel from last 4 hours."""
    lines = ["<b>📺 YouTube Intel</b>"]
    try:
        rows = execute(
            """SELECT channel_name, title, analysis_json, relevance_score
               FROM youtube_videos
               WHERE published_at > NOW() - INTERVAL '4 hours'
                 AND relevance_score > 5
               ORDER BY relevance_score DESC
               LIMIT 3""",
            fetch=True,
        )
        if rows:
            for channel, title, analysis, score in rows:
                aj = analysis if isinstance(analysis, dict) else {}
                outlook = aj.get("overall_outlook", "neutral")
                icon = {"bullish": "🟢", "bearish": "🔴"}.get(outlook, "🟡")
                tokens = aj.get("tokens_mentioned", [])
                tok_str = ", ".join(t.get("symbol", "") for t in tokens[:3]) if tokens else ""
                line = f"  {icon} {channel}: \"{(title or '')[:40]}\" — {outlook}"
                if tok_str:
                    line += f", mentioned {tok_str}"
                lines.append(line)
        else:
            lines.append("  ⚪ No relevant videos in 4h")
    except Exception as e:
        log.debug("YouTube section error: %s", e)
        lines.append("  YouTube data unavailable")

    lines.append("")
    return lines


def _x_intelligence_section() -> list[str]:
    """X smart money signals from last 4 hours."""
    lines = ["<b>📡 X Intelligence</b>"]
    try:
        from social.grok_poller import get_recent_x_signals
        signals = get_recent_x_signals(hours=4, min_strength="medium")
        if signals:
            for sig in signals[:5]:
                handle = sig.get("source_handle", "?")
                ptype = sig.get("parsed_type", "info")
                symbol = sig.get("token_symbol") or "?"
                strength = sig.get("signal_strength", "?")
                amount = sig.get("amount_usd")

                icon = "🔴" if strength == "strong" else "🟡"
                amount_str = f" (${amount:,.0f})" if amount else ""
                lines.append(f"  {icon} {handle}: {ptype} ${symbol}{amount_str} [{strength}]")
        else:
            lines.append("  ⚪ No smart money signals in 4h")
    except Exception as e:
        log.debug("X intelligence section error: %s", e)
        lines.append("  X data unavailable")

    lines.append("")
    return lines


def _smart_money_radar_section() -> list[str]:
    """Smart money convergence radar — cross-source wallet convergence."""
    lines = ["<b>🎯 Smart Money Radar</b>"]
    try:
        from wallets.convergence_detector import get_radar_summary
        radar = get_radar_summary()
        # Only show convergences at EMERGING (8+) or STRONG (12+)
        strong_signals = [
            c for c in radar.get("convergences", [])
            if c["convergence_level"] in ("EMERGING", "STRONG CONVERGENCE")
        ]
        if strong_signals:
            for conv in strong_signals[:3]:
                symbol = f"${conv['token_symbol']}" if conv.get("token_symbol") else conv["token_address"][:12]
                level = conv["convergence_level"]
                level_map = {
                    "STRONG CONVERGENCE": "🔴",
                    "EMERGING": "🟡",
                }
                icon = level_map.get(level, "🟡")
                lines.append(
                    f"  {icon} {symbol}: {conv['wallet_count']} wallets, "
                    f"score {conv['weighted_score']:.1f} [{level}]"
                )
        else:
            lines.append("  🔇 Quiet — no strong meme signals")
    except Exception as e:
        log.debug("Smart money radar section: %s", e)
        lines.append("  Radar data unavailable")

    lines.append("")
    return lines


def _scanner_section() -> list[str]:
    """Scanner summary."""
    lines = ["<b>🔍 Scanner</b>"]
    try:
        row = execute_one(
            """SELECT
                 COUNT(*) FILTER (WHERE type = 'gate_pass') as passes,
                 COUNT(*) FILTER (WHERE type LIKE 'gate_%') as total
               FROM alerts
               WHERE timestamp > NOW() - INTERVAL '4 hours'""",
        )
        if row:
            passes, total = row
            lines.append(f"  Last 4h: {total} scanned, {passes} passed")
        else:
            lines.append("  No scans in 4h")
    except Exception:
        lines.append("  Scanner data unavailable")

    lines.append("")
    return lines


def _morning_watchlist() -> list[str]:
    """06:00 special: today's watchlist."""
    lines = ["<b>📋 Today's Watchlist</b>"]
    try:
        rows = execute(
            """SELECT symbol, contract_address, last_health_score, token_tier
               FROM tokens
               WHERE health_state IS NOT NULL
                 AND last_health_score > 50
               ORDER BY last_health_score DESC LIMIT 5""",
            fetch=True,
        )
        if rows:
            for sym, addr, score, tier in rows:
                lines.append(f"  📍 ${sym}: Health {float(score or 0):.0f} ({tier or '?'})")
        else:
            lines.append("  No tokens on watchlist")
    except Exception:
        lines.append("  Watchlist unavailable")

    lines.append("")
    return lines


def _portfolio_summary() -> list[str]:
    """22:00 special: portfolio/shadow summary."""
    lines = ["<b>💼 Portfolio Summary</b>"]
    try:
        from shadow.tracker import get_shadow_summary
        summary = get_shadow_summary()
        lines.append(f"  Total trades: {summary.get('total', 0)}")
        lines.append(f"  Open: {summary.get('open', 0)} | Closed: {summary.get('closed', 0)}")
        lines.append(f"  Win rate: {summary.get('win_rate', 0):.0f}%")
        lines.append(f"  Total PnL: {summary.get('total_pnl', 0):+.1f}%")
    except Exception:
        lines.append("  Portfolio data unavailable")

    lines.append("")
    return lines
