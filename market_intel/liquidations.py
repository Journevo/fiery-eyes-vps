"""Liquidation cluster analysis — magnet zones, proximity warnings."""

from config import get_logger
from market_intel.coinglass import get_liquidations, get_open_interest

log = get_logger("market_intel.liquidations")


def get_liquidation_clusters(symbol: str) -> dict:
    """Identify liquidation clusters above and below current price.

    Returns:
        {"clusters_above": list, "clusters_below": list,
         "nearest_above": float|None, "nearest_below": float|None}
    """
    try:
        liqs = get_liquidations(symbol, hours=48)
        if not liqs.get("total_liq"):
            return {"clusters_above": [], "clusters_below": [],
                    "nearest_above": None, "nearest_below": None}

        # Aggregate liquidation data into price clusters
        long_total = liqs.get("long_liq_usd") or 0
        short_total = liqs.get("short_liq_usd") or 0

        clusters_above = []
        clusters_below = []

        if long_total > 0:
            clusters_above.append({
                "size_usd": long_total,
                "type": "long",
                "description": f"${long_total:,.0f} in long liquidations (48h)",
            })

        if short_total > 0:
            clusters_below.append({
                "size_usd": short_total,
                "type": "short",
                "description": f"${short_total:,.0f} in short liquidations (48h)",
            })

        return {
            "clusters_above": clusters_above,
            "clusters_below": clusters_below,
            "nearest_above": None,
            "nearest_below": None,
        }
    except Exception as e:
        log.error("Liquidation cluster analysis failed for %s: %s", symbol, e)
        return {"clusters_above": [], "clusters_below": [],
                "nearest_above": None, "nearest_below": None}


def calculate_magnet_zones(symbol: str, current_price: float) -> list[dict]:
    """Calculate liquidation magnet zones — price levels where cascading
    liquidations could pull the price.

    Returns:
        list of {price, size_usd, type, proximity_pct}
    """
    zones = []

    try:
        liqs = get_liquidations(symbol, hours=24)
        if not liqs.get("total_liq"):
            return zones

        long_liq = liqs.get("long_liq_usd") or 0
        short_liq = liqs.get("short_liq_usd") or 0

        # Estimate liquidation magnet zones based on leverage ratios
        # Typical liquidation prices cluster at 2-5% from entry
        if current_price > 0:
            for pct in [2, 3, 5]:
                if long_liq > 100_000:
                    below_price = current_price * (1 - pct / 100)
                    zones.append({
                        "price": below_price,
                        "size_usd": long_liq * (0.4 if pct == 3 else 0.3),
                        "type": "long",
                        "proximity_pct": pct,
                    })
                if short_liq > 100_000:
                    above_price = current_price * (1 + pct / 100)
                    zones.append({
                        "price": above_price,
                        "size_usd": short_liq * (0.4 if pct == 3 else 0.3),
                        "type": "short",
                        "proximity_pct": pct,
                    })

        zones.sort(key=lambda z: abs(z["proximity_pct"]))
    except Exception as e:
        log.error("Magnet zone calculation failed for %s: %s", symbol, e)

    return zones


def check_proximity_warnings(symbol: str, current_price: float,
                             threshold_pct: float = 3.0) -> list[dict]:
    """Check if current price is near liquidation clusters.

    Returns:
        list of {level: float, distance_pct: float, size_usd: float, direction: str}
    """
    warnings = []

    try:
        zones = calculate_magnet_zones(symbol, current_price)
        for zone in zones:
            if zone["proximity_pct"] <= threshold_pct:
                warnings.append({
                    "level": zone["price"],
                    "distance_pct": zone["proximity_pct"],
                    "size_usd": zone["size_usd"],
                    "direction": zone["type"],
                })

        if warnings:
            log.info("Proximity warnings for %s: %d zones within %.1f%%",
                     symbol, len(warnings), threshold_pct)
    except Exception as e:
        log.error("Proximity warning check failed for %s: %s", symbol, e)

    return warnings
