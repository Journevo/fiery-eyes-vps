"""Weekly Portfolio Review — runs Sunday 08:00 UTC.

Comprehensive weekly digest sent to Huoyan channel:
  - Holdings 7d performance (SOL/JUP/Pump.fun)
  - SOL/BTC ratio trend
  - Chain scorecard (Solana vs competitors)
  - Macro regime shifts this week
  - YouTube consensus (top themes)
  - Meme radar summary (convergences)
  - Action recommendation
"""

from datetime import date, datetime, timezone
from config import get_logger
from db.connection import execute, execute_one
from telegram_bot.severity import HUOYAN_CHAT_ID, _send_to_channel

log = get_logger("reports.weekly_review")


def generate_weekly_review() -> str:
    """Generate and send the weekly portfolio review."""
    log.info("=== Generating weekly portfolio review ===")

    lines = [
        "📋 <b>WEEKLY PORTFOLIO REVIEW</b>",
        f"Week ending: {date.today().isoformat()}",
        "",
    ]

    lines.extend(_holdings_performance())
    lines.extend(_sol_btc_trend())
    lines.extend(_chain_scorecard())
    lines.extend(_macro_shifts())
    lines.extend(_youtube_consensus())
    lines.extend(_meme_radar_summary())
    lines.extend(_action_recommendation())

    report = "\n".join(lines)
    _send_to_channel(HUOYAN_CHAT_ID, report)

    log.info("Weekly portfolio review sent")
    return report


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _holdings_performance() -> list[str]:
    """Holdings 7d price changes for SOL/JUP/PUMPFUN."""
    lines = ["<b>💰 Holdings Performance (7d)</b>"]
    try:
        from chain_metrics.holdings import get_holdings_summary
        holdings = get_holdings_summary()
        if not holdings:
            lines.append("  No holdings data available")
            lines.append("")
            return lines

        for token in ("SOL", "JUP", "PUMPFUN"):
            h = holdings.get(token)
            if not h:
                continue
            price = h.get("price", 0)
            change = h.get("change_7d", 0)
            arrow = "+" if change >= 0 else ""

            extras = []
            if h.get("sol_btc_ratio"):
                extras.append(f"SOL/BTC: {h['sol_btc_ratio']:.6f}")
            if h.get("mcap"):
                mcap = h["mcap"]
                if mcap >= 1e9:
                    extras.append(f"MCap: ${mcap / 1e9:.1f}B")
                else:
                    extras.append(f"MCap: ${mcap / 1e6:.0f}M")
            if h.get("volume_24h"):
                extras.append(f"Vol: ${h['volume_24h']:,.0f}")

            extra_str = f"\n    {' | '.join(extras)}" if extras else ""
            emoji = "🟢" if change > 2 else "🔴" if change < -2 else "⚪"
            lines.append(f"  {emoji} {token}: ${price:.4f} ({arrow}{change:.1f}%){extra_str}")
    except Exception as e:
        log.error("Holdings performance section: %s", e)
        lines.append("  Holdings data unavailable")
    lines.append("")
    return lines


def _sol_btc_trend() -> list[str]:
    """SOL/BTC ratio trend over the past week from macro_regime_v2."""
    lines = ["<b>📊 SOL/BTC Ratio Trend</b>"]
    try:
        # Current ratio
        current = execute_one(
            "SELECT sol_btc_ratio, btc_dominance FROM macro_regime_v2 ORDER BY timestamp DESC LIMIT 1",
        )
        # 7d ago
        prev = execute_one(
            """SELECT sol_btc_ratio, btc_dominance FROM macro_regime_v2
               WHERE timestamp < NOW() - INTERVAL '6 days'
               ORDER BY timestamp DESC LIMIT 1""",
        )

        if current and current[0]:
            ratio_now = float(current[0])
            dom_now = float(current[1] or 0)
            lines.append(f"  Current: {ratio_now:.6f} | BTC Dom: {dom_now:.1f}%")

            if prev and prev[0]:
                ratio_7d = float(prev[0])
                dom_7d = float(prev[1] or 0)
                ratio_change = (ratio_now - ratio_7d) / ratio_7d * 100
                dom_change = dom_now - dom_7d

                if ratio_change > 2:
                    trend = "SOL outperforming BTC"
                    emoji = "🟢"
                elif ratio_change < -2:
                    trend = "SOL underperforming BTC"
                    emoji = "🔴"
                else:
                    trend = "SOL tracking BTC"
                    emoji = "⚪"

                lines.append(f"  7d change: {ratio_change:+.1f}% — {emoji} {trend}")
                lines.append(f"  BTC Dom shift: {dom_change:+.1f}pp")
            else:
                lines.append("  7d comparison not available yet")
        else:
            lines.append("  No macro data — run macro-snapshot first")
    except Exception as e:
        log.error("SOL/BTC trend section: %s", e)
        lines.append("  SOL/BTC data unavailable")
    lines.append("")
    return lines


