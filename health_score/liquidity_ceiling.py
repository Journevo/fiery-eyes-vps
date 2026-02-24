"""Liquidity Ceiling — affects position sizing, not scoring.

Returns mcap/liquidity ratio and restrictions.
"""

from config import get_logger
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("health_score.liquidity")

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{address}"


def get_liquidity_ceiling(token_address: str) -> dict:
    """Assess liquidity ceiling for position sizing.

    Returns:
        {
            'ratio': float,          # mcap/liquidity
            'restriction': str,      # 'none', 'no_add', 'trim_only', 'exit_what_you_can'
            'lp_direction': str,     # 'growing', 'static', 'draining'
            'slippage_estimate_pct': float,  # estimated slippage for $1K sell
            'mcap': float,
            'liquidity_usd': float,
        }
    """
    default = {
        'ratio': 0,
        'restriction': 'none',
        'lp_direction': 'static',
        'slippage_estimate_pct': 0,
        'mcap': 0,
        'liquidity_usd': 0,
    }

    try:
        data = get_json(DEXSCREENER_API.format(address=token_address))
        record_api_call("dexscreener", True)
    except Exception as e:
        log.error("DexScreener API failed for liquidity: %s", e)
        record_api_call("dexscreener", False)
        return default

    pairs = data.get("pairs", [])
    if not pairs:
        return default

    pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

    mcap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
    liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

    if liquidity_usd <= 0:
        return {**default, 'mcap': mcap, 'restriction': 'exit_what_you_can'}

    ratio = mcap / liquidity_usd

    # Restriction based on ratio
    if ratio < 10:
        restriction = 'none'
    elif ratio < 20:
        restriction = 'no_add'
    elif ratio < 40:
        restriction = 'trim_only'
    else:
        restriction = 'exit_what_you_can'

    # LP direction: compare volume trends as proxy
    # Without historical LP data, use volume trend as indicator
    vol_h24 = float(pair.get("volume", {}).get("h24", 0) or 0)
    vol_h6 = float(pair.get("volume", {}).get("h6", 0) or 0)

    # If recent volume is higher proportion than expected, LP likely growing
    if vol_h24 > 0:
        h6_ratio = vol_h6 / vol_h24
        if h6_ratio > 0.35:  # 6h is >35% of 24h = accelerating = LP likely growing
            lp_direction = 'growing'
        elif h6_ratio > 0.20:
            lp_direction = 'static'
        else:
            lp_direction = 'draining'
    else:
        lp_direction = 'static'

    # Slippage estimate for $1K sell
    # Rough model: slippage ~= (trade_size / liquidity) * constant_impact_factor
    # For AMM: slippage ~= 2 * (amount / liquidity_depth)
    if liquidity_usd > 0:
        slippage_estimate = (1000 / liquidity_usd) * 100 * 2
        slippage_estimate = round(min(100, slippage_estimate), 2)
    else:
        slippage_estimate = 100.0

    return {
        'ratio': round(ratio, 2),
        'restriction': restriction,
        'lp_direction': lp_direction,
        'slippage_estimate_pct': slippage_estimate,
        'mcap': mcap,
        'liquidity_usd': liquidity_usd,
    }
