"""Exit Trigger Monitor — codified exit rules for all categories.

Momentum exits:
  - Wallet growth negative 3×4h
  - Buyer/seller <1 for 2d
  - Top10 rising >5%
  - Wash score >50
  - Volume <20% of 7d avg

Adoption exits:
  - Revenue negative 2mo
  - Retention <30%
  - Dev decline 3mo
  - Unlock ratio >3x
  - Stablecoin outflow >20% TVL

Infrastructure exits:
  - Revenue decline 2Q
  - Buyback paused
  - Treasury <12mo
  - Market share loss >20%
  - Regulatory event (stub)

Universal exits:
  - Regime <0.5 → reduce Tier 3-4 by 50%
  - Profit-taking: 5x sell 30%, 10x sell 30%, rest rides with 3x stop
  - Portfolio drawdown 25% → halt new entries
"""

from datetime import datetime, timezone
from config import get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json
from telegram_bot.alerts import send_message

log = get_logger("risk.exits")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


# ---------------------------------------------------------------------------
# Momentum exit checks
# ---------------------------------------------------------------------------

def _check_momentum_exits(token_id: int, mint: str, snapshots: list[dict]) -> list[dict]:
    """Check momentum exit triggers."""
    triggers = []

    # 1. Wallet growth negative 3 consecutive periods
    qa = [s.get("holders_quality_adjusted") for s in snapshots[-4:]
          if s.get("holders_quality_adjusted")]
    if len(qa) >= 4:
        declines = sum(1 for i in range(1, len(qa)) if qa[i] < qa[i-1])
        if declines >= 3:
            triggers.append({
                "trigger": "wallet_growth_negative_3x",
                "severity": "critical",
                "detail": f"QA holders declining: {' → '.join(str(h) for h in qa[-4:])}",
            })

    # 2. Buyer/seller <1 for 2d (volume proxy)
    volumes = [s.get("volume") for s in snapshots if s.get("volume")]
    if len(volumes) >= 9:
        baseline_7d = sum(volumes[-9:-2]) / 7
        if baseline_7d > 0:
            recent_2d = volumes[-2:]
            if all(v < baseline_7d * 0.5 for v in recent_2d):
                triggers.append({
                    "trigger": "buyer_seller_below_1_2d",
                    "severity": "warning",
                    "detail": f"Volume below 50% of 7d avg for 2 days",
                })

    # 3. Top10 concentration rising >5%
    top10 = [s.get("top10_pct") for s in snapshots if s.get("top10_pct") is not None]
    if len(top10) >= 2:
        change = top10[-1] - top10[0]
        if change > 5:
            triggers.append({
                "trigger": "top10_rising_5pct",
                "severity": "warning",
                "detail": f"Top10 concentration: {top10[0]:.1f}% → {top10[-1]:.1f}%",
            })

    # 4. Wash score >50
    sybil = [s.get("sybil_risk_score") for s in snapshots if s.get("sybil_risk_score") is not None]
    if sybil and sybil[-1] > 50:
        triggers.append({
            "trigger": "wash_score_elevated",
            "severity": "warning",
            "detail": f"Sybil risk score: {sybil[-1]:.0f}",
        })

    # 5. Volume <20% of 7d avg
    if len(volumes) >= 7:
        avg_7d = sum(volumes[-7:]) / 7
        if avg_7d > 0 and volumes[-1] < avg_7d * 0.2:
            triggers.append({
                "trigger": "volume_below_20pct_7d",
                "severity": "critical",
                "detail": f"Volume ${volumes[-1]:,.0f} vs 7d avg ${avg_7d:,.0f}",
            })

    return triggers


# ---------------------------------------------------------------------------
# Adoption exit checks
# ---------------------------------------------------------------------------

