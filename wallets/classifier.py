"""Wallet Quality Classifier — classifies wallets as INDEPENDENT / INSIDER / WATCH.

Utility function, NOT a gate check. Uses existing sybil scoring and cluster
detection to classify a single wallet address.

Classifications (in priority order):
  INSIDER:      tracked KOL wallet, OR part of detected cluster,
                OR quality > 80 + diversity > 20 tokens
  INDEPENDENT:  quality > 60, age > 30 days, not clustered, SOL > 0.5
  WATCH:        everything else
"""

import time

from config import HELIUS_RPC_URL, get_logger
from db.connection import execute_one
from quality_gate.helpers import post_json
from quality_gate.sybil import wallet_quality_score

log = get_logger("wallets.classifier")


def _get_sol_balance(address: str) -> float:
    """Get SOL balance for a wallet."""
    resp = post_json(HELIUS_RPC_URL, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [address],
    })
    lamports = resp.get("result", {}).get("value", 0) or 0
    return lamports / 1e9


def _get_wallet_age_days(address: str) -> float | None:
    """Get wallet age in days from oldest signature."""
    resp = post_json(HELIUS_RPC_URL, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getSignaturesForAddress",
        "params": [address, {"limit": 1}],
    })
    sigs = resp.get("result", [])
    if sigs:
        block_time = sigs[-1].get("blockTime")
        if block_time:
            return (time.time() - block_time) / 86400
    return None


def _get_unique_token_count(address: str) -> int:
    """Get count of distinct token accounts for a wallet."""
    resp = post_json(HELIUS_RPC_URL, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    })
    accounts = resp.get("result", {}).get("value", [])
    return len(accounts)


def _is_kol_wallet(address: str) -> bool:
    """Check if address is a tracked KOL wallet."""
    try:
        row = execute_one(
            "SELECT 1 FROM kol_wallets WHERE wallet_address = %s", (address,))
        return row is not None
    except Exception:
        return False


def _is_cluster_member(address: str) -> bool:
    """Check if address appears in any detected cluster via funding source analysis."""
    try:
        from collectors.cluster import _get_funding_sources
        sources = _get_funding_sources(address)
        # If wallet has multiple funding sources or shares funders, it's suspicious
        # Simple heuristic: if funded by same source as other known wallets
        return len(sources) >= 3
    except Exception:
        return False


def classify_wallet(address: str) -> dict:
    """Classify a wallet as INDEPENDENT, INSIDER, or WATCH.

    Returns:
        {
            "classification": str,   # INDEPENDENT | INSIDER | WATCH
            "quality_score": int,    # 0-100
            "age_days": float | None,
            "sol_balance": float,
            "unique_tokens": int,
            "is_kol": bool,
            "cluster_member": bool,
            "reason": str,
        }
    """
    result = {
        "classification": "WATCH",
        "quality_score": 0,
        "age_days": None,
        "sol_balance": 0.0,
        "unique_tokens": 0,
        "is_kol": False,
        "cluster_member": False,
        "reason": "Default classification",
    }

    try:
        # Collect data
        sol_balance = _get_sol_balance(address)
        age_days = _get_wallet_age_days(address)
        unique_tokens = _get_unique_token_count(address)
        quality = wallet_quality_score(sol_balance, age_days, unique_tokens)
        is_kol = _is_kol_wallet(address)
        cluster_member = _is_cluster_member(address)

        result["quality_score"] = quality
        result["age_days"] = age_days
        result["sol_balance"] = sol_balance
        result["unique_tokens"] = unique_tokens
        result["is_kol"] = is_kol
        result["cluster_member"] = cluster_member

        # Classification logic (priority order)

        # 1. INSIDER: KOL wallet, clustered, or high-quality + high-diversity
        if is_kol:
            result["classification"] = "INSIDER"
            result["reason"] = "Tracked KOL wallet"
        elif cluster_member:
            result["classification"] = "INSIDER"
            result["reason"] = "Part of detected wallet cluster"
        elif quality > 80 and unique_tokens > 20:
            result["classification"] = "INSIDER"
            result["reason"] = f"High quality ({quality}) + high diversity ({unique_tokens} tokens)"

        # 2. INDEPENDENT: good quality, aged, not clustered, has SOL
        elif quality > 60 and age_days is not None and age_days > 30 and not cluster_member and sol_balance > 0.5:
            result["classification"] = "INDEPENDENT"
            result["reason"] = f"Quality {quality}, age {age_days:.0f}d, {sol_balance:.1f} SOL"

        # 3. WATCH: everything else
        else:
            reasons = []
            if quality <= 60:
                reasons.append(f"low quality ({quality})")
            if age_days is not None and age_days <= 30:
                reasons.append(f"young ({age_days:.0f}d)")
            elif age_days is None:
                reasons.append("unknown age")
            if sol_balance <= 0.5:
                reasons.append(f"low SOL ({sol_balance:.2f})")
            result["reason"] = "Watch: " + ", ".join(reasons) if reasons else "Insufficient criteria for higher classification"

        log.info("Classified %s as %s (quality=%d, age=%s, tokens=%d): %s",
                 address[:12], result["classification"], quality,
                 f"{age_days:.0f}d" if age_days else "?", unique_tokens,
                 result["reason"])

    except Exception as e:
        log.error("Classification failed for %s: %s", address[:12], e)
        result["reason"] = f"Classification error: {e}"

    return result


# --- Self-test ---

if __name__ == "__main__":
    import sys

    address = sys.argv[1] if len(sys.argv) > 1 else "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
    print(f"Classifying wallet {address}...")
    r = classify_wallet(address)
    for k, v in r.items():
        print(f"  {k}: {v}")
    print(f"\nClassification: {r['classification']}")
