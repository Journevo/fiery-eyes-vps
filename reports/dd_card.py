"""Due Diligence Card Generator — complete DD card for any token via Telegram.

Sections:
  1. Identity (symbol, chain, category, age)
  2. Quality Gate results
  3. Tokenomics (supply, distribution, unlocks)
  4. Adoption metrics (if applicable)
  5. Market structure (liquidity, volume, mcap)
  6. Engine scores (all applicable)
  7. Regime-adjusted final score
  8. Red flags
  9. Codified exit triggers
  10. Recommended position size and tier
"""

import json
from datetime import date
from config import get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json
from telegram_bot.alerts import _send
from risk.portfolio import recommend_position

log = get_logger("reports.dd_card")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


def generate_dd_card(mint: str) -> str | None:
    """Generate a complete Due Diligence card for a token and send to Telegram.

    Returns the DD card text, or None on failure.
    """
    log.info("Generating DD card for %s", mint)

    # Fetch token from DB
    token_row = execute_one(
        """SELECT id, symbol, name, category, quality_gate_pass, safety_score, launch_date
           FROM tokens WHERE contract_address = %s""",
        (mint,),
    )
    if not token_row:
        msg = f"⚠️ Token <code>{mint}</code> not found in database. Run /scan first."
        _send(msg)
        return None

    token_id, symbol, name, category, gate_pass, safety_score, launch_date = token_row
    cat = category or "meme"

    # Fetch latest snapshot
    snapshot = execute_one(
        """SELECT price, mcap, volume, liquidity_depth_10k,
                  holders_raw, holders_quality_adjusted,
                  retention_7d, retention_30d,
                  top10_pct, top50_pct, gini,
                  median_wallet_balance, social_velocity,
                  fresh_wallet_pct, sybil_risk_score
           FROM snapshots_daily
           WHERE token_id = %s
           ORDER BY date DESC LIMIT 1""",
        (token_id,),
    )

    # Fetch latest scores
    scores_row = execute_one(
        """SELECT momentum_score, adoption_score, infra_score,
                  composite_score, confidence_score, regime_multiplier, final_score
           FROM scores_daily
           WHERE token_id = %s
           ORDER BY date DESC LIMIT 1""",
        (token_id,),
    )

    # Fetch gate results
    gate_row = execute_one(
        """SELECT feature_vector_json
           FROM alerts
           WHERE token_id = %s AND type IN ('gate_pass', 'gate_fail')
           ORDER BY timestamp DESC LIMIT 1""",
        (token_id,),
    )

    # Fetch current DexScreener data
    dex = _fetch_dex_data(mint)

    # Fetch regime
    regime_row = execute_one(
        "SELECT regime_multiplier FROM regime_snapshots WHERE date = CURRENT_DATE",
    )
    regime_mult = regime_row[0] if regime_row else 1.0

    # Build the card
    lines = [
        "📋 <b>DUE DILIGENCE CARD</b>",
        "",
    ]

    # 1. Identity
    lines.extend(_identity_section(symbol, name, mint, cat, launch_date, dex))

    # 2. Quality Gate
    lines.extend(_gate_section(gate_pass, safety_score, gate_row))

    # 3. Tokenomics
    lines.extend(_tokenomics_section(snapshot, dex))

    # 4. Market Structure
    lines.extend(_market_section(snapshot, dex))

    # 5. Engine Scores
    lines.extend(_engines_section(scores_row))

    # 6. Regime-Adjusted Score
    lines.extend(_regime_section(scores_row, regime_mult))

    # 7. Market Structure (OI + funding)
    lines.extend(_market_structure_dd_section(symbol))

    # 8. Unlocks & Buybacks
    lines.extend(_unlocks_dd_section(symbol))

    # 9. Lifecycle Stage
    lines.extend(_lifecycle_dd_section(token_id))

    # 10. Red Flags
    lines.extend(_red_flags_section(snapshot, gate_row))

    # 11. Exit Triggers
    lines.extend(_exit_triggers_section(token_id, mint, cat, regime_mult))

    # 12. Position Recommendation
    lines.extend(_position_section(scores_row, cat, snapshot, regime_mult))

    card = "\n".join(lines)
    _send(card)
    log.info("DD card sent for %s", symbol)
    return card


