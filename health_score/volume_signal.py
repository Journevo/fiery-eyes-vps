"""Volume Momentum Signal — 30 points.

Source: DexScreener API.

Sub-scores:
  - 4h volume vs previous 4h: /10
  - Volume/MCap ratio daily:  /10
  - Buy vol vs Sell vol:      /10
  - WASH ADJUSTMENT: if unique makers <20% of txn count -> halve total
"""

from config import get_logger
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("health_score.volume")

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/tokens/{address}"


def score_volume(token_address: str) -> tuple[float, str, dict]:
    """Score volume momentum for a token.

    Returns: (score: float /30, data_state: str, details: dict)
    """
    details = {}

    try:
        data = get_json(DEXSCREENER_API.format(address=token_address))
        record_api_call("dexscreener", True)
    except Exception as e:
        log.error("DexScreener API failed for volume: %s", e)
        record_api_call("dexscreener", False)
        return 0.0, 'missing', {'error': str(e)}

    pairs = data.get("pairs", [])
    if not pairs:
        return 0.0, 'missing', {'error': 'no pairs found'}

    # Use highest-liquidity pair
    pair = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))

    vol_h24 = float(pair.get("volume", {}).get("h24", 0) or 0)
    vol_h6 = float(pair.get("volume", {}).get("h6", 0) or 0)
    vol_h1 = float(pair.get("volume", {}).get("h1", 0) or 0)
    mcap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
    liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)

    txns = pair.get("txns", {})
    buys_h24 = int(txns.get("h24", {}).get("buys", 0) or 0)
    sells_h24 = int(txns.get("h24", {}).get("sells", 0) or 0)
    buys_h6 = int(txns.get("h6", {}).get("buys", 0) or 0)
    sells_h6 = int(txns.get("h6", {}).get("sells", 0) or 0)

    details['vol_h24'] = vol_h24
    details['vol_h6'] = vol_h6
    details['vol_h1'] = vol_h1
    details['mcap'] = mcap
    details['liquidity_usd'] = liquidity_usd

    # Sub-score 1: 4h volume vs previous 4h (/10)
    # Estimate: vol_h6 covers ~6h. Split as current 4h ~= vol_h6 * 2/3
    # previous 4h ~= (vol_h24 - vol_h6) * (4/18)
    # Simplified: compare h6 rate to remaining rate
    if vol_h24 > 0:
        remaining_vol = max(0, vol_h24 - vol_h6)
        remaining_hours = 18  # 24 - 6
        h6_rate = vol_h6 / 6 if vol_h6 > 0 else 0
        remaining_rate = remaining_vol / remaining_hours if remaining_hours > 0 else 0

        if remaining_rate > 0:
            vol_growth = h6_rate / remaining_rate
        else:
            vol_growth = 2.0 if h6_rate > 0 else 1.0

        if vol_growth > 1.5:
            sub1 = 10
        elif vol_growth > 1.1:
            sub1 = 7
        elif vol_growth > 0.8:
            sub1 = 4
        elif vol_growth > 0.5:
            sub1 = 2
        else:
            sub1 = 0
    else:
        sub1 = 0
        vol_growth = 0

    details['vol_growth_ratio'] = round(vol_growth, 2)
    details['sub1_volume_momentum'] = sub1

    # Sub-score 2: Volume/MCap ratio daily (/10)
    if mcap > 0:
        vol_mcap_ratio = vol_h24 / mcap
        if vol_mcap_ratio > 0.5:
            sub2 = 10
        elif vol_mcap_ratio > 0.2:
            sub2 = 7
        elif vol_mcap_ratio > 0.05:
            sub2 = 4
        else:
            sub2 = 1
    else:
        vol_mcap_ratio = 0
        sub2 = 0

    details['vol_mcap_ratio'] = round(vol_mcap_ratio, 4)
    details['sub2_vol_mcap'] = sub2

    # Sub-score 3: Buy vol vs Sell vol (/10)
    total_h6_txns = buys_h6 + sells_h6
    if total_h6_txns > 0:
        buy_ratio = buys_h6 / sells_h6 if sells_h6 > 0 else 2.0
        if buy_ratio > 1.5:
            sub3 = 10
        elif buy_ratio > 1.2:
            sub3 = 7
        elif buy_ratio > 0.8:
            sub3 = 4
        else:
            sub3 = 0
    else:
        buy_ratio = 1.0
        sub3 = 4  # no data, neutral

    details['buy_sell_ratio'] = round(buy_ratio, 2)
    details['sub3_buy_pressure'] = sub3

    total_score = sub1 + sub2 + sub3

    # WASH ADJUSTMENT: check unique makers vs txn count
    # DexScreener doesn't provide unique makers directly.
    # Use ratio of buys+sells: if very high txn count with low volume,
    # likely wash. Proxy: if avg txn size < $10, suspicious.
    total_txns_24h = buys_h24 + sells_h24
    if total_txns_24h > 0 and vol_h24 > 0:
        avg_txn_size = vol_h24 / total_txns_24h
        if avg_txn_size < 10:
            total_score = total_score / 2
            details['wash_adjustment'] = True
            details['avg_txn_size'] = round(avg_txn_size, 2)
        else:
            details['wash_adjustment'] = False
            details['avg_txn_size'] = round(avg_txn_size, 2)

    total_score = round(min(30, max(0, total_score)), 1)

    # Determine data state
    data_state = 'live'
    pair_updated = pair.get("pairCreatedAt")
    if not vol_h24 and not vol_h6:
        data_state = 'missing'
    elif vol_h1 == 0 and vol_h6 == 0:
        data_state = 'stale'

    return total_score, data_state, details