def _chain_scorecard() -> list[str]:
    """Chain adoption scorecard from DeFiLlama."""
    lines = ["<b>🔗 Chain Scorecard</b>"]
    try:
        from chain_metrics.adoption import get_chain_scorecard
        sc = get_chain_scorecard()
        chains = sc.get("chains", {})
        if not chains:
            lines.append("  No chain data — run chain-metrics first")
            lines.append("")
            return lines

        for chain in ("Solana", "Ethereum", "Base", "Sui", "Arbitrum"):
            c = chains.get(chain, {})
            if not c:
                continue
            tvl = c.get("tvl", 0)
            tvl_pct = c.get("tvl_7d_pct", 0)
            dex = c.get("dex_volume", 0)
            dex_pct = c.get("dex_volume_7d_pct", 0)
            tvl_share = c.get("tvl_share", 0)
            dex_share = c.get("dex_share", 0)

            lines.append(
                f"  {chain}: TVL ${tvl / 1e9:.1f}B ({tvl_pct:+.1f}%) "
                f"| DEX ${dex / 1e9:.1f}B ({dex_pct:+.1f}%) "
                f"| Share: {tvl_share:.0f}%/{dex_share:.0f}%"
            )

        trend = sc.get("solana_trend", "unknown")
        trend_emoji = {
            "accelerating": "🚀", "gaining": "🟢",
            "steady": "⚪", "losing": "🟡", "decelerating": "🔴",
        }.get(trend, "❓")
        lines.append(f"  Solana trend: {trend_emoji} <b>{trend}</b>")
    except Exception as e:
        log.error("Chain scorecard section: %s", e)
        lines.append("  Chain data unavailable")
    lines.append("")
    return lines


def _macro_shifts() -> list[str]:
    """Macro regime shifts this week."""
    lines = ["<b>📉 Macro Regime This Week</b>"]
    try:
        rows = execute(
            """SELECT regime_signal, btc_price, btc_dominance, sol_btc_ratio,
                      timestamp
               FROM macro_regime_v2
               WHERE timestamp > NOW() - INTERVAL '7 days'
               ORDER BY timestamp ASC""",
            fetch=True,
        )
        if not rows:
            lines.append("  No macro data this week")
            lines.append("")
            return lines

        # Detect regime changes
        prev_regime = None
        shifts = []
        for regime, btc, dom, ratio, ts in rows:
            if prev_regime and regime != prev_regime:
                shifts.append((prev_regime, regime, ts))
            prev_regime = regime

        if shifts:
            for from_r, to_r, ts in shifts:
                ts_str = ts.strftime("%a %H:%M") if ts else "?"
                emoji = {"RISK_ON": "🟢", "NEUTRAL": "🟡", "RISK_OFF": "🔴"}.get(to_r, "⚪")
                lines.append(f"  {emoji} {from_r} → {to_r} ({ts_str} UTC)")
        else:
            lines.append(f"  Stable: {prev_regime} all week")

        # Current state
        last = rows[-1]
        lines.append(
            f"  Now: {last[0]} | BTC ${float(last[1] or 0):,.0f} | "
            f"Dom {float(last[2] or 0):.1f}%"
        )

        # Funding
        from chain_metrics.macro import get_macro_summary
        summary = get_macro_summary()
        if summary.get("funding_avg") is not None:
            funding = summary["funding_avg"]
            if abs(funding) > 0.05:
                f_label = "Overleveraged"
            elif abs(funding) < 0.01:
                f_label = "Neutral"
            else:
                f_label = "Moderate"
            lines.append(f"  Funding: {funding:.4f} ({f_label})")
    except Exception as e:
        log.error("Macro shifts section: %s", e)
        lines.append("  Macro data unavailable")
    lines.append("")
    return lines


def _youtube_consensus() -> list[str]:
    """Top YouTube themes this week from summaries."""
    lines = ["<b>📺 YouTube Consensus</b>"]
    try:
        rows = execute(
            """SELECT title, channel_name, summary
               FROM youtube_summaries
               WHERE created_at > NOW() - INTERVAL '7 days'
                 AND summary IS NOT NULL AND summary != ''
               ORDER BY created_at DESC
               LIMIT 10""",
            fetch=True,
        )
        if not rows:
            lines.append("  No YouTube summaries this week")
            lines.append("")
            return lines

        lines.append(f"  {len(rows)} videos summarized this week:")
        for title, channel, summary in rows[:5]:
            # Truncate summary to first sentence
            first_line = (summary or "").split("\n")[0][:120]
            lines.append(f"  - <b>{channel or '?'}</b>: {first_line}")

    except Exception as e:
        log.debug("YouTube consensus section: %s", e)
        lines.append("  YouTube data unavailable")
    lines.append("")
    return lines