def _fetch_dex_data(mint: str) -> dict:
    """Get current DexScreener data."""
    try:
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            return {}
        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        return {
            "price": best.get("priceUsd"),
            "mcap": best.get("marketCap") or best.get("fdv"),
            "volume_24h": best.get("volume", {}).get("h24"),
            "liquidity": best.get("liquidity", {}).get("usd"),
            "pair_age_hours": None,  # calculated from pairCreatedAt
            "dex": best.get("dexId"),
            "buys_24h": best.get("txns", {}).get("h24", {}).get("buys", 0),
            "sells_24h": best.get("txns", {}).get("h24", {}).get("sells", 0),
            "price_change_24h": best.get("priceChange", {}).get("h24"),
            "socials": best.get("info", {}).get("socials", []) if best.get("info") else [],
        }
    except Exception:
        return {}


def _identity_section(symbol, name, mint, category, launch_date, dex) -> list[str]:
    lines = ["<b>1. Identity</b>"]
    lines.append(f"  Symbol: <code>{symbol}</code>")
    if name:
        lines.append(f"  Name: {name}")
    lines.append(f"  Mint: <code>{mint}</code>")
    lines.append(f"  Category: {category}")
    lines.append(f"  Chain: Solana")
    if launch_date:
        lines.append(f"  Launch: {launch_date}")
    if dex.get("dex"):
        lines.append(f"  DEX: {dex['dex']}")
    socials = dex.get("socials", [])
    if socials:
        social_types = [s.get("type", "?") for s in socials]
        lines.append(f"  Socials: {', '.join(social_types)}")
    lines.append("")
    return lines


def _gate_section(gate_pass, safety_score, gate_row) -> list[str]:
    lines = ["<b>2. Quality Gate</b>"]
    icon = "✅ PASS" if gate_pass else "❌ FAIL"
    lines.append(f"  Result: {icon}")
    lines.append(f"  Safety Score: {safety_score:.0f}/100" if safety_score else "  Safety Score: N/A")

    if gate_row and gate_row[0]:
        checks = gate_row[0] if isinstance(gate_row[0], dict) else {}
        for check_name, check_data in checks.items():
            if isinstance(check_data, dict):
                passed = "✅" if check_data.get("pass") else "❌"
                lines.append(f"  {passed} {check_name}")
    lines.append("")
    return lines


def _tokenomics_section(snapshot, dex) -> list[str]:
    lines = ["<b>3. Tokenomics & Distribution</b>"]
    if snapshot:
        _, _, _, _, holders_raw, holders_qa, _, _, top10, top50, gini, median, *_ = snapshot
        if holders_raw:
            lines.append(f"  Holders (raw): {holders_raw}")
        if holders_qa:
            lines.append(f"  Holders (QA): {holders_qa}")
        if top10:
            lines.append(f"  Top 10 concentration: {top10:.1f}%")
        if top50:
            lines.append(f"  Top 50 concentration: {top50:.1f}%")
        if gini:
            lines.append(f"  Gini coefficient: {gini:.3f}")
        if median:
            lines.append(f"  Median wallet: {median:,.2f}")
    else:
        lines.append("  ⚠️ No snapshot data — run snapshots first")
    lines.append("")
    return lines


def _market_section(snapshot, dex) -> list[str]:
    lines = ["<b>4. Market Structure</b>"]
    price = dex.get("price") or (snapshot[0] if snapshot else None)
    mcap = dex.get("mcap") or (snapshot[1] if snapshot else None)
    vol = dex.get("volume_24h") or (snapshot[2] if snapshot else None)
    liq = dex.get("liquidity") or (snapshot[3] if snapshot else None)

    if price:
        lines.append(f"  Price: ${float(price):,.8f}")
    if mcap:
        lines.append(f"  Market Cap: ${float(mcap):,.0f}")
    if vol:
        lines.append(f"  24h Volume: ${float(vol):,.0f}")
    if liq:
        lines.append(f"  Liquidity: ${float(liq):,.0f}")
    if mcap and liq and float(mcap) > 0:
        lines.append(f"  Liq/Mcap: {float(liq)/float(mcap)*100:.1f}%")

    buys = dex.get("buys_24h", 0)
    sells = dex.get("sells_24h", 0)
    if buys or sells:
        lines.append(f"  24h Txns: {buys} buys / {sells} sells")

    change = dex.get("price_change_24h")
    if change:
        lines.append(f"  24h Change: {float(change):+.1f}%")

    lines.append("")
    return lines


