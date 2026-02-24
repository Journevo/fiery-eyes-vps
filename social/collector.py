"""Master social data aggregator."""

from config import get_logger
from db.connection import execute
from social.pulse import calculate_pulse, get_platform_breakdown

log = get_logger("social.collector")


def collect_social_data(keyword: str, mint: str | None = None) -> dict:
    """Aggregate all social data for a keyword/token.

    Returns:
        {"keyword": str, "mint": str|None, "pulse": dict, "platform_breakdown": dict}
    """
    log.info("Collecting social data for '%s' (mint=%s)", keyword, mint)

    try:
        pulse = calculate_pulse(keyword, mint)
    except Exception as e:
        log.error("Pulse failed for '%s': %s", keyword, e)
        pulse = {"pulse_score": 0, "platform_scores": {},
                 "cross_platform_count": 0, "high_conviction": False,
                 "dominant_platform": "none", "trend_direction": "stable"}

    try:
        breakdown = get_platform_breakdown(keyword, mint)
    except Exception as e:
        log.error("Breakdown failed for '%s': %s", keyword, e)
        breakdown = {}

    return {
        "keyword": keyword,
        "mint": mint,
        "pulse": pulse,
        "platform_breakdown": breakdown,
    }


def collect_all_token_social() -> list[dict]:
    """Collect social data for all tracked tokens."""
    results = []
    try:
        rows = execute(
            "SELECT contract_address, symbol FROM tokens WHERE quality_gate_pass = TRUE",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query tokens: %s", e)
        return results

    if not rows:
        return results

    log.info("Collecting social for %d tokens", len(rows))
    for mint, symbol in rows:
        try:
            data = collect_social_data(symbol, mint)
            results.append(data)
        except Exception as e:
            log.error("Social collection failed for %s: %s", symbol, e)

    log.info("Social collection done: %d/%d", len(results), len(rows))
    return results


def get_social_summary(keyword: str, mint: str | None = None) -> str:
    """One-line social summary."""
    try:
        pulse = calculate_pulse(keyword, mint)
    except Exception:
        return f"{keyword} | Pulse unavailable"

    score = pulse.get("pulse_score", 0)
    cross = pulse.get("cross_platform_count", 0)
    direction = pulse.get("trend_direction", "stable")
    dominant = pulse.get("dominant_platform", "none")
    conviction = " | HIGH CONVICTION" if pulse.get("high_conviction") else ""

    return (f"{keyword} | Pulse {score:.0f}/100 | {cross} platforms "
            f"| {direction} | dominant: {dominant}{conviction}")