def _check_adoption_exits(token_id: int, snapshots: list[dict]) -> list[dict]:
    """Check adoption exit triggers."""
    triggers = []

    # 1. Revenue negative 2mo
    revenues = [s.get("revenue") for s in snapshots if s.get("revenue") is not None]
    if len(revenues) >= 60:
        m1 = sum(r for r in revenues[-30:] if r)
        m2 = sum(r for r in revenues[-60:-30] if r)
        if m2 > 0 and m1 < m2 * 0.8:
            triggers.append({
                "trigger": "revenue_negative_2mo",
                "severity": "critical",
                "detail": f"Revenue declining: ${m2:,.0f} → ${m1:,.0f}",
            })

    # 2. Retention <30%
    if snapshots:
        ret = snapshots[-1].get("retention_30d")
        if ret is not None and ret < 0.3:
            triggers.append({
                "trigger": "retention_below_30pct",
                "severity": "critical",
                "detail": f"30d retention: {ret*100:.0f}%",
            })

    # 3. Dev decline 3mo
    dev = [s.get("dev_commits") for s in snapshots if s.get("dev_commits") is not None]
    if len(dev) >= 90:
        m1 = sum(dev[-30:])
        m2 = sum(dev[-60:-30])
        m3 = sum(dev[-90:-60])
        if m1 < m2 < m3 and m3 > 0:
            triggers.append({
                "trigger": "dev_decline_3mo",
                "severity": "warning",
                "detail": f"Dev commits declining: {m3} → {m2} → {m1}",
            })

    # 4. Unlock ratio >3x
    if snapshots:
        ratio = snapshots[-1].get("unlock_to_volume_ratio")
        if ratio and ratio > 3.0:
            triggers.append({
                "trigger": "unlock_ratio_above_3x",
                "severity": "warning",
                "detail": f"Unlock/volume ratio: {ratio:.1f}x",
            })

    # 5. Stablecoin outflow >20% TVL
    inflows = [s.get("stablecoin_inflow") for s in snapshots if s.get("stablecoin_inflow") is not None]
    if len(inflows) >= 2 and inflows[-2] and inflows[-2] > 0:
        change = (inflows[-1] - inflows[-2]) / inflows[-2]
        if change < -0.2:
            triggers.append({
                "trigger": "stablecoin_outflow_20pct",
                "severity": "warning",
                "detail": f"Stablecoin change: {change*100:.0f}%",
            })

    return triggers


# ---------------------------------------------------------------------------
# Infrastructure exit checks
# ---------------------------------------------------------------------------

def _check_infra_exits(token_id: int, snapshots: list[dict]) -> list[dict]:
    """Check infrastructure exit triggers."""
    triggers = []

    # 1. Revenue decline 2Q
    revenues = [s.get("revenue") for s in snapshots if s.get("revenue") is not None]
    if len(revenues) >= 180:
        q1 = sum(r for r in revenues[-90:] if r)
        q2 = sum(r for r in revenues[-180:-90] if r)
        if q2 > 0 and q1 < q2 * 0.8:
            triggers.append({
                "trigger": "revenue_decline_2q",
                "severity": "critical",
                "detail": f"Quarterly revenue declining: ${q2:,.0f} → ${q1:,.0f}",
            })

    # 2. Buyback paused (volume decline as proxy)
    volumes = [s.get("volume") for s in snapshots if s.get("volume")]
    if len(volumes) >= 30:
        recent_7d = sum(volumes[-7:])
        baseline = sum(volumes[-30:]) / 30 * 7
        if baseline > 0 and recent_7d < baseline * 0.3:
            triggers.append({
                "trigger": "buyback_paused_proxy",
                "severity": "warning",
                "detail": "Activity at <30% of 30d baseline",
            })

    # 3. Treasury <12mo (mcap/revenue proxy)
    if snapshots:
        rev = snapshots[-1].get("revenue")
        mcap = snapshots[-1].get("mcap")
        if rev and mcap and mcap > 0:
            annual_rev = rev * 365
            if annual_rev / mcap < 0.03:
                triggers.append({
                    "trigger": "treasury_runway_low",
                    "severity": "warning",
                    "detail": f"Annual revenue/mcap: {annual_rev/mcap*100:.1f}%",
                })

    # 4. Market share loss (mcap decline as proxy)
    if len(snapshots) >= 30:
        mcap_now = snapshots[-1].get("mcap") or 0
        mcap_30d = snapshots[-30].get("mcap") or snapshots[0].get("mcap") or 0
        if mcap_30d > 0:
            loss = (mcap_30d - mcap_now) / mcap_30d
            if loss > 0.2:
                triggers.append({
                    "trigger": "market_share_loss_20pct",
                    "severity": "warning",
                    "detail": f"Mcap declined {loss*100:.0f}% in 30d",
                })

    # 5. Regulatory event — STUB
    # Would need news API integration
    return triggers


# ---------------------------------------------------------------------------
# Liquidation + Unlock proximity checks
# ---------------------------------------------------------------------------

def _check_liquidation_proximity(mint: str, symbol: str) -> list[dict]:
    """Check if price is near liquidation clusters."""
    triggers = []
    try:
        from market_intel.liquidations import check_proximity_warnings
        # Get current price from DexScreener
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            return triggers
        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        price = float(best.get("priceUsd") or 0)
        if price <= 0:
            return triggers

        warnings = check_proximity_warnings(symbol, price, threshold_pct=3.0)
        for w in warnings:
            triggers.append({
                "trigger": "liquidation_proximity",
                "severity": "warning",
                "detail": (f"Liquidation cluster at ${w['level']:,.2f} "
                          f"({w['distance_pct']:.1f}% away, ${w['size_usd']:,.0f} {w['direction']})"),
            })
    except Exception as e:
        log.debug("Liquidation proximity check skipped: %s", e)
    return triggers


