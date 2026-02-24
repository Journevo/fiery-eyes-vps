"""Virality Integrity Scorer — distinguishes organic vs manufactured attention.

Analysis:
  - Time-decay shape: spike-then-cliff = manufactured (20), multiple waves = organic (80)
  - Conversion test: does volume/holder growth follow social attention within 72h?
  - Virality WITHOUT wallet growth within 72h = noise (score 0)
  - Adjusted Virality Score = Raw × (Integrity / 100)

Integrates into Momentum Engine as a modifier.
"""

from config import get_logger
from db.connection import execute
from quality_gate.helpers import get_json

log = get_logger("virality.integrity")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_volume_history(mint: str) -> list[dict]:
    """Fetch volume + price data from DexScreener to analyze patterns.
    Returns list of {volume_24h, price, liquidity, social_count} for the best pair."""
    try:
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            return []

        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))

        # DexScreener gives current data; for history we use snapshots
        volume_24h = float(best.get("volume", {}).get("h24", 0) or 0)
        volume_6h = float(best.get("volume", {}).get("h6", 0) or 0)
        volume_1h = float(best.get("volume", {}).get("h1", 0) or 0)

        price_change_5m = float(best.get("priceChange", {}).get("m5", 0) or 0)
        price_change_1h = float(best.get("priceChange", {}).get("h1", 0) or 0)
        price_change_6h = float(best.get("priceChange", {}).get("h6", 0) or 0)
        price_change_24h = float(best.get("priceChange", {}).get("h24", 0) or 0)

        txns = best.get("txns", {})
        buys_24h = txns.get("h24", {}).get("buys", 0) or 0
        sells_24h = txns.get("h24", {}).get("sells", 0) or 0
        buys_6h = txns.get("h6", {}).get("buys", 0) or 0
        sells_6h = txns.get("h6", {}).get("sells", 0) or 0
        buys_1h = txns.get("h1", {}).get("buys", 0) or 0
        sells_1h = txns.get("h1", {}).get("sells", 0) or 0

        social_count = len(best.get("info", {}).get("socials", []) if best.get("info") else [])

        return [{
            "volume_24h": volume_24h,
            "volume_6h": volume_6h,
            "volume_1h": volume_1h,
            "price_change_5m": price_change_5m,
            "price_change_1h": price_change_1h,
            "price_change_6h": price_change_6h,
            "price_change_24h": price_change_24h,
            "buys_24h": buys_24h,
            "sells_24h": sells_24h,
            "buys_6h": buys_6h,
            "sells_6h": sells_6h,
            "buys_1h": buys_1h,
            "sells_1h": sells_1h,
            "social_count": social_count,
        }]
    except Exception as e:
        log.error("Failed to fetch volume history for %s: %s", mint, e)
        return []


def _get_snapshot_history(token_id: int) -> list[dict]:
    """Get snapshot history for time-series analysis."""
    try:
        rows = execute(
            """SELECT date, volume, holders_raw, holders_quality_adjusted,
                      social_velocity, price
               FROM snapshots_daily
               WHERE token_id = %s
               ORDER BY date ASC""",
            (token_id,),
            fetch=True,
        )
        if not rows:
            return []
        keys = ["date", "volume", "holders_raw", "holders_quality_adjusted",
                "social_velocity", "price"]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        log.error("Failed to fetch snapshot history for token_id=%d: %s", token_id, e)
        return []


# ---------------------------------------------------------------------------
# Shape analysis
# ---------------------------------------------------------------------------

def _analyze_time_decay_shape(dex_data: list[dict], snapshots: list[dict]) -> float:
    """Analyze volume time-decay shape.

    Spike-then-cliff = manufactured (score ~20)
    Multiple waves / sustained = organic (score ~80)
    Returns 0-100.
    """
    if not dex_data:
        return 50.0  # no data, neutral

    d = dex_data[0]

    # Analyze volume distribution across time windows
    vol_24h = d.get("volume_24h", 0)
    vol_6h = d.get("volume_6h", 0)
    vol_1h = d.get("volume_1h", 0)

    if vol_24h <= 0:
        return 30.0

    # Check if volume is front-loaded (spike) or distributed (organic)
    # If 6h vol is >70% of 24h → recent spike
    # If 1h vol is >40% of 6h → very concentrated → likely manufactured
    ratio_6h_24h = vol_6h / vol_24h if vol_24h > 0 else 0
    ratio_1h_6h = vol_1h / vol_6h if vol_6h > 0 else 0

    shape_score = 50.0

    # Spike detection: most volume in recent window
    if ratio_6h_24h > 0.7 and ratio_1h_6h > 0.5:
        shape_score = 20.0  # very concentrated = manufactured
    elif ratio_6h_24h > 0.6:
        shape_score = 35.0  # somewhat front-loaded
    elif ratio_6h_24h > 0.3:
        shape_score = 65.0  # reasonably distributed
    else:
        shape_score = 80.0  # spread across day = organic

    # Check buy/sell balance (manufactured often has lopsided buys)
    buys = d.get("buys_24h", 0)
    sells = d.get("sells_24h", 0)
    total_txns = buys + sells
    if total_txns > 0:
        buy_ratio = buys / total_txns
        if buy_ratio > 0.85:
            shape_score *= 0.7  # suspiciously one-sided
        elif buy_ratio > 0.7:
            shape_score *= 0.85
        elif buy_ratio < 0.3:
            shape_score *= 0.6  # dump pattern

    # Multi-day pattern from snapshots
    if len(snapshots) >= 3:
        volumes = [s.get("volume") or 0 for s in snapshots]
        # Count "waves" (local maxima)
        waves = 0
        for i in range(1, len(volumes) - 1):
            if volumes[i] > volumes[i-1] and volumes[i] > volumes[i+1]:
                waves += 1
        if waves >= 2:
            shape_score = min(100, shape_score + 20)  # multiple waves = organic

    return max(0, min(100, shape_score))


