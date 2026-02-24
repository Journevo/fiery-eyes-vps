"""Health Score v2 Engine — Core scoring for Fiery Golden Eyes.

Scores tokens /100 based on 3 core signals (phase 1) or 5 (phase 2+).

PHASE 1 (launch): Volume(30) + Price(20) + KOL(20) = /70, scaled to /100
PHASE 2 (earned): + Social(20) + Holders(10) = /100

Every score includes a Data Confidence percentage.
Low confidence = auto-actions disabled.
"""

from datetime import datetime, timezone
from config import get_logger
from db.connection import execute

log = get_logger("health_score.engine")

# Phase 1 active signals (phase 2 adds 'social' and 'holders')
ACTIVE_SIGNALS = ['volume', 'price', 'kol']

SIGNAL_WEIGHTS = {
    'volume': 30,
    'price': 20,
    'kol': 20,
    'social': 20,
    'holders': 10,
}


def score_token(token_address: str, token_symbol: str | None = None) -> dict:
    """Master scoring function. Calls all signal scorers and assembles health score.

    Returns dict matching health_scores table columns.
    """
    log.info("Scoring token %s (%s)", token_address[:16], token_symbol or "?")

    from health_score.volume_signal import score_volume
    from health_score.price_signal import score_price
    from health_score.kol_signal import score_kol
    from health_score.social_signal import score_social
    from health_score.holder_signal import score_holders
    from health_score.liquidity_ceiling import get_liquidity_ceiling

    # Score all signals
    vol_score, vol_state, vol_details = score_volume(token_address)
    price_score, price_state, price_details = score_price(token_address)
    kol_score, kol_state, kol_details = score_kol(token_address)
    social_score, social_state, social_details = score_social(token_address, token_symbol)
    holder_score, holder_state, holder_details = score_holders(token_address)

    signal_states = {
        'volume': vol_state,
        'price': price_state,
        'kol': kol_state,
        'social': social_state,
        'holders': holder_state,
    }

    # Calculate raw score (only active signals)
    raw_score = vol_score + price_score + kol_score
    max_possible = sum(SIGNAL_WEIGHTS[s] for s in ACTIVE_SIGNALS)  # 70 in phase 1

    # Scaled score: (raw/max)*100
    scaled_score = (raw_score / max_possible * 100) if max_possible > 0 else 0
    scaled_score = round(min(100, max(0, scaled_score)), 1)

    # Data confidence
    confidence = get_data_confidence(signal_states)

    # Get regime state
    regime_state = _get_regime_state()

    # KOL exit flag
    kol_exit = kol_details.get('kol_exit', False)

    # Recommended action
    recommended_action = get_recommended_action(scaled_score, confidence, regime_state, kol_exit)

    # Auto-action allowed if confidence >60% and no _untrusted suffix
    auto_action_enabled = confidence >= 60 and not recommended_action.endswith('_untrusted')

    # Token tier from mcap
    mcap = vol_details.get('mcap') or price_details.get('mcap') or 0
    token_tier = classify_token_tier(mcap)

    # Liquidity ceiling
    liq = get_liquidity_ceiling(token_address)

    result = {
        'token_address': token_address,
        'token_symbol': token_symbol,
        'scored_at': datetime.now(timezone.utc),
        'volume_score': round(vol_score, 1),
        'price_score': round(price_score, 1),
        'kol_score': round(kol_score, 1),
        'social_score': round(social_score, 1),
        'holder_score': round(holder_score, 1),
        'raw_score': round(raw_score, 1),
        'max_possible': max_possible,
        'scaled_score': scaled_score,
        'volume_data_state': vol_state,
        'price_data_state': price_state,
        'kol_data_state': kol_state,
        'social_data_state': social_state,
        'holder_data_state': holder_state,
        'confidence_pct': round(confidence, 1),
        'token_tier': token_tier,
        'regime_state': regime_state,
        'liquidity_ratio': liq.get('ratio'),
        'lp_direction': liq.get('lp_direction'),
        'recommended_action': recommended_action,
        'auto_action_enabled': auto_action_enabled,
        # Extra details not in DB but useful for callers
        '_details': {
            'volume': vol_details,
            'price': price_details,
            'kol': kol_details,
            'social': social_details,
            'holders': holder_details,
            'liquidity': liq,
        },
    }

    # Save to DB
    _save_health_score(result)

    # Update token record
    _update_token_health(token_address, scaled_score, confidence,
                         recommended_action, token_tier)

    log.info("Health Score for %s: %.1f/100 (conf %.0f%%, action=%s)",
             token_symbol or token_address[:12], scaled_score, confidence,
             recommended_action)

    return result


