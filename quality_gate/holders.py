"""Check 3: Holder Distribution — top 10 concentration + cluster detection."""

from config import HELIUS_RPC_URL, GATE_MAX_TOP10_PCT, get_logger
from quality_gate.helpers import post_json

log = get_logger("gate.holders")


def _get_token_supply(mint: str) -> float | None:
    """Get actual total supply via RPC."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint],
        })
        value = resp.get("result", {}).get("value", {})
        return float(value.get("uiAmount", 0) or 0)
    except Exception as e:
        log.warning("getTokenSupply failed for %s: %s", mint, e)
        return None


def _get_top_holders(mint: str, limit: int = 20) -> list[dict]:
    """Fetch largest token holders via Helius RPC."""
    resp = post_json(HELIUS_RPC_URL, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint],
    })
    accounts = resp.get("result", {}).get("value", [])
    holders = []
    for acc in accounts[:limit]:
        holders.append({
            "address": acc.get("address", ""),
            "amount": float(acc.get("uiAmount", 0) or 0),
        })
    return holders


def _detect_clusters(holders: list[dict]) -> list[list[str]]:
    """
    Basic cluster detection stub.
    Phase 2: check if wallets were funded from the same source within 24h.
    """
    return []


def check(mint: str) -> dict:
    """
    Returns:
        {
            "pass": bool,
            "top10_pct": float,
            "total_supply": float,
            "clusters_found": int,
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "top10_pct": None,
        "total_supply": 0,
        "clusters_found": 0,
        "reason": None,
    }

    try:
        holders = _get_top_holders(mint)
        if not holders:
            result["reason"] = "No holder data returned"
            return result

        # Get actual total supply (not just sum of top 20)
        total_supply = _get_token_supply(mint)
        if not total_supply or total_supply == 0:
            # Fallback: use sum of top 20 (less accurate)
            total_supply = sum(h["amount"] for h in holders)
            log.warning("Using top-20 sum as supply fallback for %s", mint)

        if total_supply == 0:
            result["reason"] = "Zero supply in holder data"
            return result

        result["total_supply"] = total_supply

        top10_amount = sum(h["amount"] for h in holders[:10])
        top10_pct = (top10_amount / total_supply) * 100
        result["top10_pct"] = round(top10_pct, 2)

        clusters = _detect_clusters(holders)
        result["clusters_found"] = len(clusters)

        if top10_pct > GATE_MAX_TOP10_PCT:
            result["reason"] = f"Top 10 hold {top10_pct:.1f}% (limit {GATE_MAX_TOP10_PCT}%)"
            log.info("Holders FAIL for %s: %s", mint, result["reason"])
        else:
            result["pass"] = True
            log.info("Holders PASS for %s (top10 %.1f%%)", mint, top10_pct)

    except Exception as e:
        log.error("Holder check failed for %s: %s", mint, e)
        result["reason"] = f"Holder API error: {e}"

    return result
