"""Nightly Strategist Report — runs daily at 6:00 AM UTC.

Expanded report includes:
  - Regime status (multiplier + each component)
  - THREE SHORTLISTS: Momentum / Adoption / Infra ranked
  - Early Watch (watching tokens with velocity signals)
  - Market Structure (OI + funding + liquidations)
  - Unlocks with risk + buybacks
  - Social Pulse summary
  - Lifecycle Transitions
  - Exit triggers that fired today
  - Portfolio summary
  - System health: API status, scan success rate
"""

from datetime import date, datetime, timezone
from config import get_logger
from db.connection import execute, execute_one
from telegram_bot.alerts import _send, send_message

log = get_logger("reports.nightly")


def generate_nightly_report() -> str:
    """Generate and send the nightly strategist report. Returns the report text."""
    log.info("=== Generating nightly strategist report ===")

    lines = [
        "🌙 <b>NIGHTLY STRATEGIST REPORT</b>",
        f"Date: {date.today().isoformat()}",
        "",
    ]

    # 1. Regime status
    lines.extend(_regime_section())

    # 2. THREE SHORTLISTS
    lines.extend(_momentum_shortlist())
    lines.extend(_adoption_shortlist())
    lines.extend(_infra_shortlist())

    # 3. Early Watch
    lines.extend(_early_watch_section())

    # 4. Market Structure (OI + funding + liquidations)
    lines.extend(_market_structure_section())

    # 5. Unlocks
    lines.extend(_unlocks_section())

    # 6. Social Pulse
    lines.extend(_social_pulse_section())

    # 6b. YouTube Intelligence
    try:
        from social.youtube_free import youtube_report_section
        lines.extend(youtube_report_section())
    except Exception as e:
        log.debug("YouTube report section: %s", e)

    # 7. Lifecycle Transitions
    lines.extend(_lifecycle_section())

    # 8. Convergence alerts
    lines.extend(_convergence_section())

    # 9. Exit triggers
    lines.extend(_exit_triggers_section())

    # 10. Portfolio summary
    lines.extend(_portfolio_section())

    # 11. System health
    lines.extend(_system_health_section())

    report = "\n".join(lines)
    _send(report)

    log.info("Nightly report sent")
    return report


def _regime_section() -> list[str]:
    """Regime multiplier and components."""
    lines = ["<b>📊 Regime Status</b>"]

    try:
        row = execute_one(
            """SELECT regime_multiplier, btc_trend_score, stablecoin_supply_delta,
                      liquidity_proxy, risk_appetite
               FROM regime_snapshots WHERE date = CURRENT_DATE""",
        )
        if row:
            mult, btc, stable, liq, fng = row
            if mult >= 0.8:
                status = "🟢 Full Allocation"
            elif mult >= 0.6:
                status = "🟡 Half Allocation"
            elif mult >= 0.5:
                status = "🟠 Tier 1-2 Only"
            else:
                status = "🔴 Cash Mode"

            lines.append(f"  Multiplier: <b>{mult:.3f}</b> — {status}")
            lines.append(f"  BTC Trend: {btc:.2f} | Stablecoin: {stable:.2f}")
            lines.append(f"  Liquidity: {liq:.2f} | Fear&Greed: {fng:.2f}")
        else:
            lines.append("  ⚠️ No regime data for today")
    except Exception as e:
        log.error("Regime section error: %s", e)
        lines.append("  ⚠️ Regime data unavailable")

    lines.append("")
    return lines


def _momentum_shortlist() -> list[str]:
    """Top momentum tokens ranked by momentum score."""
    lines = ["<b>📈 Momentum Shortlist</b>"]
    try:
        rows = execute(
            """SELECT t.symbol, s.momentum_score, s.final_score, s.confidence_score
               FROM scores_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE s.date = CURRENT_DATE AND s.momentum_score IS NOT NULL
                 AND t.category = 'meme'
               ORDER BY s.momentum_score DESC NULLS LAST
               LIMIT 5""",
            fetch=True,
        )
        if rows:
            for i, (sym, mom, final, conf) in enumerate(rows, 1):
                lines.append(f"  {i}. <code>{sym}</code>: M={mom:.0f} Final={final:.0f} conf={conf:.0f}%")
        else:
            lines.append("  No momentum tokens scored today")
    except Exception as e:
        log.error("Momentum shortlist error: %s", e)
        lines.append("  ⚠️ Data unavailable")
    lines.append("")
    return lines


