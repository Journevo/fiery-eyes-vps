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


def _detect_clusters_for_mint(mint: str) -> list[list[str]]:
    """Detect wallet clusters using cached snapshot or live cluster detection.

    1. Check snapshot: if holders_raw > holders_quality_adjusted, clusters exist
    2. Fall back to collectors.cluster.detect_clusters() for full analysis
    """
    # Check cached snapshot first
    try:
        from db.connection import execute_one
        row = execute_one(
            """SELECT s.holders_raw, s.holders_quality_adjusted
               FROM snapshots_daily s
               JOIN tokens t ON t.id = s.token_id
               WHERE t.contract_address = %s
               ORDER BY s.date DESC LIMIT 1""",
            (mint,),
        )
        if row and row[0] and row[1] and row[0] > row[1]:
            gap = row[0] - row[1]
            log.info("Snapshot shows %d clustered wallets for %s", gap, mint[:16])
            # Return a synthetic cluster to indicate clusters were detected
            return [["snapshot_inferred"]] * min(gap, 5)
    except Exception as e:
        log.debug("Snapshot cluster check failed: %s", e)

    # Fall back to live cluster detection
    try:
        from collectors.cluster import detect_clusters
        result = detect_clusters(mint)
        return result.get("clusters", [])
    except Exception as e:
        log.debug("Live cluster detection failed: %s", e)
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

        clusters = _detect_clusters_for_mint(mint)
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