def _engines_section(scores_row) -> list[str]:
    lines = ["<b>5. Engine Scores</b>"]
    if scores_row:
        mom, adopt, infra, comp, conf, regime, final = scores_row
        if mom is not None:
            lines.append(f"  📈 Momentum: {mom:.0f}/100")
        if adopt is not None:
            lines.append(f"  👥 Adoption: {adopt:.0f}/100")
        if infra is not None:
            lines.append(f"  🏗 Infrastructure: {infra:.0f}/100")
        if comp is not None:
            lines.append(f"  Composite: <b>{comp:.0f}</b>/100")
        if conf is not None:
            lines.append(f"  Confidence: {conf:.0f}%")
    else:
        lines.append("  ⚠️ No scores — run scoring first")
    lines.append("")
    return lines


def _regime_section(scores_row, regime_mult) -> list[str]:
    lines = ["<b>6. Regime-Adjusted Score</b>"]
    lines.append(f"  Regime Multiplier: {regime_mult:.3f}")
    if scores_row:
        comp = scores_row[3]
        if comp is not None:
            final = comp * regime_mult
            lines.append(f"  Final Score: <b>{final:.0f}</b>/100 ({comp:.0f} × {regime_mult:.2f})")
    lines.append("")
    return lines


def _market_structure_dd_section(symbol) -> list[str]:
    """Market structure: OI, funding, liquidations for DD card."""
    lines = ["<b>7. Market Structure</b>"]
    try:
        from market_intel.oi_analyzer import get_market_structure_summary
        summary = get_market_structure_summary(symbol or "BTC")
        if summary:
            lines.append(f"  OI Regime: {summary.get('oi_regime', 'N/A')}")
            lines.append(f"  Funding: {summary.get('funding_signal', 'N/A')}")
            lines.append(f"  Leverage Risk: {summary.get('leverage_risk', 0):.0f}/100")
            interpretation = summary.get("interpretation", "")
            if interpretation:
                lines.append(f"  <i>{interpretation}</i>")
        else:
            lines.append("  ⚠️ No market structure data")
    except Exception:
        lines.append("  ⚠️ Market structure data unavailable")
    lines.append("")
    return lines


def _unlocks_dd_section(symbol) -> list[str]:
    """Unlock schedule and buyback data for DD card."""
    lines = ["<b>8. Unlocks & Buybacks</b>"]
    try:
        from market_intel.unlocks import get_upcoming_unlocks, get_buyback_burn_data, calculate_unlock_risk
        unlocks = get_upcoming_unlocks(symbol or "")
        if unlocks:
            for u in unlocks[:3]:
                utype = u.get("type", "linear")
                pct = u.get("pct_of_supply", 0)
                lines.append(f"  📅 {u.get('date', '?')}: {pct:.1f}% ({utype})")
        else:
            lines.append("  No upcoming unlocks found")

        bb = get_buyback_burn_data(symbol or "")
        if bb and (bb.get("buyback_30d_usd", 0) > 0 or bb.get("burn_30d_tokens", 0) > 0):
            lines.append(f"  💰 Buyback 30d: ${bb.get('buyback_30d_usd', 0):,.0f}")
            lines.append(f"  🔥 Burn 30d: {bb.get('burn_30d_tokens', 0):,.0f} tokens")
            lines.append(f"  Net dilution: {bb.get('net_emission', 0):,.0f}")

        risk = calculate_unlock_risk(symbol or "", 0)
        if risk:
            risk_icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(
                risk.get("risk_level", ""), "⚪")
            lines.append(f"  Unlock Risk: {risk_icon} {risk.get('risk_level', 'N/A')} "
                        f"(ratio: {risk.get('unlock_to_volume_ratio', 0):.1f}x)")
    except Exception:
        lines.append("  ⚠️ Unlock data unavailable")
    lines.append("")
    return lines