def _adoption_shortlist() -> list[str]:
    """Top adoption tokens ranked by adoption score."""
    lines = ["<b>👥 Adoption Shortlist</b>"]
    try:
        rows = execute(
            """SELECT t.symbol, s.adoption_score, s.momentum_score, s.final_score
               FROM scores_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE s.date = CURRENT_DATE AND s.adoption_score IS NOT NULL
               ORDER BY s.adoption_score DESC NULLS LAST
               LIMIT 5""",
            fetch=True,
        )
        if rows:
            for i, (sym, adopt, mom, final) in enumerate(rows, 1):
                parts = [f"A={adopt:.0f}"]
                if mom is not None:
                    parts.append(f"M={mom:.0f}")
                lines.append(f"  {i}. <code>{sym}</code>: {' '.join(parts)} Final={final:.0f}")
        else:
            lines.append("  No adoption tokens scored today")
    except Exception as e:
        log.error("Adoption shortlist error: %s", e)
        lines.append("  ⚠️ Data unavailable")
    lines.append("")
    return lines


def _infra_shortlist() -> list[str]:
    """Top infrastructure tokens ranked by infra score."""
    lines = ["<b>🏗 Infrastructure Shortlist</b>"]
    try:
        rows = execute(
            """SELECT t.symbol, s.infra_score, s.adoption_score, s.momentum_score, s.final_score
               FROM scores_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE s.date = CURRENT_DATE AND s.infra_score IS NOT NULL
               ORDER BY s.infra_score DESC NULLS LAST
               LIMIT 5""",
            fetch=True,
        )
        if rows:
            for i, (sym, infra, adopt, mom, final) in enumerate(rows, 1):
                parts = [f"I={infra:.0f}"]
                if adopt is not None:
                    parts.append(f"A={adopt:.0f}")
                if mom is not None:
                    parts.append(f"M={mom:.0f}")
                lines.append(f"  {i}. <code>{sym}</code>: {' '.join(parts)} Final={final:.0f}")
        else:
            lines.append("  No infrastructure tokens scored today")
    except Exception as e:
        log.error("Infra shortlist error: %s", e)
        lines.append("  ⚠️ Data unavailable")
    lines.append("")
    return lines


def _early_watch_section() -> list[str]:
    """Tokens in WATCHING state with velocity signals."""
    lines = ["<b>👀 Early Watch</b>"]
    try:
        rows = execute(
            """SELECT symbol, contract_address, quality_gate_status, updated_at
               FROM tokens WHERE quality_gate_status = 'watching'
               ORDER BY updated_at DESC LIMIT 10""",
            fetch=True,
        )
        if rows:
            for sym, mint, status, updated in rows:
                age = ""
                if updated:
                    hours = (datetime.now(timezone.utc) - updated.replace(tzinfo=timezone.utc)).total_seconds() / 3600
                    age = f" ({hours:.0f}h ago)"
                lines.append(f"  👀 <code>{sym}</code> — watching{age}")
        else:
            lines.append("  No tokens on watch")
    except Exception as e:
        log.debug("Early watch section: %s", e)
        lines.append("  No watch data available")
    lines.append("")
    return lines


def _market_structure_section() -> list[str]:
    """Market structure: OI + funding + liquidation summary."""
    lines = ["<b>📉 Market Structure</b>"]
    try:
        from market_intel.oi_analyzer import get_market_structure_summary
        summary = get_market_structure_summary("BTC")
        if summary:
            oi = summary.get("oi_regime", "unknown")
            funding = summary.get("funding_signal", "unknown")
            risk = summary.get("leverage_risk", 0)
            lines.append(f"  BTC OI Regime: <b>{oi}</b>")
            lines.append(f"  Funding Signal: {funding}")
            lines.append(f"  Leverage Risk: {risk:.0f}/100")

            # Liquidation zones
            liq_data = summary.get("liquidation_summary", {})
            if liq_data.get("nearest_above"):
                lines.append(f"  Nearest liq above: ${liq_data['nearest_above']:,.0f}")
            if liq_data.get("nearest_below"):
                lines.append(f"  Nearest liq below: ${liq_data['nearest_below']:,.0f}")
        else:
            lines.append("  Market structure data unavailable")
    except Exception as e:
        log.debug("Market structure section: %s", e)
        lines.append("  ⚠️ OI/funding data unavailable")
    lines.append("")
    return lines