def _check_unlock_proximity(token_id: int, mint: str, symbol: str) -> list[dict]:
    """Check for upcoming token unlocks within 7 days."""
    triggers = []
    try:
        from market_intel.unlocks import get_upcoming_unlocks, calculate_unlock_risk
        unlocks = get_upcoming_unlocks(symbol)
        if not unlocks:
            return triggers

        # Check for cliff unlocks within 7 days
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        for unlock in unlocks:
            unlock_date = unlock.get("date")
            if not unlock_date:
                continue
            if isinstance(unlock_date, str):
                try:
                    unlock_date = datetime.fromisoformat(unlock_date.replace("Z", "+00:00"))
                except Exception:
                    continue
            days_until = (unlock_date - now).days
            if 0 <= days_until <= 7 and unlock.get("type") == "cliff":
                pct = unlock.get("pct_of_supply", 0)
                triggers.append({
                    "trigger": "unlock_cliff_7d",
                    "severity": "critical" if pct > 5 else "warning",
                    "detail": (f"Cliff unlock in {days_until}d: "
                              f"{pct:.1f}% of supply (${unlock.get('amount_usd', 0):,.0f})"),
                })

        # Check unlock-to-volume ratio
        risk = calculate_unlock_risk(symbol, 0)
        if risk.get("risk_level") == "red":
            triggers.append({
                "trigger": "unlock_volume_ratio_high",
                "severity": "warning",
                "detail": f"Unlock/volume ratio: {risk.get('unlock_to_volume_ratio', 0):.1f}x (>3x = red)",
            })
    except Exception as e:
        log.debug("Unlock proximity check skipped: %s", e)
    return triggers


# ---------------------------------------------------------------------------
# Universal exit checks
# ---------------------------------------------------------------------------

def _check_universal_exits(regime_multiplier: float,
                           position: dict | None = None) -> list[dict]:
    """Check universal exit triggers."""
    triggers = []

    # 1. Regime <0.5 → reduce Tier 3-4 by 50%
    if regime_multiplier < 0.5:
        triggers.append({
            "trigger": "regime_cash_mode",
            "severity": "critical",
            "detail": f"Regime {regime_multiplier:.2f} — reduce Tier 3-4 by 50%",
        })
    elif regime_multiplier < 0.6:
        triggers.append({
            "trigger": "regime_defensive",
            "severity": "warning",
            "detail": f"Regime {regime_multiplier:.2f} — Tier 1-2 only",
        })

    # 2. Profit-taking rules (requires position + current price)
    if position:
        entry = position.get("entry_price", 0)
        current = position.get("current_price", 0)
        if entry > 0 and current > 0:
            multiple = current / entry
            if multiple >= 10:
                triggers.append({
                    "trigger": "profit_take_10x",
                    "severity": "info",
                    "detail": f"At {multiple:.1f}x — sell 30%, ride rest with 3x stop",
                })
            elif multiple >= 5:
                triggers.append({
                    "trigger": "profit_take_5x",
                    "severity": "info",
                    "detail": f"At {multiple:.1f}x — sell 30%",
                })

    return triggers


def _check_portfolio_drawdown() -> list[dict]:
    """Check portfolio-level drawdown. Returns triggers if drawdown >25%."""
    # STUB: requires portfolio value tracking over time
    # Would compare current total value vs peak value
    log.debug("STUB: portfolio drawdown check — manual input needed")
    return []


# ---------------------------------------------------------------------------
# Snapshot fetcher
# ---------------------------------------------------------------------------

def _get_snapshots(token_id: int, days: int = 180) -> list[dict]:
    """Fetch snapshot history for a token."""
    try:
        rows = execute(
            """SELECT date, price, mcap, volume, liquidity_depth_10k,
                      holders_raw, holders_quality_adjusted,
                      retention_7d, retention_30d,
                      top10_pct, top50_pct, gini,
                      median_wallet_balance, fees, revenue,
                      stablecoin_inflow, dev_commits, dev_active,
                      social_velocity, smart_money_netflow,
                      fresh_wallet_pct, sybil_risk_score,
                      unlock_to_volume_ratio
               FROM snapshots_daily
               WHERE token_id = %s AND date >= CURRENT_DATE - %s
               ORDER BY date ASC""",
            (token_id, days),
            fetch=True,
        )
        if not rows:
            return []
        keys = ["date", "price", "mcap", "volume", "liquidity_depth_10k",
                "holders_raw", "holders_quality_adjusted",
                "retention_7d", "retention_30d",
                "top10_pct", "top50_pct", "gini",
                "median_wallet_balance", "fees", "revenue",
                "stablecoin_inflow", "dev_commits", "dev_active",
                "social_velocity", "smart_money_netflow",
                "fresh_wallet_pct", "sybil_risk_score",
                "unlock_to_volume_ratio"]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        log.error("Failed to fetch snapshots for token_id=%d: %s", token_id, e)
        return []


