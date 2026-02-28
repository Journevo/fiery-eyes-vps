"""Check 4: Sybil Risk Score — fresh wallet % + sybil scoring.
   Also provides wallet quality scoring for downstream engines."""

from config import HELIUS_RPC_URL, HELIUS_API_KEY, GATE_MAX_SYBIL_SCORE, GATE_MIN_WALLET_QUALITY, get_logger
from quality_gate.helpers import get_json, post_json
import time

log = get_logger("gate.sybil")

HELIUS_API_BASE = "https://api.helius.xyz/v0"


# ---------------------------------------------------------------------------
# Wallet quality scoring tiers (used by engines + quality-adjusted metrics)
# ---------------------------------------------------------------------------

def score_sol_balance(sol: float) -> int:
    """Score wallet by SOL balance. Higher = more legitimate."""
    if sol < 0.01:
        return 0
    if sol < 0.1:
        return 25
    if sol < 1.0:
        return 50
    if sol < 10.0:
        return 75
    return 100


def score_wallet_age(age_days: float | None) -> int:
    """Score wallet by age in days. Higher = more established."""
    if age_days is None:
        return 10  # unknown treated as suspicious
    if age_days < 1:
        return 10
    if age_days < 7:
        return 30
    if age_days < 30:
        return 60
    if age_days < 90:
        return 80
    return 100


def score_tx_diversity(unique_token_count: int) -> int:
    """Score wallet by number of distinct tokens interacted with."""
    if unique_token_count <= 1:
        return 20
    if unique_token_count <= 5:
        return 50
    if unique_token_count <= 20:
        return 80
    return 100


def wallet_quality_score(sol_balance: float, age_days: float | None,
                         unique_tokens: int = 1) -> int:
    """Composite wallet quality 0-100. Equal weight across three factors."""
    bal = score_sol_balance(sol_balance)
    age = score_wallet_age(age_days)
    div = score_tx_diversity(unique_tokens)
    return round((bal + age + div) / 3)


def quality_adjusted_holder_count(raw_count: int, avg_quality: float) -> int:
    """Quality-Adjusted Holder Count = raw_holders × (avg_wallet_quality / 100)."""
    return max(1, round(raw_count * (avg_quality / 100)))


# ---------------------------------------------------------------------------
# Wallet data fetching
# ---------------------------------------------------------------------------

def _get_token_holders_list(mint: str, limit: int = 100) -> list[str]:
    """Get a sample of holder wallet addresses for a token."""
    resp = post_json(HELIUS_RPC_URL, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint],
    })
    accounts = resp.get("result", {}).get("value", [])
    return [acc["address"] for acc in accounts[:limit]]