def _unlocks_section() -> list[str]:
    """Token unlocks with risk assessment and buyback data."""
    lines = ["<b>🔓 Unlocks & Buybacks</b>"]
    try:
        from market_intel.unlocks import get_7day_cliff_warnings
        warnings = get_7day_cliff_warnings()
        if warnings:
            for w in warnings:
                risk_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
                    w.get("risk_level", ""), "⚪")
                lines.append(
                    f"  {risk_icon} <code>{w.get('symbol', '?')}</code>: "
                    f"cliff in {w.get('days_until', '?')}d "
                    f"({w.get('pct_of_supply', 0):.1f}% supply)"
                )
        else:
            lines.append("  ✅ No cliff unlocks in next 7 days")

        # Buyback summary
        try:
            rows = execute(
                """SELECT t.symbol FROM tokens t
                   WHERE t.category = 'infra' AND t.quality_gate_pass = TRUE
                   LIMIT 5""",
                fetch=True,
            )
            if rows:
                from market_intel.unlocks import get_buyback_burn_data
                for (sym,) in rows:
                    bb = get_buyback_burn_data(sym)
                    if bb and bb.get("buyback_30d_usd", 0) > 0:
                        lines.append(
                            f"  💰 <code>{sym}</code>: "
                            f"${bb['buyback_30d_usd']:,.0f} buyback/30d"
                        )
        except Exception:
            pass
    except Exception as e:
        log.debug("Unlocks section: %s", e)
        lines.append("  ⚠️ Unlock data unavailable")
    lines.append("")
    return lines


def _social_pulse_section() -> list[str]:
    """Social pulse summary across platforms."""
    lines = ["<b>📱 Social Pulse</b>"]
    try:
        rows = execute(
            """SELECT t.symbol, t.contract_address
               FROM tokens t
               WHERE t.quality_gate_pass = TRUE
               ORDER BY t.updated_at DESC LIMIT 5""",
            fetch=True,
        )
        if rows:
            from social.pulse import calculate_pulse
            for sym, mint in rows:
                try:
                    pulse = calculate_pulse(sym, mint=mint)
                    score = pulse.get("pulse_score", 0)
                    platforms = pulse.get("cross_platform_count", 0)
                    conviction = "🔥" if pulse.get("high_conviction") else ""
                    lines.append(
                        f"  <code>{sym}</code>: {score:.0f}/100 "
                        f"({platforms} platforms) {conviction}"
                    )
                except Exception:
                    continue
        if len(lines) == 1:
            lines.append("  No social data available")
    except Exception as e:
        log.debug("Social pulse section: %s", e)
        lines.append("  ⚠️ Social data unavailable")
    lines.append("")
    return lines


def _lifecycle_section() -> list[str]:
    """Lifecycle transitions and promotion candidates."""
    lines = ["<b>🔄 Lifecycle Transitions</b>"]
    try:
        from engines.lifecycle import check_promotion_candidates, get_lifecycle_summary
        summary = get_lifecycle_summary()
        stage_names = {1: "Birth", 2: "Viral", 3: "Community", 4: "Adoption", 5: "Infrastructure"}
        stage_counts = summary.get("stage_counts", {})
        if stage_counts:
            count_parts = [f"S{s}:{c}" for s, c in sorted(stage_counts.items())]
            lines.append(f"  Stages: {' | '.join(count_parts)}")

        candidates = check_promotion_candidates()
        if candidates:
            for c in candidates[:5]:
                lines.append(
                    f"  🎓 <code>{c.get('symbol', '?')}</code>: "
                    f"ready for Stage {c.get('new_stage', '?')} "
                    f"({c.get('reason', '')})"
                )
        else:
            lines.append("  No promotion candidates")

        recent = summary.get("recent_transitions", [])
        if recent:
            for t in recent[:3]:
                lines.append(
                    f"  ↗️ <code>{t.get('symbol', '?')}</code>: "
                    f"Stage {t.get('from_stage', '?')} → {t.get('to_stage', '?')}"
                )
    except Exception as e:
        log.debug("Lifecycle section: %s", e)
        lines.append("  ⚠️ Lifecycle data unavailable")
    lines.append("")
    return lines


