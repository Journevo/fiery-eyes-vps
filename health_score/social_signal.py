"""Social Momentum Signal — 20 points. LOGGED ONLY in phase 1, not scored.

Uses existing social/pulse.py if available, otherwise stub.
Always returns data_state='missing' until Grok API is connected.
Logs raw data to health_scores table for later analysis.
"""

from config import get_logger

log = get_logger("health_score.social")


def score_social(token_address: str, token_symbol: str | None = None) -> tuple[float, str, dict]:
    """Score social momentum for a token.

    Phase 1: LOGGED ONLY. Returns data_state='missing' always.
    The score is calculated but not included in the active health score.

    Returns: (score: float /20, data_state: str, details: dict)
    """
    details = {'phase': 1, 'note': 'logged_only_not_scored'}

    # Try to get data from existing social pulse
    raw_score = 0.0
    try:
        from social.pulse import calculate_pulse
        if token_symbol:
            pulse = calculate_pulse(token_symbol, mint=token_address)
            pulse_score = pulse.get('pulse_score', 0)
            # Scale 0-100 pulse to 0-20 health signal
            raw_score = pulse_score / 100 * 20
            details['pulse_score'] = pulse_score
            details['cross_platform'] = pulse.get('cross_platform_count', 0)
            details['high_conviction'] = pulse.get('high_conviction', False)
            details['platform_scores'] = pulse.get('platform_scores', {})
            log.debug("Social pulse for %s: %.0f/100 -> %.1f/20",
                       token_symbol, pulse_score, raw_score)
    except Exception as e:
        log.debug("Social pulse unavailable: %s", e)
        details['error'] = str(e)

    # Always return 'missing' in phase 1 — data is logged but not used in scoring
    return round(raw_score, 1), 'missing', details