def _get_unique_token_count(wallet: str) -> int:
    """Get count of distinct token accounts for a wallet (proxy for tx diversity)."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                wallet,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        })
        accounts = resp.get("result", {}).get("value", [])
        return len(accounts)
    except Exception:
        return 1  # fallback: assume single token


def _check_wallet_age_and_balance(wallets: list[str]) -> list[dict]:
    """Check wallet ages, SOL balances, and token diversity.
    Sample up to 20 wallets to stay within rate limits."""
    sample = wallets[:20]
    results = []

    for wallet in sample:
        try:
            # Get SOL balance
            bal_resp = post_json(HELIUS_RPC_URL, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [wallet],
            })
            sol_balance = (bal_resp.get("result", {}).get("value", 0) or 0) / 1e9

            # Get first transaction to estimate wallet age
            sig_resp = post_json(HELIUS_RPC_URL, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getSignaturesForAddress",
                "params": [wallet, {"limit": 1, "before": None}],
            })
            sigs = sig_resp.get("result", [])
            age_days = None
            if sigs:
                block_time = sigs[-1].get("blockTime")
                if block_time:
                    age_days = (time.time() - block_time) / 86400

            # Token diversity (rate-limit friendly: only for first 10 wallets)
            unique_tokens = 1
            if len(results) < 10:
                unique_tokens = _get_unique_token_count(wallet)

            quality = wallet_quality_score(sol_balance, age_days, unique_tokens)

            results.append({
                "address": wallet,
                "sol_balance": sol_balance,
                "age_days": age_days,
                "unique_tokens": unique_tokens,
                "quality_score": quality,
                "is_fresh": age_days is not None and age_days < 7,
                "is_low_balance": sol_balance < 0.05,
            })
        except Exception as e:
            log.debug("Skipping wallet %s: %s", wallet[:8], e)

    return results


def score_wallets(mint: str) -> dict:
    """Full wallet quality analysis for a token. Returns quality metrics
    usable by both the sybil gate check and downstream engines.

    Returns:
        {
            "wallets_sampled": int,
            "wallet_details": list[dict],
            "avg_quality": float,
            "quality_adjusted_holders": int,
            "fresh_wallet_pct": float,
            "low_balance_pct": float,
            "sybil_score": int,
        }
    """
    wallets = _get_token_holders_list(mint)
    if not wallets:
        return {
            "wallets_sampled": 0,
            "wallet_details": [],
            "avg_quality": 0,
            "quality_adjusted_holders": 0,
            "fresh_wallet_pct": 0,
            "low_balance_pct": 0,
            "sybil_score": 100,
        }

    wallet_info = _check_wallet_age_and_balance(wallets)
    if not wallet_info:
        return {
            "wallets_sampled": 0,
            "wallet_details": [],
            "avg_quality": 0,
            "quality_adjusted_holders": 0,
            "fresh_wallet_pct": 0,
            "low_balance_pct": 0,
            "sybil_score": 100,
        }

    n = len(wallet_info)
    fresh_count = sum(1 for w in wallet_info if w["is_fresh"])
    low_bal_count = sum(1 for w in wallet_info if w["is_low_balance"])
    fresh_pct = (fresh_count / n) * 100
    low_bal_pct = (low_bal_count / n) * 100

    # Sybil score: both fresh AND low balance is strongest signal
    sybil_both = sum(1 for w in wallet_info if w["is_fresh"] and w["is_low_balance"])
    sybil_both_pct = (sybil_both / n) * 100
    sybil_score = min(100, int(sybil_both_pct * 0.7 + fresh_pct * 0.2 + low_bal_pct * 0.1))

    avg_quality = sum(w["quality_score"] for w in wallet_info) / n

    # Estimate total holders as len(wallets) since we only see top N
    raw_count = len(wallets)
    qa_holders = quality_adjusted_holder_count(raw_count, avg_quality)

    return {
        "wallets_sampled": n,
        "wallet_details": wallet_info,
        "avg_quality": round(avg_quality, 1),
        "quality_adjusted_holders": qa_holders,
        "fresh_wallet_pct": round(fresh_pct, 1),
        "low_balance_pct": round(low_bal_pct, 1),
        "sybil_score": sybil_score,
    }


# ---------------------------------------------------------------------------
# Quality Gate check (backward-compatible interface)
# ---------------------------------------------------------------------------

def check(mint: str) -> dict:
    """
    Returns:
        {
            "pass": bool,
            "sybil_score": int,
            "fresh_wallet_pct": float,
            "low_balance_pct": float,
            "wallets_sampled": int,
            "avg_quality": float,
            "quality_adjusted_holders": int,
            "reason": str | None
        }
    """
    result = {
        "pass": False,
        "sybil_score": 0,
        "fresh_wallet_pct": 0,
        "low_balance_pct": 0,
        "wallets_sampled": 0,
        "avg_quality": 0,
        "quality_adjusted_holders": 0,
        "reason": None,
    }

    try:
        scores = score_wallets(mint)
        result["wallets_sampled"] = scores["wallets_sampled"]
        result["fresh_wallet_pct"] = scores["fresh_wallet_pct"]
        result["low_balance_pct"] = scores["low_balance_pct"]
        result["sybil_score"] = scores["sybil_score"]
        result["avg_quality"] = scores["avg_quality"]
        result["quality_adjusted_holders"] = scores["quality_adjusted_holders"]

        if scores["wallets_sampled"] == 0:
            result["reason"] = "No holder wallets found"
            return result

        if result["sybil_score"] > GATE_MAX_SYBIL_SCORE:
            result["reason"] = f"Sybil score {result['sybil_score']} exceeds {GATE_MAX_SYBIL_SCORE}"
            log.info("Sybil FAIL for %s: %s", mint, result["reason"])
        else:
            result["pass"] = True
            log.info("Sybil PASS for %s (score %d, quality %.0f)",
                     mint, result["sybil_score"], result["avg_quality"])

        # Quality warning (does NOT affect pass/fail — informational for downstream)
        if result["avg_quality"] < GATE_MIN_WALLET_QUALITY:
            result["quality_warning"] = (
                f"Low wallet quality ({result['avg_quality']:.0f} < {GATE_MIN_WALLET_QUALITY})"
            )
            log.info("Quality warning for %s: %s", mint, result["quality_warning"])

    except Exception as e:
        log.error("Sybil check failed for %s: %s", mint, e)
        result["reason"] = f"Sybil check error: {e}"

    return result
