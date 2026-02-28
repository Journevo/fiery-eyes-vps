"""Holder Quality Signal — 10 points.

3 sub-scores: Concentration(/4) + Wallet Quality(/4) + Cluster Risk(/2).
Prefers cached snapshot data from snapshots_daily to avoid expensive Helius RPC calls.
"""

from config import get_logger
from db.connection import execute_one

log = get_logger("health_score.holders")


def _get_cached_snapshot(token_address: str) -> dict | None:
    """Fetch today's snapshot data for the token (avoids Helius RPC)."""
    try:
        row = execute_one(
            """SELECT s.holders_raw, s.holders_quality_adjusted,
                      s.top10_pct, s.sybil_risk_score
               FROM snapshots_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE t.contract_address = %s
               ORDER BY s.date DESC LIMIT 1""",
            (token_address,),
        )
        if row:
            return {
                "holders_raw": row[0],
                "holders_quality_adjusted": row[1],
                "top10_pct": row[2],
                "sybil_risk_score": row[3],
            }
    except Exception as e:
        log.debug("Snapshot lookup failed for %s: %s", token_address[:16], e)
    return None


def _score_concentration(top10_pct: float | None) -> tuple[float, bool]:
    """Sub-score: holder concentration /4. Lower top10% = better."""
    if top10_pct is None:
        return 0.0, False
    if top10_pct < 20:
        return 4.0, True
    if top10_pct < 30:
        return 3.0, True
    if top10_pct < 40:
        return 2.0, True
    if top10_pct < 50:
        return 1.0, True
    return 0.0, True


def _score_wallet_quality(snapshot: dict | None, token_address: str) -> tuple[float, float | None, bool]:
    """Sub-score: wallet quality /4. Returns (score, avg_quality, has_data)."""
    avg_quality = None
    sybil_score = None

    # Try snapshot first
    if snapshot and snapshot.get("sybil_risk_score") is not None:
        sybil_score = snapshot["sybil_risk_score"]
        avg_quality = max(0, 100 - sybil_score)
    else:
        # Fallback to live sybil check
        try:
            from quality_gate.sybil import score_wallets
            wallet_data = score_wallets(token_address)
            avg_quality = wallet_data.get("avg_quality")
            sybil_score = wallet_data.get("sybil_score")
        except Exception as e:
            log.debug("Sybil fallback failed for %s: %s", token_address[:16], e)
            return 0.0, None, False

    if avg_quality is None:
        return 0.0, None, False

    # Score based on avg wallet quality
    if avg_quality > 70:
        score = 4.0
    elif avg_quality > 50:
        score = 3.0
    elif avg_quality > 30:
        score = 2.0
    elif avg_quality > 15:
        score = 1.0
    else:
        score = 0.0

    # Halve if sybil score is high
    if sybil_score is not None and sybil_score > 70:
        score = score * 0.5

    return score, avg_quality, True


def _score_cluster_risk(snapshot: dict | None) -> tuple[float, bool]:
    """Sub-score: cluster risk /2. Compares raw vs quality-adjusted holders."""
    if snapshot is None:
        return 0.0, False

    raw = snapshot.get("holders_raw")
    adjusted = snapshot.get("holders_quality_adjusted")

    if raw is None or adjusted is None or raw == 0:
        return 0.0, False

    gap_pct = ((raw - adjusted) / raw) * 100

    if gap_pct <= 10:
        return 2.0, True   # no meaningful gap
    if gap_pct <= 30:
        return 1.0, True   # small gap
    return 0.0, True       # large gap — likely clusters


def score_holders(token_address: str) -> tuple[float, str, dict]:
    """Score holder quality for a token.

    Returns: (score: float /10, data_state: str, details: dict)
    """
    details = {}

    # Get cached snapshot data (avoids Helius RPC)
    snapshot = _get_cached_snapshot(token_address)
    top10_pct = snapshot["top10_pct"] if snapshot else None

    # If no snapshot, fall back to holders.check() for concentration
    if top10_pct is None:
        try:
            from quality_gate.holders import check
            holder_data = check(token_address)
            top10_pct = holder_data.get("top10_pct")
        except Exception as e:
            log.debug("Holder check fallback failed: %s", e)

    # Sub-score 1: Concentration (/4)
    conc_score, conc_has_data = _score_concentration(top10_pct)
    details["top10_pct"] = top10_pct
    details["concentration_score"] = conc_score

    # Sub-score 2: Wallet Quality (/4)
    quality_score, avg_quality, quality_has_data = _score_wallet_quality(snapshot, token_address)
    details["avg_quality"] = avg_quality
    details["wallet_quality_score"] = quality_score

    # Sub-score 3: Cluster Risk (/2)
    cluster_score, cluster_has_data = _score_cluster_risk(snapshot)
    details["cluster_risk_score"] = cluster_score

    raw_score = conc_score + quality_score + cluster_score

    # Data state: based on how many sub-scores have data
    data_count = sum([conc_has_data, quality_has_data, cluster_has_data])
    if data_count >= 2:
        data_state = "live"
    elif data_count == 1:
        data_state = "stale"
    else:
        data_state = "missing"

    details["data_sources"] = data_count

    log.debug("Holder score for %s: %.1f/10 (%s) — conc=%.1f qual=%.1f cluster=%.1f",
              token_address[:16], raw_score, data_state,
              conc_score, quality_score, cluster_score)

    return round(raw_score, 1), data_state, details
