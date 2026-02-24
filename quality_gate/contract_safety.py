"""Check 1: Contract Safety — RugCheck + Token-2022 audit."""

from config import HELIUS_RPC_URL, get_logger
from quality_gate.helpers import get_json, post_json

log = get_logger("gate.contract_safety")

RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"

# Dangerous Token-2022 extensions
DANGEROUS_EXTENSIONS = {"PermanentDelegate", "TransferHook", "ConfidentialTransfers"}


def check(mint: str) -> dict:
    """
    Returns:
        {
            "pass": bool,
            "mint_authority_renounced": bool,
            "freeze_authority_renounced": bool,
            "lp_status": str,
            "dangerous_extensions": list[str],
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "mint_authority_renounced": False,
        "freeze_authority_renounced": False,
        "lp_status": "unknown",
        "dangerous_extensions": [],
        "reason": None,
    }

    # --- RugCheck API ---
    try:
        rc = get_json(RUGCHECK_URL.format(mint=mint))
        risks = rc.get("risks", [])
        risk_names = [r.get("name", "") for r in risks]

        result["mint_authority_renounced"] = "Mint Authority still enabled" not in risk_names
        result["freeze_authority_renounced"] = "Freeze Authority still enabled" not in risk_names

        # LP status: look for LP info in markets
        markets = rc.get("markets", [])
        if markets:
            lp_info = markets[0].get("lp", {})
            lp_locked_pct = lp_info.get("lpLockedPct", 0)
            if lp_locked_pct > 90:
                result["lp_status"] = "locked"
            elif lp_info.get("lpBurned", False):
                result["lp_status"] = "burned"
            else:
                result["lp_status"] = f"unlocked ({lp_locked_pct:.0f}% locked)"

        if not result["mint_authority_renounced"]:
            result["reason"] = "Mint authority NOT renounced"
            return result
        if not result["freeze_authority_renounced"]:
            result["reason"] = "Freeze authority NOT renounced"
            return result
        if result["lp_status"].startswith("unlocked"):
            result["reason"] = f"LP {result['lp_status']}"
            return result

    except Exception as e:
        log.error("RugCheck API failed for %s: %s", mint, e)
        result["reason"] = f"RugCheck API error: {e}"
        return result

    # --- Token-2022 extension check via Helius DAS ---
    try:
        das_resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAsset",
            "params": {"id": mint},
        })
        asset = das_resp.get("result", {})
        extensions = set()
        # Check mint extensions from content metadata
        mint_exts = asset.get("mint_extensions", {})
        if mint_exts:
            extensions = set(mint_exts.keys())

        dangerous_found = extensions & DANGEROUS_EXTENSIONS
        result["dangerous_extensions"] = list(dangerous_found)

        if dangerous_found:
            result["reason"] = f"Dangerous Token-2022 extensions: {', '.join(dangerous_found)}"
            return result

    except Exception as e:
        log.warning("Helius DAS getAsset failed for %s: %s (non-fatal)", mint, e)

    result["pass"] = True
    log.info("Contract safety PASS for %s", mint)
    return result
