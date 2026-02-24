"""Momentum Engine — scores token momentum on 0-100 scale.

Weighted factors:
  1. Wallet growth acceleration (quality-adjusted):  20%
  2. Short-term retention (48h + 7d cohort):         20%
  3. Volume vs 30d baseline:                         15%
  4. Smart money net exposure:                       15% (stub)
  5. Liquidity depth:                                10%
  6. Social velocity:                                10% (DexScreener proxy)
  7. Distribution improving:                         10%

Exit triggers:
  - Wallet growth negative 3 consecutive 4h periods
  - Buyer/seller ratio < 1 for 2 consecutive days
  - Top10 concentration rising > 5%
"""

from datetime import date, timedelta
from config import get_logger
from db.connection import execute

log = get_logger("engines.momentum")


# ---------------------------------------------------------------------------
# Factor scoring (each returns 0-100)
# ---------------------------------------------------------------------------

def _score_wallet_growth(snapshots: list[dict]) -> float:
    """Score wallet growth acceleration from quality-adjusted holder counts.
    Compares recent growth rate to prior growth rate."""
    if len(snapshots) < 3:
        return 50.0  # insufficient data, neutral

    qa_holders = [s["holders_quality_adjusted"] for s in snapshots if s["holders_quality_adjusted"]]
    if len(qa_holders) < 3:
        return 50.0

    # Recent growth (last entry vs 2nd to last)
    recent_growth = (qa_holders[-1] - qa_holders[-2]) / max(qa_holders[-2], 1)
    # Prior growth
    prior_growth = (qa_holders[-2] - qa_holders[-3]) / max(qa_holders[-3], 1)

    # Acceleration
    if prior_growth == 0:
        accel = recent_growth * 100
    else:
        accel = ((recent_growth - prior_growth) / abs(prior_growth)) * 100

    # Map to 0-100
    if accel <= -50:
        return 0
    if accel >= 100:
        return 100
    return max(0, min(100, 50 + accel * 0.5))


def _score_retention(snapshots: list[dict]) -> float:
    """Score short-term retention from 7d retention metric.
    48h retention not yet available — use 7d as proxy."""
    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    ret_7d = latest.get("retention_7d")

    if ret_7d is None:
        # Estimate from holder count stability
        if len(snapshots) >= 7:
            holders_now = snapshots[-1].get("holders_quality_adjusted") or 0
            holders_7d = snapshots[-7].get("holders_quality_adjusted") or 0
            if holders_7d > 0:
                retention_est = min(1.0, holders_now / holders_7d)
                return retention_est * 100
        return 50.0

    # 7d retention: 0-100% mapped to 0-100 score
    return max(0, min(100, ret_7d * 100))


def _score_volume_baseline(snapshots: list[dict]) -> float:
    """Score current volume vs 30d average baseline.
    > 2x baseline = high score, < 0.5x = low score."""
    if not snapshots:
        return 50.0

    volumes = [s["volume"] for s in snapshots if s.get("volume")]
    if not volumes:
        return 50.0

    current_vol = volumes[-1]
    if len(volumes) >= 30:
        baseline = sum(volumes[-30:]) / 30
    else:
        baseline = sum(volumes) / len(volumes)

    if baseline == 0:
        return 80.0 if current_vol > 0 else 50.0

    ratio = current_vol / baseline
    if ratio >= 3.0:
        return 100
    if ratio >= 2.0:
        return 85
    if ratio >= 1.5:
        return 70
    if ratio >= 1.0:
        return 55
    if ratio >= 0.5:
        return 30
    return 10


def _score_smart_money(snapshots: list[dict]) -> float:
    """Smart money net exposure. STUB — returns neutral until wallet tracker built."""
    if not snapshots:
        return 50.0
    latest = snapshots[-1]
    flow = latest.get("smart_money_netflow")
    if flow is None:
        return 50.0  # neutral — no data
    # Positive flow = bullish
    if flow > 0:
        return min(100, 60 + flow * 0.01)
    return max(0, 50 + flow * 0.01)


def _score_liquidity_depth(snapshots: list[dict]) -> float:
    """Score liquidity depth. Higher USD liquidity = safer."""
    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    liq = latest.get("liquidity_depth_10k") or 0

    if liq >= 500_000:
        return 100
    if liq >= 200_000:
        return 85
    if liq >= 100_000:
        return 70
    if liq >= 50_000:
        return 55
    if liq >= 20_000:
        return 35
    return 15


def _score_social_velocity(snapshots: list[dict], mint: str | None = None) -> float:
    """Social velocity using Social Pulse score when available,
    falls back to DexScreener social link count."""
    # Try social pulse first
    if mint:
        try:
            from social.pulse import calculate_pulse
            pulse = calculate_pulse(mint[:8], mint=mint)
            pulse_score = pulse.get("pulse_score", 0)
            if pulse_score > 0:
                return pulse_score
        except Exception:
            pass  # fall through to DexScreener proxy

    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    social = latest.get("social_velocity") or 0

    # DexScreener social links: 0-5+
    if social >= 4:
        return 90
    if social >= 3:
        return 75
    if social >= 2:
        return 60
    if social >= 1:
        return 45
    return 20


