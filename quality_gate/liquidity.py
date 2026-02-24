"""Check 2: Liquidity Depth — Jupiter quote API slippage check."""

from config import GATE_MAX_SLIPPAGE_PCT, get_logger
from quality_gate.helpers import get_json

log = get_logger("gate.liquidity")

# Jupiter lite API (free, no key required — deprecation postponed)
JUPITER_QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SWAP_AMOUNT_USDC = 10_000  # $10K


def check(mint: str) -> dict:
    """
    Simulate $10K USDC→token swap via Jupiter.
    Returns:
        {
            "pass": bool,
            "slippage_pct": float | None,
            "in_amount_usd": float,
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "slippage_pct": None,
        "in_amount_usd": SWAP_AMOUNT_USDC,
        "reason": None,
    }

    try:
        # USDC has 6 decimals
        amount_lamports = SWAP_AMOUNT_USDC * 10**6

        data = get_json(JUPITER_QUOTE_URL, params={
            "inputMint": USDC_MINT,
            "outputMint": mint,
            "amount": str(amount_lamports),
            "slippageBps": 1000,  # allow up to 10% to get a quote
        })

        # priceImpactPct comes as a string like "0.0041700917..." meaning ~0.004%
        try:
            slippage_pct = abs(float(data.get("priceImpactPct", "0")))
        except (ValueError, TypeError):
            slippage_pct = 0.0

        result["slippage_pct"] = round(slippage_pct, 4)

        if slippage_pct > GATE_MAX_SLIPPAGE_PCT:
            result["reason"] = f"Slippage {slippage_pct:.2f}% exceeds {GATE_MAX_SLIPPAGE_PCT}% limit"
            log.info("Liquidity FAIL for %s: %s", mint, result["reason"])
        else:
            result["pass"] = True
            log.info("Liquidity PASS for %s (slippage %.4f%%)", mint, slippage_pct)

    except Exception as e:
        log.error("Jupiter quote failed for %s: %s", mint, e)
        result["reason"] = f"Jupiter API error: {e}"

    return result
