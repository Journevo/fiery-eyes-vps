"""Check 6: Wash Trading Detection — unique traders vs transaction count."""

from config import HELIUS_RPC_URL, GATE_MAX_WASH_SCORE, get_logger
from quality_gate.helpers import post_json

log = get_logger("gate.wash_trading")

# System programs to exclude from trader counts
SYSTEM_PROGRAMS = {
    "11111111111111111111111111111111",
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    "ComputeBudget111111111111111111111111111111",
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
}


def _get_recent_signatures(mint: str, limit: int = 100) -> list[dict]:
    """Fetch recent transaction signatures for a token mint via RPC."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [mint, {"limit": limit}],
        })
        return resp.get("result", [])
    except Exception as e:
        log.error("Failed to fetch signatures for %s: %s", mint, e)
        return []


def _get_transaction_signers(signatures: list[dict], sample_size: int = 30) -> list[str]:
    """Get the fee payer (signer) for a sample of transactions."""
    signers = []
    sigs_to_check = [s["signature"] for s in signatures[:sample_size] if not s.get("err")]

    if not sigs_to_check:
        return signers

    # Batch fetch transactions
    for sig in sigs_to_check:
        try:
            resp = post_json(HELIUS_RPC_URL, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            })
            tx = resp.get("result")
            if tx:
                account_keys = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
                if account_keys:
                    # First signer is the fee payer
                    first = account_keys[0]
                    if isinstance(first, dict):
                        signer = first.get("pubkey", "")
                    else:
                        signer = str(first)
                    if signer and signer not in SYSTEM_PROGRAMS:
                        signers.append(signer)
        except Exception as e:
            log.debug("Failed to parse tx %s: %s", sig[:16], e)

    return signers


def check(mint: str) -> dict:
    """
    Compare unique traders vs transaction count.
    If avg txns/trader >20 in the sample, flag as wash trading.

    Returns:
        {
            "pass": bool,
            "wash_score": int,
            "unique_traders": int,
            "total_txns": int,
            "avg_txns_per_trader": float,
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "wash_score": 0,
        "unique_traders": 0,
        "total_txns": 0,
        "avg_txns_per_trader": 0,
        "reason": None,
    }

    try:
        sigs = _get_recent_signatures(mint)
        if not sigs:
            result["pass"] = True
            result["reason"] = "No transaction data available (pass by default)"
            log.info("Wash trading PASS for %s (no data)", mint)
            return result

        result["total_txns"] = len(sigs)

        # Get signers from a sample of transactions
        signers = _get_transaction_signers(sigs)
        if not signers:
            result["pass"] = True
            result["reason"] = "Could not identify traders"
            return result

        unique_traders = set(signers)
        result["unique_traders"] = len(unique_traders)

        avg_per_trader = len(signers) / len(unique_traders)
        result["avg_txns_per_trader"] = round(avg_per_trader, 1)

        # Wash score: 0-100 based on txn concentration
        if avg_per_trader <= 5:
            wash_score = 0
        elif avg_per_trader <= 10:
            wash_score = int((avg_per_trader - 5) * 6)
        elif avg_per_trader <= 20:
            wash_score = 30 + int((avg_per_trader - 10) * 4)
        else:
            wash_score = min(70 + int((avg_per_trader - 20) * 1.5), 100)

        result["wash_score"] = wash_score

        if wash_score > GATE_MAX_WASH_SCORE:
            result["reason"] = f"Wash score {wash_score} (avg {avg_per_trader:.1f} txns/trader) exceeds {GATE_MAX_WASH_SCORE}"
            log.info("Wash trading FAIL for %s: %s", mint, result["reason"])
        else:
            result["pass"] = True
            log.info("Wash trading PASS for %s (score %d, avg %.1f txns/trader)",
                     mint, wash_score, avg_per_trader)

    except Exception as e:
        log.error("Wash trading check failed for %s: %s", mint, e)
        result["reason"] = f"Wash trading check error: {e}"

    return result
