"""Huoyan Jinjing (火眼金睛) — 4-hourly intelligence pulse.

Runs at 02:00, 06:00, 10:00, 14:00, 18:00, 22:00 UTC.
Max 50 lines per pulse. If nothing new -> "X quiet" (confirms monitoring active).

Section order: Macro → Chain Scorecard → Holdings → Positions →
Intelligence (YouTube + X) → Meme Radar → Scanner

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

    # 1. Macro Regime (enhanced)
    lines.extend(_macro_regime_section())

    # 2. Chain Scorecard
    lines.extend(_chain_scorecard_section())

    # 3. Holdings Health
    lines.extend(_holdings_health_section())

    # 4. Open positions health dashboard
    lines.extend(_positions_section())

    # 5. Intelligence: YouTube + X
    lines.extend(_youtube_section())
    lines.extend(_x_intelligence_section())

    # 6. Meme Radar (strong convergence only)
    lines.extend(_smart_money_radar_section())

    # Batched Tier 3 alerts
    batch = flush_huoyan_batch()
    if batch:
        lines.append("<b>📋 Alerts</b>")
        for item in batch[:5]:
            clean = item.replace("<b>", "").replace("</b>", "")
            lines.append(f"  • {clean[:80]}")
        if len(batch) > 5:
            lines.append(f"  ... +{len(batch) - 5} more")
        lines.append("")

    # 7. Scanner summary
    lines.extend(_scanner_section())

    # 06:00 special: watchlist + catalysts
    if hour == 6:
        lines.extend(_morning_watchlist())

    # 22:00 special: portfolio summary
    if hour == 22:
        lines.extend(_portfolio_summary())

    # Cap at 50 lines (increased for richer format)
    if len(lines) > 50:
        lines = lines[:49] + ["... (truncated)"]

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


def _macro_regime_section() -> list[str]:
    """Enhanced macro regime: BTC, dominance, SOL/BTC ratio, funding, stablecoins."""
    lines = ["<b>📊 Macro Regime</b>"]
    try:
        from chain_metrics.macro import get_macro_summary
        macro = get_macro_summary()
        if macro:
            # Regime signal
            signal = macro.get("regime_signal", "NEUTRAL")
            signal_map = {"RISK_ON": "🟢 RISK-ON", "RISK_OFF": "🔴 RISK-OFF", "NEUTRAL": "⚪ NEUTRAL"}
            lines.append(f"  {signal_map.get(signal, '⚪ NEUTRAL')}")

            # BTC price + dominance
            btc = macro.get("btc_price", 0)
            dom = macro.get("btc_dominance", 0)
            dom_trend = macro.get("dom_trend", "flat")
            trend_arrow = {"rising": "↑", "falling": "↓"}.get(dom_trend, "→")
            if btc:
                lines.append(f"  BTC: ${btc:,.0f} | Dom: {dom:.1f}% {trend_arrow}")

            # SOL/BTC ratio
            ratio = macro.get("sol_btc_ratio", 0)
            ratio_trend = macro.get("sol_btc_trend", "flat")
            ratio_arrow = {"up": "↑", "down": "↓"}.get(ratio_trend, "→")
            if ratio:
                lines.append(f"  SOL/BTC: {ratio:.6f} {ratio_arrow}")

            # Funding
            funding = macro.get("funding_avg")
            if funding is not None:
                label = "neutral"
                if funding > 0.03:
                    label = "greedy"
                elif funding < -0.03:
                    label = "fearful"
                lines.append(f"  Funding: {funding:.4f} ({label})")

            # Stablecoin total
            stable = macro.get("stablecoin_total", 0)
            if stable:
                lines.append(f"  Stablecoins: ${stable / 1e9:.1f}B")
        else:
            # Fallback to old regime data
            from regime.multiplier import get_current_regime
            regime = get_current_regime()
            if regime:
                mult = regime['regime_multiplier']
                lines.append(f"  Regime: {mult:.2f}")
                raw = regime.get('raw_data', {})
                if raw.get('btc_price'):
                    lines.append(f"  BTC: ${raw['btc_price']:,.0f}")
    except Exception:
        lines.append("  Macro data unavailable")

    lines.append("")
    return lines


def _chain_scorecard_section() -> list[str]:
    """Chain adoption scorecard: Solana vs ETH/Base/Sui/Arb."""
    lines = ["<b>🔗 Chain Scorecard</b>"]
    try:
        from chain_metrics.adoption import get_chain_scorecard
        sc = get_chain_scorecard()
        chains = sc.get("chains", {})
        sol = chains.get("Solana", {})
        if sol:
            tvl = sol.get("tvl", 0)
            tvl_pct = sol.get("tvl_7d_pct", 0)
            dex = sol.get("dex_volume", 0)
            dex_pct = sol.get("dex_volume_7d_pct", 0)
            stable = sol.get("stablecoin_mcap", 0)
            stable_pct = sol.get("stablecoin_mcap_7d_pct", 0)
            lines.append(
                f"  SOL: TVL ${tvl / 1e9:.1f}B ({tvl_pct:+.1f}%) | "
                f"DEX ${dex / 1e9:.1f}B ({dex_pct:+.1f}%)"
            )
            if stable:
                lines.append(f"  Stables: ${stable / 1e9:.1f}B ({stable_pct:+.1f}%)")
            # Compare vs ETH
            eth = chains.get("Ethereum", {})
            if eth:
                eth_tvl_pct = eth.get("tvl_7d_pct", 0)
                if tvl_pct > eth_tvl_pct + 2:
                    lines.append("  vs ETH: gaining share")
                elif tvl_pct < eth_tvl_pct - 2:
                    lines.append("  vs ETH: losing share")
                else:
                    lines.append("  vs ETH: steady")
            trend = sc.get("solana_trend", "unknown")
            trend_icon = {"accelerating": "🚀", "gaining": "📈", "steady": "➡️",
                          "losing": "📉", "decelerating": "⬇️"}.get(trend, "❓")
            lines.append(f"  Trend: {trend_icon} {trend}")
        else:
            lines.append("  No chain data yet — run chain-metrics")
    except Exception:
        lines.append("  Chain data unavailable")

    lines.append("")
    return lines


def _holdings_health_section() -> list[str]:
    """Holdings health: SOL/JUP/Pump.fun prices and 7d changes."""
    lines = ["<b>💰 Holdings Health</b>"]
    try:
        from chain_metrics.holdings import get_holdings_summary
        holdings = get_holdings_summary()
        if holdings:
            for token in ("SOL", "JUP", "PUMPFUN"):
                h = holdings.get(token)
                if not h:
                    continue
                price = h.get("price", 0)
                change = h.get("change_7d", 0)
                arrow = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                if token == "SOL":
                    ratio = h.get("sol_btc_ratio", 0)
                    ratio_str = f" | SOL/BTC: {ratio:.6f}" if ratio else ""
                    lines.append(f"  SOL: ${price:.2f} (7d: {change:+.1f}%){ratio_str} {arrow}")
                elif token == "JUP":
                    lines.append(f"  JUP: ${price:.4f} (7d: {change:+.1f}%) {arrow}")
                elif token == "PUMPFUN":
                    label = h.get("symbol", "PUMP")
                    lines.append(f"  {label}: ${price:.6f} (7d: {change:+.1f}%) {arrow}")
        else:
            lines.append("  No holdings data yet — run holdings")
    except Exception:
        lines.append("  Holdings data unavailable")

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
