"""Social Momentum Signal — 20 points.

3 sub-scores: Social Pulse(/12) + X Intelligence(/6) + Cross-platform(/2).
Integrates social/pulse.py and grok_poller x_intelligence data.
"""

from config import get_logger

log = get_logger("health_score.social")


def _score_pulse(token_address: str, token_symbol: str | None) -> tuple[float, dict | None]:
    """Sub-score: social pulse /12. Scale pulse 0-100 → 0-12."""
    try:
        from social.pulse import calculate_pulse
        if not token_symbol:
            return 0.0, None
        pulse = calculate_pulse(token_symbol, mint=token_address)
        pulse_score = pulse.get("pulse_score", 0)
        score = pulse_score / 100 * 12
        return round(score, 1), pulse
    except Exception as e:
        log.debug("Social pulse unavailable: %s", e)
        return 0.0, None


def _score_x_intelligence(token_address: str) -> tuple[float, dict | None]:
    """Sub-score: X intelligence /6. strong×2 + other×0.5, cap 6."""
    try:
        from social.grok_poller import get_x_intelligence_summary
        summary = get_x_intelligence_summary(token_address)
        signal_count = summary.get("signal_count", 0)
        strong_signals = summary.get("strong_signals", 0)

        if signal_count == 0:
            return 0.0, None

        other_signals = signal_count - strong_signals
        score = strong_signals * 2 + other_signals * 0.5
        score = min(6.0, score)
        return round(score, 1), summary
    except Exception as e:
        log.debug("X intelligence unavailable: %s", e)
        return 0.0, None


def _score_cross_platform(pulse_data: dict | None) -> float:
    """Sub-score: cross-platform presence /2."""
    if not pulse_data:
        return 0.0
    cross_count = pulse_data.get("cross_platform_count", 0)
    if cross_count >= 3:
        return 2.0
    if cross_count >= 2:
        return 1.0
    return 0.0


def score_social(token_address: str, token_symbol: str | None = None) -> tuple[float, str, dict]:
    """Score social momentum for a token.

    Returns: (score: float /20, data_state: str, details: dict)
    """
    details = {}

    # Sub-score 1: Social Pulse (/12)
    pulse_score, pulse_data = _score_pulse(token_address, token_symbol)
    pulse_has_data = pulse_data is not None
    details["pulse_score"] = pulse_data.get("pulse_score", 0) if pulse_data else 0
    details["social_pulse_sub"] = pulse_score

    # Sub-score 2: X Intelligence (/6)
    x_intel_score, x_intel_data = _score_x_intelligence(token_address)
    x_intel_has_data = x_intel_data is not None and x_intel_data.get("signal_count", 0) > 0
    details["x_intelligence"] = x_intel_data or {}
    details["x_intel_sub"] = x_intel_score

    # Sub-score 3: Cross-platform (/2)
    cross_score = _score_cross_platform(pulse_data)
    details["cross_platform"] = pulse_data.get("cross_platform_count", 0) if pulse_data else 0
    details["cross_platform_sub"] = cross_score

    # Extra details for consumers
    if pulse_data:
        details["high_conviction"] = pulse_data.get("high_conviction", False)
        details["platform_scores"] = pulse_data.get("platform_scores", {})

    raw_score = pulse_score + x_intel_score + cross_score

    # Data state
    if pulse_has_data and x_intel_has_data:
        data_state = "live"
    elif pulse_has_data or x_intel_has_data:
        data_state = "stale"
    else:
        data_state = "missing"

    log.debug("Social score for %s: %.1f/20 (%s) — pulse=%.1f xintel=%.1f cross=%.1f",
              token_symbol or token_address[:16], raw_score, data_state,
              pulse_score, x_intel_score, cross_score)

    return round(raw_score, 1), data_state, details