# ---------------------------------------------------------------------------
# Main exit check
# ---------------------------------------------------------------------------

def check_exits(token_id: int, mint: str, category: str,
                regime_multiplier: float = 1.0,
                position: dict | None = None) -> dict:
    """Run all applicable exit checks for a token.

    Returns:
        {
            "token_id": int,
            "category": str,
            "triggers": list[dict],
            "has_critical": bool,
            "has_warning": bool,
            "action_required": bool,
        }
    """
    snapshots = _get_snapshots(token_id)

    all_triggers = []

    # Always check momentum exits
    all_triggers.extend(_check_momentum_exits(token_id, mint, snapshots))

    # Category-specific
    if category in ("adoption", "infra"):
        all_triggers.extend(_check_adoption_exits(token_id, snapshots))
    if category == "infra":
        all_triggers.extend(_check_infra_exits(token_id, snapshots))

    # Universal
    all_triggers.extend(_check_universal_exits(regime_multiplier, position))
    all_triggers.extend(_check_portfolio_drawdown())

    # Liquidation proximity
    symbol = None
    try:
        from db.connection import execute_one as _eo
        sym_row = _eo("SELECT symbol FROM tokens WHERE id = %s", (token_id,))
        symbol = sym_row[0] if sym_row else mint[:8]
    except Exception:
        symbol = mint[:8]
    all_triggers.extend(_check_liquidation_proximity(mint, symbol))
    all_triggers.extend(_check_unlock_proximity(token_id, mint, symbol))

    has_critical = any(t["severity"] == "critical" for t in all_triggers)
    has_warning = any(t["severity"] == "warning" for t in all_triggers)

    result = {
        "token_id": token_id,
        "mint": mint,
        "category": category,
        "triggers": all_triggers,
        "has_critical": has_critical,
        "has_warning": has_warning,
        "action_required": has_critical,
    }

    if all_triggers:
        log.info("Exit triggers for %s: %d total (%s critical)",
                 mint, len(all_triggers),
                 sum(1 for t in all_triggers if t["severity"] == "critical"))

    return result


def check_all_exits(regime_multiplier: float = 1.0) -> list[dict]:
    """Check exit triggers for all open positions and gate-pass tokens."""
    log.info("=== Checking exit triggers for all tokens ===")

    results = []

    # Check all gate-pass tokens (not just open positions)
    try:
        rows = execute(
            """SELECT id, contract_address, symbol, category
               FROM tokens WHERE quality_gate_pass = TRUE""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query tokens for exit check: %s", e)
        return results

    if not rows:
        return results

    for token_id, mint, symbol, category in rows:
        cat = category or "meme"
        result = check_exits(token_id, mint, cat, regime_multiplier)
        result["symbol"] = symbol
        if result["triggers"]:
            results.append(result)

    # Send Telegram alerts for critical triggers
    critical_results = [r for r in results if r["has_critical"]]
    if critical_results:
        _send_exit_alerts(critical_results)

    log.info("Exit check complete: %d tokens with triggers (%d critical)",
             len(results), len(critical_results))

    return results


def _send_exit_alerts(results: list[dict]):
    """Send Telegram alerts for exit triggers with urgency formatting."""
    for result in results:
        lines = [
            "⚠️ <b>EXIT TRIGGER ALERT</b> ⚠️",
            f"Token: <code>{result.get('symbol', '???')}</code> ({result['mint'][:12]}...)",
            f"Category: {result['category']}",
            "",
        ]

        for trigger in result["triggers"]:
            severity = trigger["severity"]
            icon = "🔴" if severity == "critical" else "🟡" if severity == "warning" else "🟢"
            lines.append(f"{icon} <b>{trigger['trigger']}</b>")
            lines.append(f"   {trigger['detail']}")

        if result["action_required"]:
            lines.append("")
            lines.append("⚡ <b>ACTION REQUIRED</b>: Review position immediately")

        send_message("\n".join(lines))
