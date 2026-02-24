"""Price Trend Signal — 20 points.

Source: DexScreener API.

Sub-scores:
  - Trend structure (higher lows): /7
  - Current vs 4h ago:             /5
  - Current vs 24h ago:            /5
  - Drawdown from 24h high:        /3
"""

from config import get_logger
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("health_score.price")

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{address}"


def score_price(token_address: str) -> tuple[float, str, dict]:
    """Score price trend for a token.

    Returns: (score: float /20, data_state: str, details: dict)
    """
    details = {}

    try:
        data = get_json(DEXSCREENER_API.format(address=token_address))
        record_api_call("dexscreener", True)
    except Exception as e:
        log.error("DexScreener API failed for price: %s", e)
        record_api_call("dexscreener", False)
        return 0.0, 'missing', {'error': str(e)}

    pairs = data.get("pairs", [])
    if not pairs:
        return 0.0, 'missing', {'error': 'no pairs found'}

    pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

    price_usd = float(pair.get("priceUsd", 0) or 0)
    mcap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)

    price_change = pair.get("priceChange", {})
    change_h1 = float(price_change.get("h1", 0) or 0)
    change_h6 = float(price_change.get("h6", 0) or 0)
    change_h24 = float(price_change.get("h24", 0) or 0)
    change_m5 = float(price_change.get("m5", 0) or 0)

    details['price_usd'] = price_usd
    details['mcap'] = mcap
    details['change_h1'] = change_h1
    details['change_h6'] = change_h6
    details['change_h24'] = change_h24

    # Sub-score 1: Trend structure — higher lows (/7)
    # Use price change progression to infer: m5, h1, h6, h24
    # Higher lows = each shorter timeframe shows improvement from its low
    # Simplified: count how many timeframes show positive or improving trend
    trend_points = 0
    # h1 > 0 means recent uptick
    if change_h1 > 0:
        trend_points += 1
    # h6 > 0 means 6h trend is up
    if change_h6 > 0:
        trend_points += 1
    # h1 > h6 improvement means accelerating (higher lows forming)
    if change_h1 > change_h6 and change_h1 > 0:
        trend_points += 1
    # m5 positive = micro-trend bullish
    if change_m5 > 0:
        trend_points += 1

    if trend_points >= 3:
        sub1 = 7  # strong higher lows
    elif trend_points == 2:
        sub1 = 5  # moderate trend
    elif trend_points == 1:
        sub1 = 3  # choppy but some up
    elif change_h6 > -5:
        sub1 = 2  # flat
    else:
        sub1 = 0  # lower lows

    details['trend_points'] = trend_points
    details['sub1_trend_structure'] = sub1

    # Sub-score 2: Current vs ~4h ago (/5)
    # Use h6 change as proxy (DexScreener doesn't have exact 4h)
    # Adjusted: h6 change gives us 6h view
    if change_h6 > 20:
        sub2 = 5
    elif change_h6 > 5:
        sub2 = 4
    elif change_h6 > -5:
        sub2 = 2
    elif change_h6 > -15:
        sub2 = 1
    else:
        sub2 = 0

    details['sub2_vs_4h'] = sub2

    # Sub-score 3: Current vs 24h ago (/5)
    if change_h24 > 100:
        sub3 = 5
    elif change_h24 > 20:
        sub3 = 4
    elif change_h24 > 0:
        sub3 = 2
    elif change_h24 > -20:
        sub3 = 1
    else:
        sub3 = 0

    details['sub3_vs_24h'] = sub3

    # Sub-score 4: Drawdown from 24h high (/3)
    # Estimate: if h24 change was e.g. +50% but h1 is -10%, drawdown ~= h24_high - current
    # Without exact high, use: if h24 > 0 and h1 < 0, drawdown = abs(h1)
    # Better proxy: max of (h1, h6, h24) minus current
    peak_change = max(change_h1, change_h6, change_h24)
    if peak_change > change_h1:
        drawdown_est = peak_change - change_h1
    else:
        drawdown_est = 0

    if drawdown_est < 10:
        sub4 = 3  # within 10% of high
    elif drawdown_est < 25:
        sub4 = 2
    elif drawdown_est < 50:
        sub4 = 1
    else:
        sub4 = 0

    details['drawdown_est_pct'] = round(drawdown_est, 1)
    details['sub4_drawdown'] = sub4

    total_score = sub1 + sub2 + sub3 + sub4
    total_score = round(min(20, max(0, total_score)), 1)

    # Data state
    if price_usd == 0:
        data_state = 'missing'
    elif change_h1 == 0 and change_h6 == 0 and change_h24 == 0:
        data_state = 'stale'
    else:
        data_state = 'live'

    return total_score, data_state, details