def get_data_confidence(signal_states: dict) -> float:
    """Calculate data confidence percentage (0-100).

    Each signal has a weight. 'live' = full weight, 'stale' = half, 'missing' = zero.
    Only counts ACTIVE signals (phase 1: volume + price + kol).
    """
    total_weight = 0
    achieved_weight = 0

    for signal in ACTIVE_SIGNALS:
        weight = SIGNAL_WEIGHTS.get(signal, 0)
        state = signal_states.get(signal, 'missing')
        total_weight += weight

        if state == 'live':
            achieved_weight += weight
        elif state == 'stale':
            achieved_weight += weight * 0.5
        # 'missing' contributes zero

    if total_weight == 0:
        return 0.0

    return round((achieved_weight / total_weight) * 100, 1)


def get_recommended_action(scaled_score: float, confidence: float,
                           regime_state: str, kol_exit: bool = False) -> str:
    """Determine recommended action based on health score and context.

    Score thresholds:
        80-100: 'add'
        65-79:  'hold'
        50-64:  'cooling'
        35-49:  'trim'
        20-34:  'exit'
        <20:    'dead'

    Overrides:
        - kol_exit=True -> 'exit' regardless
        - confidence <60% -> append '_untrusted'
        - regime 'risk_off' -> shift thresholds down 10pts
    """
    # KOL exit override
    if kol_exit:
        action = 'exit'
        if confidence < 60:
            return action + '_untrusted'
        return action

    # Apply regime adjustment
    offset = 0
    if regime_state == 'risk_off':
        offset = -10  # shift down = more tolerant, prevents panic selling

    score = scaled_score

    if score >= (80 + offset):
        action = 'add'
    elif score >= (65 + offset):
        action = 'hold'
    elif score >= (50 + offset):
        action = 'cooling'
    elif score >= (35 + offset):
        action = 'trim'
    elif score >= (20 + offset):
        action = 'exit'
    else:
        action = 'dead'

    if confidence < 60:
        action = action + '_untrusted'

    return action


def classify_token_tier(mcap: float) -> str:
    """Classify token tier by market cap.

    <5M:   'hatchling' (monitor every 15min)
    5M-50M: 'runner' (monitor every 30min)
    50M+:  'established' (monitor every 4h)
    """
    if mcap < 5_000_000:
        return 'hatchling'
    elif mcap < 50_000_000:
        return 'runner'
    else:
        return 'established'


def _get_regime_state() -> str:
    """Get current regime state from v2 regime module, fall back to old multiplier."""
    try:
        from regime.multiplier import get_regime_state
        regime = get_regime_state()
        return regime.get('state', 'neutral')
    except (ImportError, AttributeError):
        try:
            from regime.multiplier import get_current_regime
            regime = get_current_regime()
            if regime:
                mult = regime.get('regime_multiplier', 0.7)
                if mult >= 0.8:
                    return 'risk_on'
                elif mult >= 0.5:
                    return 'neutral'
                else:
                    return 'risk_off'
        except Exception:
            pass
    return 'neutral'


def _save_health_score(result: dict):
    """Persist health score to database."""
    try:
        execute(
            """INSERT INTO health_scores
               (token_address, token_symbol, scored_at,
                volume_score, price_score, kol_score, social_score, holder_score,
                raw_score, max_possible, scaled_score,
                volume_data_state, price_data_state, kol_data_state,
                social_data_state, holder_data_state,
                confidence_pct, token_tier, regime_state,
                liquidity_ratio, lp_direction,
                recommended_action, auto_action_enabled)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (token_address, scored_at) DO UPDATE SET
                 scaled_score = EXCLUDED.scaled_score,
                 confidence_pct = EXCLUDED.confidence_pct,
                 recommended_action = EXCLUDED.recommended_action""",
            (result['token_address'], result['token_symbol'], result['scored_at'],
             result['volume_score'], result['price_score'], result['kol_score'],
             result['social_score'], result['holder_score'],
             result['raw_score'], result['max_possible'], result['scaled_score'],
             result['volume_data_state'], result['price_data_state'],
             result['kol_data_state'], result['social_data_state'],
             result['holder_data_state'],
             result['confidence_pct'], result['token_tier'], result['regime_state'],
             result['liquidity_ratio'], result['lp_direction'],
             result['recommended_action'], result['auto_action_enabled']),
        )
    except Exception as e:
        log.error("Failed to save health score: %s", e)


def _update_token_health(token_address: str, score: float, confidence: float,
                         action: str, tier: str):
    """Update token record with latest health data."""
    try:
        interval = {'hatchling': 15, 'runner': 30, 'established': 240}.get(tier, 15)
        execute(
            """UPDATE tokens SET
                 last_health_score = %s,
                 last_health_confidence = %s,
                 health_state = %s,
                 token_tier = %s,
                 monitoring_interval_min = %s,
                 updated_at = NOW()
               WHERE contract_address = %s""",
            (score, confidence, action, tier, interval, token_address),
        )
    except Exception as e:
        log.error("Failed to update token health: %s", e)
