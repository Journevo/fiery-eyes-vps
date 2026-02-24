"""Check 7: Minimum Age/Volume — token must be >2h old and >$50K volume."""

from datetime import datetime, timezone
from config import GATE_MIN_AGE_HOURS, GATE_MIN_VOLUME_USD, get_logger
from quality_gate.helpers import get_json

log = get_logger("gate.age_volume")

DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{address}"


def check(mint: str) -> dict:
    """
    Use DexScreener API to check token age and cumulative volume.

    Returns:
        {
            "pass": bool,
            "age_hours": float | None,
            "volume_usd": float | None,
            "pair_address": str | None,
            "dex": str | None,
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "age_hours": None,
        "volume_usd": None,
        "pair_address": None,
        "dex": None,
        "reason": None,
    }

    try:
        data = get_json(DEXSCREENER_URL.format(address=mint))
        pairs = data.get("pairs") or []

        if not pairs:
            result["reason"] = "No trading pairs found on DexScreener"
            log.info("Age/Volume FAIL for %s: no pairs", mint)
            return result

        # Use the pair with highest volume
        pairs_sorted = sorted(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0), reverse=True)
        pair = pairs_sorted[0]

        result["pair_address"] = pair.get("pairAddress", "")
        result["dex"] = pair.get("dexId", "unknown")

        # Age: pairCreatedAt is millisecond timestamp
        created_at_ms = pair.get("pairCreatedAt")
        if created_at_ms:
            created_at = datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            age_hours = (now - created_at).total_seconds() / 3600
            result["age_hours"] = round(age_hours, 1)
        else:
            result["age_hours"] = None

        # Volume: 24h volume in USD
        volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
        result["volume_usd"] = volume_24h

        # Check age
        if result["age_hours"] is not None and result["age_hours"] < GATE_MIN_AGE_HOURS:
            result["reason"] = f"Token is {result['age_hours']:.1f}h old (minimum {GATE_MIN_AGE_HOURS}h)"
            log.info("Age/Volume FAIL for %s: %s", mint, result["reason"])
            return result

        # Check volume
        if volume_24h < GATE_MIN_VOLUME_USD:
            result["reason"] = f"Volume ${volume_24h:,.0f} below ${GATE_MIN_VOLUME_USD:,.0f} minimum"
            log.info("Age/Volume FAIL for %s: %s", mint, result["reason"])
            return result

        result["pass"] = True
        log.info("Age/Volume PASS for %s (age %.1fh, vol $%.0f)", mint,
                 result["age_hours"] or 0, volume_24h)

    except Exception as e:
        log.error("Age/Volume check failed for %s: %s", mint, e)
        result["reason"] = f"DexScreener API error: {e}"

    return result
