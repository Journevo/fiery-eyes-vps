"""Check 5: Unlock Overhang — vesting/unlock schedule risk."""

from config import GATE_MAX_UNLOCK_VOLUME_RATIO, get_logger

log = get_logger("gate.unlocks")


def check(mint: str, category: str = "meme", manual_unlock_usd: float = 0, avg_30d_volume: float = 0) -> dict:
    """
    For non-meme tokens: check if upcoming unlocks >10% of 30d avg volume.

    Phase 1: stub that accepts manual input. Phase 2 will pull vesting
    schedules from on-chain programs and APIs.

    Args:
        mint: token contract address
        category: 'meme', 'adoption', or 'infra'
        manual_unlock_usd: manually provided unlock amount in USD (next 30 days)
        avg_30d_volume: average daily volume over last 30 days in USD

    Returns:
        {
            "pass": bool,
            "unlock_to_volume_ratio": float | None,
            "unlock_usd": float,
            "avg_volume": float,
            "skipped": bool,
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "unlock_to_volume_ratio": None,
        "unlock_usd": manual_unlock_usd,
        "avg_volume": avg_30d_volume,
        "skipped": False,
        "reason": None,
    }

    # Meme tokens typically don't have vesting — auto-pass
    if category == "meme":
        result["pass"] = True
        result["skipped"] = True
        result["reason"] = "Meme token — no unlock schedule expected"
        log.info("Unlock PASS for %s (meme, skipped)", mint)
        return result

    if manual_unlock_usd == 0:
        result["pass"] = True
        result["skipped"] = True
        result["reason"] = "No unlock data provided (stubbed for Phase 1)"
        log.info("Unlock PASS for %s (no data, stubbed)", mint)
        return result

    if avg_30d_volume <= 0:
        result["reason"] = "Cannot assess — avg_30d_volume is zero"
        return result

    ratio = manual_unlock_usd / (avg_30d_volume * 30)
    result["unlock_to_volume_ratio"] = round(ratio, 2)

    if ratio > GATE_MAX_UNLOCK_VOLUME_RATIO:
        result["reason"] = f"Unlock/volume ratio {ratio:.1f}x exceeds {GATE_MAX_UNLOCK_VOLUME_RATIO}x limit"
        log.info("Unlock FAIL for %s: %s", mint, result["reason"])
    else:
        result["pass"] = True
        log.info("Unlock PASS for %s (ratio %.2fx)", mint, ratio)

    return result
