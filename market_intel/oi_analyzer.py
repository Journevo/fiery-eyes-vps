"""OI + Price Regime Analyzer — determines market structure from derivatives data.

Regimes:
  OI rising  + price rising  = new_longs (bullish)
  OI rising  + price falling = shorts_opening (bearish)
  OI falling + price rising  = short_squeeze (bullish but fragile)
  OI falling + price falling = capitulation (bearish, potential bottom)

Funding signals:
  > 0.05% = overleveraged_long
  < -0.05% = overleveraged_short
"""

from config import get_logger
from market_intel.coinglass import (
    get_open_interest, get_funding_rates,
    get_long_short_ratio, get_liquidations,
)

log = get_logger("market_intel.oi_analyzer")


def analyze_oi_regime(symbol: str, current_price: float,
                      price_24h_ago: float) -> dict:
    """Analyze OI regime based on OI change and price change.

    Returns:
        {
            "oi_regime": str,        # new_longs, shorts_opening, short_squeeze, capitulation, unknown
            "funding_signal": str,   # overleveraged_long, overleveraged_short, neutral
            "leverage_risk": float,  # 0-100
            "interpretation": str,
        }
    """
    oi_data = get_open_interest(symbol)
    funding = get_funding_rates(symbol)

    oi_change = oi_data.get("oi_change_24h")
    funding_rate = funding.get("current_rate")

    # Determine price direction
    if current_price > 0 and price_24h_ago > 0:
        price_rising = current_price > price_24h_ago
    else:
        price_rising = None

    # Determine OI direction
    oi_rising = None
    if oi_change is not None:
        oi_rising = oi_change > 0

    # Classify regime
    if oi_rising is None or price_rising is None:
        oi_regime = "unknown"
        interpretation = "Insufficient data for OI regime classification"
    elif oi_rising and price_rising:
        oi_regime = "new_longs"
        interpretation = "New long positions opening — bullish momentum"
    elif oi_rising and not price_rising:
        oi_regime = "shorts_opening"
        interpretation = "New short positions opening — bearish pressure"
    elif not oi_rising and price_rising:
        oi_regime = "short_squeeze"
        interpretation = "Short squeeze — bullish but fragile, shorts covering"
    else:
        oi_regime = "capitulation"
        interpretation = "Capitulation — bearish, potential bottom forming"

    # Funding signal
    if funding_rate is not None:
        if funding_rate > 0.05:
            funding_signal = "overleveraged_long"
            interpretation += ". WARNING: funding rate elevated, long squeeze risk"
        elif funding_rate < -0.05:
            funding_signal = "overleveraged_short"
            interpretation += ". WARNING: negative funding, short squeeze risk"
        else:
            funding_signal = "neutral"
    else:
        funding_signal = "unknown"

    # Leverage risk score (0-100)
    leverage_risk = _calculate_leverage_risk(oi_data, funding, symbol)

    return {
        "oi_regime": oi_regime,
        "funding_signal": funding_signal,
        "leverage_risk": leverage_risk,
        "interpretation": interpretation,
    }


def _calculate_leverage_risk(oi_data: dict, funding: dict, symbol: str) -> float:
    """Calculate leverage risk score 0-100."""
    risk = 50.0  # baseline

    # OI change magnitude
    oi_change = oi_data.get("oi_change_24h")
    if oi_change is not None:
        if abs(oi_change) > 20:
            risk += 25
        elif abs(oi_change) > 10:
            risk += 15
        elif abs(oi_change) > 5:
            risk += 5

    # Funding rate extremity
    rate = funding.get("current_rate")
    if rate is not None:
        if abs(rate) > 0.1:
            risk += 25
        elif abs(rate) > 0.05:
            risk += 15
        elif abs(rate) > 0.02:
            risk += 5
        else:
            risk -= 10

    # Liquidation volume
    try:
        liqs = get_liquidations(symbol, hours=24)
        total = liqs.get("total_liq") or 0
        if total > 500_000_000:
            risk += 15
        elif total > 100_000_000:
            risk += 10
        elif total > 10_000_000:
            risk += 5
    except Exception:
        pass

    return max(0, min(100, risk))


def get_market_structure_summary(symbol: str) -> dict:
    """Get comprehensive market structure summary combining OI + funding + liquidations.

    Returns:
        {
            "oi_regime": str,
            "funding_signal": str,
            "leverage_risk": float,
            "interpretation": str,
            "oi_data": dict,
            "funding_data": dict,
            "ls_ratio": dict,
            "liquidation_summary": dict,
        }
    """
    oi_data = get_open_interest(symbol)
    funding = get_funding_rates(symbol)
    ls_ratio = get_long_short_ratio(symbol)
    liqs = get_liquidations(symbol, hours=24)

    # Analyze regime (use 0 for prices since we're doing overview)
    regime = analyze_oi_regime(symbol, 0, 0)

    return {
        "oi_regime": regime["oi_regime"],
        "funding_signal": regime["funding_signal"],
        "leverage_risk": regime["leverage_risk"],
        "interpretation": regime["interpretation"],
        "oi_data": oi_data,
        "funding_data": funding,
        "ls_ratio": ls_ratio,
        "liquidation_summary": {
            "total_24h": liqs.get("total_liq"),
            "long_liq": liqs.get("long_liq_usd"),
            "short_liq": liqs.get("short_liq_usd"),
            "largest_single": liqs.get("largest_single"),
        },
    }