def _meme_radar_summary() -> list[str]:
    """Meme convergence activity this week."""
    lines = ["<b>🔥 Meme Radar (Weekly)</b>"]
    try:
        rows = execute(
            """SELECT token_address, weighted_score, wallet_count, sources,
                      detected_at
               FROM smart_money_convergence
               WHERE detected_at > NOW() - INTERVAL '7 days'
               ORDER BY weighted_score DESC
               LIMIT 5""",
            fetch=True,
        )
        if not rows:
            lines.append("  Quiet week — no convergence signals")
            lines.append("")
            return lines

        for addr, score, wallets, sources, ts in rows:
            # Try to resolve symbol
            sym = addr[:12] + "..."
            try:
                row = execute_one(
                    "SELECT symbol FROM tokens WHERE contract_address = %s",
                    (addr,),
                )
                if row and row[0]:
                    sym = f"${row[0]}"
            except Exception:
                pass
            ts_str = ts.strftime("%a") if ts else "?"
            level = "STRONG" if score >= 12 else "EMERGING" if score >= 8 else "WATCHING"
            lines.append(
                f"  {sym}: score {score:.1f} | {wallets} wallets | "
                f"{level} ({ts_str})"
            )
    except Exception as e:
        log.debug("Meme radar section: %s", e)
        lines.append("  Convergence data unavailable")
    lines.append("")
    return lines


def _action_recommendation() -> list[str]:
    """Synthesized action recommendation based on all signals."""
    lines = ["<b>🎯 Action Recommendation</b>"]
    try:
        # Gather signals
        signals = {"bullish": 0, "bearish": 0}

        # 1. Regime signal
        from chain_metrics.macro import get_macro_summary
        macro = get_macro_summary()
        regime = macro.get("regime_signal", "NEUTRAL")
        if regime == "RISK_ON":
            signals["bullish"] += 2
        elif regime == "RISK_OFF":
            signals["bearish"] += 2

        # 2. SOL/BTC trend
        sol_btc_trend = macro.get("sol_btc_trend", "flat")
        if sol_btc_trend == "up":
            signals["bullish"] += 1
        elif sol_btc_trend == "down":
            signals["bearish"] += 1

        # 3. BTC dominance trend
        dom_trend = macro.get("dom_trend", "flat")
        if dom_trend == "falling":
            signals["bullish"] += 1  # alt season
        elif dom_trend == "rising":
            signals["bearish"] += 1

        # 4. Solana chain trend
        try:
            from chain_metrics.adoption import get_solana_trend
            sol_trend = get_solana_trend()
            if sol_trend in ("accelerating", "gaining"):
                signals["bullish"] += 1
            elif sol_trend in ("losing", "decelerating"):
                signals["bearish"] += 1
        except Exception:
            pass

        # 5. Holdings performance
        try:
            from chain_metrics.holdings import get_holdings_summary
            holdings = get_holdings_summary()
            sol = holdings.get("SOL", {})
            sol_7d = sol.get("change_7d", 0)
            if sol_7d > 5:
                signals["bullish"] += 1
            elif sol_7d < -5:
                signals["bearish"] += 1
        except Exception:
            pass

        # Generate recommendation
        bull = signals["bullish"]
        bear = signals["bearish"]
        net = bull - bear

        if net >= 3:
            recommendation = (
                "🟢 <b>ACCUMULATE</b> — Strong bullish alignment. "
                "Maintain or increase SOL allocation. "
                "Look for JUP/infra dips to add."
            )
        elif net >= 1:
            recommendation = (
                "🟢 <b>HOLD</b> — Moderately bullish. "
                "Maintain current positions. "
                "No urgency to change allocation."
            )
        elif net >= -1:
            recommendation = (
                "🟡 <b>NEUTRAL</b> — Mixed signals. "
                "Hold current positions but avoid new entries. "
                "Monitor SOL/BTC ratio closely."
            )
        elif net >= -3:
            recommendation = (
                "🟠 <b>CAUTIOUS</b> — Bearish lean. "
                "Consider trimming smaller positions. "
                "Keep core SOL but tighten stops."
            )
        else:
            recommendation = (
                "🔴 <b>DE-RISK</b> — Strong bearish alignment. "
                "Reduce exposure, move to stables. "
                "Wait for regime shift before re-entering."
            )

        lines.append(f"  Signals: {bull} bullish / {bear} bearish (net: {net:+d})")
        lines.append(f"  {recommendation}")

    except Exception as e:
        log.error("Action recommendation section: %s", e)
        lines.append("  Recommendation unavailable")
    lines.append("")
    return lines
