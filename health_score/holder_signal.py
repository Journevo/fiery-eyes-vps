"""Holder Quality Signal — 10 points. LOGGED ONLY in phase 1, not scored.

Uses existing quality_gate/holders.py and sybil.py data if available.
Always returns data_state='missing' until validated.
Logs to health_scores table.
"""

from config import get_logger

log = get_logger("health_score.holders")


def score_holders(token_address: str) -> tuple[float, str, dict]:
    """Score holder quality for a token.

    Phase 1: LOGGED ONLY. Returns data_state='missing' always.
    The score is calculated but not included in the active health score.

    Returns: (score: float /10, data_state: str, details: dict)
    """
    details = {'phase': 1, 'note': 'logged_only_not_scored'}

    raw_score = 0.0

    # Try to use existing quality gate holder data
    try:
        from quality_gate.holders import check_holder_concentration
        holder_data = check_holder_concentration(token_address)
        top10_pct = holder_data.get('top10_pct', 50)
        details['top10_pct'] = top10_pct

        # Score based on concentration (lower = better)
        if top10_pct < 20:
            raw_score = 10
        elif top10_pct < 30:
            raw_score = 7
        elif top10_pct < 50:
            raw_score = 4
        else:
            raw_score = 1
    except Exception as e:
        log.debug("Holder concentration check failed: %s", e)

    # Try sybil data
    try:
        from quality_gate.sybil import estimate_sybil_risk
        sybil_data = estimate_sybil_risk(token_address)
        sybil_score = sybil_data.get('sybil_score', 50)
        quality_adjusted = sybil_data.get('quality_adjusted_holders', 0)
        details['sybil_score'] = sybil_score
        details['quality_adjusted_holders'] = quality_adjusted

        # Adjust holder score by sybil risk
        if sybil_score > 70:
            raw_score = raw_score * 0.5  # high sybil risk halves score
    except Exception as e:
        log.debug("Sybil check failed: %s", e)

    # Always return 'missing' in phase 1
    return round(raw_score, 1), 'missing', details