def _convergence_section() -> list[str]:
    """New convergence alerts (multi-engine tokens)."""
    lines = ["<b>🔥 Convergence Alerts</b>"]

    try:
        rows = execute(
            """SELECT t.symbol, t.category,
                      s.momentum_score, s.adoption_score, s.infra_score
               FROM scores_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE s.date = CURRENT_DATE AND t.quality_gate_pass = TRUE""",
            fetch=True,
        )
        converging = []
        for sym, cat, mom, adopt, infra in (rows or []):
            high = []
            if mom and mom >= 70:
                high.append("Momentum")
            if adopt and adopt >= 70:
                high.append("Adoption")
            if infra and infra >= 70:
                high.append("Infrastructure")
            if len(high) >= 2:
                converging.append((sym, high))

        if converging:
            for sym, engines in converging:
                strength = "🔥🔥🔥" if len(engines) == 3 else "🔥🔥"
                lines.append(f"  {strength} <code>{sym}</code>: {' + '.join(engines)}")
        else:
            lines.append("  No convergence signals today")
    except Exception as e:
        log.error("Convergence section error: %s", e)
        lines.append("  ⚠️ Data unavailable")

    lines.append("")
    return lines


def _exit_triggers_section() -> list[str]:
    """Exit triggers that fired today."""
    lines = ["<b>⚠️ Exit Triggers</b>"]

    try:
        rows = execute(
            """SELECT t.symbol, a.feature_vector_json
               FROM alerts a
               JOIN tokens t ON t.id = a.token_id
               WHERE a.type = 'exit_trigger'
                 AND a.timestamp >= CURRENT_DATE
               ORDER BY a.severity DESC""",
            fetch=True,
        )
        if rows:
            for sym, fv in rows:
                trigger_name = fv.get("trigger", "unknown") if isinstance(fv, dict) else "unknown"
                lines.append(f"  🔴 <code>{sym}</code>: {trigger_name}")
        else:
            lines.append("  ✅ No exit triggers today")
    except Exception as e:
        log.error("Exit triggers section error: %s", e)
        lines.append("  ⚠️ Trigger data unavailable")

    lines.append("")
    return lines


def _portfolio_section() -> list[str]:
    """Portfolio summary from open positions."""
    lines = ["<b>💼 Portfolio</b>"]

    try:
        rows = execute(
            """SELECT p.tier, COUNT(*), SUM(p.size_pct)
               FROM positions p
               WHERE p.status = 'open'
               GROUP BY p.tier ORDER BY p.tier""",
            fetch=True,
        )
        if rows:
            tier_names = {1: "Foundation", 2: "Adopt/Infra", 3: "Momentum",
                          4: "Scanner", 5: "Cash"}
            total_alloc = 0
            for tier, count, total_size in rows:
                name = tier_names.get(tier, f"Tier {tier}")
                lines.append(f"  T{tier} {name}: {count} pos, {total_size:.1f}%")
                total_alloc += total_size
            lines.append(f"  Total allocated: {total_alloc:.1f}%")
            lines.append(f"  Cash: {100 - total_alloc:.1f}%")
        else:
            lines.append("  No open positions tracked")
    except Exception as e:
        log.error("Portfolio section error: %s", e)
        lines.append("  ⚠️ Portfolio data unavailable")

    lines.append("")
    return lines


def _system_health_section() -> list[str]:
    """System health: API status, scan success rate."""
    lines = ["<b>🔧 System Health</b>"]

    try:
        # DB status
        from db.connection import is_healthy
        db_ok = is_healthy()
        lines.append(f"  Database: {'🟢 Connected' if db_ok else '🔴 Disconnected'}")

        # Scan stats today
        row = execute_one(
            """SELECT
                 COUNT(*) FILTER (WHERE type = 'gate_pass') as passes,
                 COUNT(*) FILTER (WHERE type = 'gate_fail') as fails,
                 COUNT(*) as total
               FROM alerts
               WHERE timestamp >= CURRENT_DATE""",
        )
        if row:
            passes, fails, total = row
            rate = (passes / total * 100) if total > 0 else 0
            lines.append(f"  Scans today: {total} ({passes} pass, {fails} fail, {rate:.0f}% rate)")

        # Token count
        row = execute_one(
            "SELECT COUNT(*) FROM tokens WHERE quality_gate_pass = TRUE",
        )
        if row:
            lines.append(f"  Tracked tokens: {row[0]}")

        # Watching count
        try:
            row = execute_one(
                "SELECT COUNT(*) FROM tokens WHERE quality_gate_status = 'watching'",
            )
            if row and row[0]:
                lines.append(f"  Watching: {row[0]}")
        except Exception:
            pass

        # Check degraded mode
        try:
            from monitoring.degraded import is_degraded
            if is_degraded():
                lines.append("  ⚠️ <b>DEGRADED MODE ACTIVE</b>")
            else:
                lines.append("  Mode: 🟢 Normal")
        except Exception:
            lines.append("  Mode: 🟢 Normal")

    except Exception as e:
        log.error("System health section error: %s", e)
        lines.append("  ⚠️ Health data unavailable")

    return lines