def _lifecycle_dd_section(token_id) -> list[str]:
    """Lifecycle stage information for DD card."""
    lines = ["<b>9. Lifecycle Stage</b>"]
    try:
        from engines.lifecycle import detect_stage
        from db.connection import execute_one as _eo
        row = _eo("SELECT contract_address FROM tokens WHERE id = %s", (token_id,))
        if row:
            stage = detect_stage(token_id, row[0])
            stage_names = {1: "Birth", 2: "Viral", 3: "Community", 4: "Adoption", 5: "Infrastructure"}
            lines.append(f"  Stage: <b>{stage.get('stage', '?')} — {stage.get('stage_name', '?')}</b>")
            met = stage.get("criteria_met", [])
            if met:
                lines.append(f"  Criteria met: {', '.join(met[:5])}")
            missing = stage.get("criteria_missing", [])
            if missing:
                lines.append(f"  Still needed: {', '.join(missing[:5])}")
            if stage.get("promotion_ready"):
                lines.append("  🎓 <b>PROMOTION CANDIDATE</b>")
    except Exception:
        lines.append("  ⚠️ Lifecycle data unavailable")
    lines.append("")
    return lines


def _red_flags_section(snapshot, gate_row) -> list[str]:
    lines = ["<b>10. Red Flags</b>"]
    flags = []

    if snapshot:
        _, _, _, _, _, _, _, _, top10, _, gini, _, social_vel, fresh_pct, sybil, *_ = snapshot[-3:]  if len(snapshot) > 3 else (*snapshot, *([None]*15))
        if top10 and top10 > 40:
            flags.append(f"🔴 High concentration: top10 = {top10:.0f}%")
        if gini and gini > 0.8:
            flags.append(f"🔴 Very unequal distribution: Gini = {gini:.2f}")
        if sybil and sybil > 50:
            flags.append(f"🟡 Elevated sybil risk: {sybil:.0f}")
        if fresh_pct and fresh_pct > 60:
            flags.append(f"🟡 Many fresh wallets: {fresh_pct:.0f}%")

    if gate_row and gate_row[0]:
        checks = gate_row[0] if isinstance(gate_row[0], dict) else {}
        for name, data in checks.items():
            if isinstance(data, dict) and not data.get("pass"):
                flags.append(f"🔴 Failed gate check: {name}")

    if flags:
        for f in flags:
            lines.append(f"  {f}")
    else:
        lines.append("  ✅ No red flags detected")
    lines.append("")
    return lines


def _exit_triggers_section(token_id, mint, category, regime_mult) -> list[str]:
    lines = ["<b>11. Exit Triggers</b>"]
    try:
        from risk.exits import check_exits
        result = check_exits(token_id, mint, category, regime_mult)
        if result["triggers"]:
            for t in result["triggers"]:
                severity_icon = {"critical": "🔴", "warning": "🟡", "info": "🟢"}.get(
                    t["severity"], "⚪")
                lines.append(f"  {severity_icon} {t['trigger']}: {t['detail']}")
        else:
            lines.append("  ✅ No active exit triggers")
    except Exception as e:
        log.debug("Exit trigger check error: %s", e)
        lines.append("  ⚠️ Exit check unavailable")
    lines.append("")
    return lines


def _position_section(scores_row, category, snapshot, regime_mult) -> list[str]:
    lines = ["<b>12. Position Recommendation</b>"]

    if scores_row and scores_row[3] is not None:
        comp = scores_row[3]
        conf = scores_row[4] or 50
        liq = float(snapshot[3]) if snapshot and snapshot[3] else 50_000

        rec = recommend_position(comp, conf, category, liq,
                                 regime_multiplier=regime_mult)
        lines.append(f"  Tier: {rec['tier']} ({rec['tier_name']})")
        lines.append(f"  Size: {rec['adjusted_size_pct']:.2f}%")
        lines.append(f"  {rec['reason']}")
    else:
        lines.append("  ⚠️ Score data needed for recommendation")

    return lines