# ---------------------------------------------------------------------------
# Conversion test
# ---------------------------------------------------------------------------

def _test_conversion(dex_data: list[dict], snapshots: list[dict]) -> float:
    """Conversion test: does volume/holder growth follow social attention?

    Virality WITHOUT wallet growth within 72h = noise (score 0).
    Returns 0-100.
    """
    if not dex_data:
        return 50.0

    d = dex_data[0]
    social = d.get("social_count", 0)

    # No social presence at all — can't measure conversion
    if social == 0:
        return 40.0

    # Check if there's actual holder growth in snapshots
    if len(snapshots) >= 3:
        holders_recent = snapshots[-1].get("holders_quality_adjusted") or 0
        holders_3d_ago = snapshots[-3].get("holders_quality_adjusted") or snapshots[0].get("holders_quality_adjusted") or 0

        if holders_3d_ago > 0:
            growth = (holders_recent - holders_3d_ago) / holders_3d_ago
            if growth > 0.1:
                return 90.0  # strong holder growth following attention
            if growth > 0.02:
                return 70.0  # modest growth
            if growth > -0.02:
                return 40.0  # flat — attention without conversion
            return 10.0  # holders declining despite attention = noise

    # Fallback: use buy/sell ratio as proxy
    buys = d.get("buys_24h", 0)
    sells = d.get("sells_24h", 0)
    if buys + sells > 0:
        net_buy_ratio = (buys - sells) / (buys + sells)
        if net_buy_ratio > 0.3:
            return 70.0
        if net_buy_ratio > 0:
            return 55.0
        return 25.0  # net selling despite social presence

    return 50.0


# ---------------------------------------------------------------------------
# Main scorer
# ---------------------------------------------------------------------------

def score(mint: str, token_id: int) -> dict:
    """Calculate Virality Integrity Score.

    Returns:
        {
            "raw_virality": float (0-100),     # social presence + volume
            "integrity": float (0-100),         # organic vs manufactured
            "adjusted_virality": float (0-100), # raw × (integrity / 100)
            "shape_score": float,
            "conversion_score": float,
            "momentum_modifier": float,         # multiplier for momentum engine
        }
    """
    dex_data = _fetch_volume_history(mint)
    snapshots = _get_snapshot_history(token_id)

    # Raw virality: social presence + volume magnitude (with cross-platform data)
    raw_virality = _calculate_raw_virality(dex_data, mint=mint)

    # Integrity components
    shape_score = _analyze_time_decay_shape(dex_data, snapshots)
    conversion_score = _test_conversion(dex_data, snapshots)

    # Integrity = average of shape + conversion
    integrity = (shape_score + conversion_score) / 2

    # Adjusted virality = raw × (integrity / 100)
    adjusted_virality = raw_virality * (integrity / 100)

    # Momentum modifier: 0.7–1.3 range
    # High adjusted virality boosts momentum, low dampens it
    if adjusted_virality >= 70:
        momentum_modifier = 1.3
    elif adjusted_virality >= 50:
        momentum_modifier = 1.1
    elif adjusted_virality >= 30:
        momentum_modifier = 1.0
    elif adjusted_virality >= 15:
        momentum_modifier = 0.85
    else:
        momentum_modifier = 0.7

    result = {
        "raw_virality": round(raw_virality, 1),
        "integrity": round(integrity, 1),
        "adjusted_virality": round(adjusted_virality, 1),
        "shape_score": round(shape_score, 1),
        "conversion_score": round(conversion_score, 1),
        "momentum_modifier": momentum_modifier,
    }

    log.info("Virality for %s: raw=%.0f integrity=%.0f adjusted=%.0f modifier=%.2f",
             mint, raw_virality, integrity, adjusted_virality, momentum_modifier)

    return result


def _calculate_raw_virality(dex_data: list[dict], mint: str | None = None) -> float:
    """Raw virality score from social presence + volume magnitude.
    Uses cross-platform social pulse when available."""
    # Try cross-platform social pulse first
    cross_platform_score = None
    if mint:
        try:
            from social.pulse import calculate_pulse
            pulse = calculate_pulse(mint[:8], mint=mint)
            if pulse.get("pulse_score", 0) > 0:
                cross_platform_score = pulse["pulse_score"]
                # Boost if high conviction (3+ platforms)
                if pulse.get("high_conviction"):
                    cross_platform_score = min(100, cross_platform_score * 1.2)
        except Exception:
            pass

    if not dex_data:
        return cross_platform_score or 30.0

    d = dex_data[0]
    social = d.get("social_count", 0)
    vol = d.get("volume_24h", 0)

    # Social presence: use cross-platform if available, else DexScreener links
    if cross_platform_score is not None:
        social_score = cross_platform_score
    else:
        social_score = min(100, social * 25)

    # Volume magnitude
    if vol >= 1_000_000:
        vol_score = 100
    elif vol >= 500_000:
        vol_score = 85
    elif vol >= 100_000:
        vol_score = 65
    elif vol >= 50_000:
        vol_score = 45
    elif vol >= 10_000:
        vol_score = 25
    else:
        vol_score = 10

    # Transaction count as activity indicator
    buys = d.get("buys_24h", 0)
    sells = d.get("sells_24h", 0)
    total_txns = buys + sells
    if total_txns >= 5000:
        txn_score = 100
    elif total_txns >= 1000:
        txn_score = 75
    elif total_txns >= 200:
        txn_score = 50
    else:
        txn_score = 25

    # Weighted: social 40%, volume 35%, transactions 25%
    return social_score * 0.4 + vol_score * 0.35 + txn_score * 0.25