def _score_distribution(snapshots: list[dict]) -> float:
    """Score whether distribution is improving (top10 concentration decreasing)."""
    if len(snapshots) < 2:
        return 50.0

    top10_values = [(s.get("top10_pct"), s.get("gini")) for s in snapshots
                    if s.get("top10_pct") is not None]

    if len(top10_values) < 2:
        return 50.0

    recent_top10 = top10_values[-1][0]
    prior_top10 = top10_values[-2][0]

    # Improving = top10 going down
    change = prior_top10 - recent_top10  # positive = improving

    if change > 5:
        return 90
    if change > 2:
        return 75
    if change > 0:
        return 60
    if change > -2:
        return 45
    if change > -5:
        return 30
    return 10


# ---------------------------------------------------------------------------
# Exit trigger detection
# ---------------------------------------------------------------------------

def _check_exit_triggers(snapshots: list[dict]) -> list[str]:
    """Check for codified exit triggers.

    Returns list of triggered exit condition descriptions.
    """
    triggers = []

    if len(snapshots) < 2:
        return triggers

    # 1. Wallet growth negative for 3 consecutive periods
    qa_holders = [s.get("holders_quality_adjusted") for s in snapshots[-4:]
                  if s.get("holders_quality_adjusted")]
    if len(qa_holders) >= 4:
        declines = 0
        for i in range(1, len(qa_holders)):
            if qa_holders[i] < qa_holders[i - 1]:
                declines += 1
            else:
                declines = 0
        if declines >= 3:
            triggers.append("wallet_growth_negative_3x")

    # 2. Volume declining (buyer/seller < 1 proxy: volume below baseline 2 days)
    volumes = [s.get("volume") for s in snapshots if s.get("volume")]
    if len(volumes) >= 30:
        baseline = sum(volumes[-30:]) / 30
        if baseline > 0:
            recent_2d = volumes[-2:]
            if all(v < baseline * 0.5 for v in recent_2d):
                triggers.append("volume_below_baseline_2d")

    # 3. Top10 concentration rising > 5%
    top10_values = [s.get("top10_pct") for s in snapshots if s.get("top10_pct") is not None]
    if len(top10_values) >= 2:
        change = top10_values[-1] - top10_values[0]
        if change > 5:
            triggers.append("top10_concentration_rising_5pct")

    return triggers


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def _get_snapshots(token_id: int, days: int = 30) -> list[dict]:
    """Fetch recent snapshots for a token."""
    try:
        rows = execute(
            """SELECT date, price, mcap, volume, liquidity_depth_10k,
                      holders_raw, holders_quality_adjusted,
                      retention_7d, retention_30d,
                      top10_pct, top50_pct, gini,
                      median_wallet_balance, social_velocity,
                      smart_money_netflow, fresh_wallet_pct, sybil_risk_score
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
                "median_wallet_balance", "social_velocity",
                "smart_money_netflow", "fresh_wallet_pct", "sybil_risk_score"]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        log.error("Failed to fetch snapshots for token_id=%d: %s", token_id, e)
        return []


def score(token_id: int, mint: str | None = None) -> dict:
    """Calculate Momentum Engine score for a token.

    Returns:
        {
            "momentum_score": float (0-100),
            "factors": {
                "wallet_growth": float,
                "retention": float,
                "volume_baseline": float,
                "smart_money": float,
                "liquidity": float,
                "social": float,
                "distribution": float,
            },
            "exit_triggers": list[str],
            "data_points": int,
        }
    """
    snapshots = _get_snapshots(token_id, days=30)

    factors = {
        "wallet_growth": _score_wallet_growth(snapshots),
        "retention": _score_retention(snapshots),
        "volume_baseline": _score_volume_baseline(snapshots),
        "smart_money": _score_smart_money(snapshots),
        "liquidity": _score_liquidity_depth(snapshots),
        "social": _score_social_velocity(snapshots, mint=mint),
        "distribution": _score_distribution(snapshots),
    }

    weights = {
        "wallet_growth": 0.20,
        "retention": 0.20,
        "volume_baseline": 0.15,
        "smart_money": 0.15,
        "liquidity": 0.10,
        "social": 0.10,
        "distribution": 0.10,
    }

    momentum_score = sum(factors[k] * weights[k] for k in weights)
    exit_triggers = _check_exit_triggers(snapshots)

    result = {
        "momentum_score": round(momentum_score, 1),
        "factors": {k: round(v, 1) for k, v in factors.items()},
        "exit_triggers": exit_triggers,
        "data_points": len(snapshots),
    }

    log.info("Momentum score for token_id=%d: %.1f (data_points=%d, triggers=%s)",
             token_id, momentum_score, len(snapshots), exit_triggers or "none")

    return result
