"""Smart Money Wallet Tracker — monitor curated wallets for exposure changes.

System:
  - Track 30 initial wallets (manually curated)
  - Monitor net exposure changes via Helius
  - Reputation system: start at 50
    +5 profitable hold >48h
    -15 dump into retail
    -2/month inactivity
  - Remove wallets below reputation 20
  - Signal weight proportional to reputation score
  - Store in wallet_reputation table
"""

import time
from datetime import datetime, timezone, timedelta
from config import HELIUS_RPC_URL, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import post_json

log = get_logger("wallets.tracker")

# Initial curated wallets — add known smart money addresses here
# These are placeholders; replace with actual curated wallet addresses
INITIAL_WALLETS: list[str] = [
    # STUB: populate with 30 curated smart money Solana wallet addresses
    # Example: "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU",
]

# Reputation thresholds
STARTING_REPUTATION = 50.0
MIN_REPUTATION = 20.0
PROFITABLE_HOLD_BONUS = 5.0
DUMP_PENALTY = -15.0
INACTIVITY_PENALTY_MONTHLY = -2.0


# ---------------------------------------------------------------------------
# Wallet initialization
# ---------------------------------------------------------------------------

def initialize_wallets(wallet_addresses: list[str] | None = None):
    """Add wallets to the tracking table if not already present."""
    wallets = wallet_addresses or INITIAL_WALLETS
    if not wallets:
        log.warning("STUB: No wallet addresses configured. "
                    "Add addresses to INITIAL_WALLETS or pass them as argument.")
        return

    added = 0
    for addr in wallets:
        try:
            existing = execute_one(
                "SELECT wallet_address FROM wallet_reputation WHERE wallet_address = %s",
                (addr,),
            )
            if not existing:
                execute(
                    """INSERT INTO wallet_reputation
                       (wallet_address, reputation_score, tracked_since)
                       VALUES (%s, %s, NOW())""",
                    (addr, STARTING_REPUTATION),
                )
                added += 1
        except Exception as e:
            log.error("Failed to add wallet %s: %s", addr[:8], e)

    log.info("Initialized %d new wallets for tracking (of %d total)", added, len(wallets))


# ---------------------------------------------------------------------------
# Token holding analysis
# ---------------------------------------------------------------------------

def _get_wallet_token_accounts(wallet: str) -> list[dict]:
    """Get all SPL token accounts for a wallet."""
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
        holdings = []
        for acc in accounts:
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            mint = info.get("mint", "")
            amount = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0)
            if amount > 0 and mint:
                holdings.append({"mint": mint, "amount": amount})
        return holdings
    except Exception as e:
        log.debug("Failed to get token accounts for %s: %s", wallet[:8], e)
        return []


def _get_wallet_sol_balance(wallet: str) -> float:
    """Get SOL balance for a wallet."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [wallet],
        })
        lamports = resp.get("result", {}).get("value", 0) or 0
        return lamports / 1e9
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Net exposure calculation
# ---------------------------------------------------------------------------

def get_wallet_exposure(wallet: str) -> dict:
    """Get current net exposure for a wallet.

    Returns:
        {
            "wallet": str,
            "sol_balance": float,
            "token_count": int,
            "holdings": list[dict],  # [{mint, amount}]
        }
    """
    sol = _get_wallet_sol_balance(wallet)
    holdings = _get_wallet_token_accounts(wallet)

    return {
        "wallet": wallet,
        "sol_balance": sol,
        "token_count": len(holdings),
        "holdings": holdings,
    }


def get_smart_money_signal(mint: str) -> dict:
    """Calculate smart money signal for a specific token.

    Checks how many tracked wallets hold this token, weighted by reputation.

    Returns:
        {
            "mint": str,
            "holders_tracked": int,    # number of smart money wallets holding
            "total_tracked": int,      # total tracked wallets
            "weighted_signal": float,  # 0-100 reputation-weighted
            "net_exposure": str,       # "increasing", "stable", "decreasing"
        }
    """
    try:
        wallets = execute(
            """SELECT wallet_address, reputation_score
               FROM wallet_reputation
               WHERE reputation_score >= %s""",
            (MIN_REPUTATION,),
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query tracked wallets: %s", e)
        return {
            "mint": mint, "holders_tracked": 0, "total_tracked": 0,
            "weighted_signal": 0, "net_exposure": "unknown",
        }

    if not wallets:
        return {
            "mint": mint, "holders_tracked": 0, "total_tracked": 0,
            "weighted_signal": 0, "net_exposure": "unknown",
        }

    holders = 0
    total_rep_weight = 0
    holder_rep_weight = 0

    for wallet_addr, rep_score in wallets:
        total_rep_weight += rep_score
        holdings = _get_wallet_token_accounts(wallet_addr)
        holding_mints = {h["mint"] for h in holdings}
        if mint in holding_mints:
            holders += 1
            holder_rep_weight += rep_score

    weighted_signal = (holder_rep_weight / total_rep_weight * 100) if total_rep_weight > 0 else 0

    return {
        "mint": mint,
        "holders_tracked": holders,
        "total_tracked": len(wallets),
        "weighted_signal": round(weighted_signal, 1),
        "net_exposure": "stable",  # STUB: would need historical comparison
    }


# ---------------------------------------------------------------------------
# Reputation management
# ---------------------------------------------------------------------------

def update_reputation(wallet: str, event: str, details: str = ""):
    """Update wallet reputation based on observed behavior.

    Events:
      "profitable_hold" — held position profitably for >48h
      "dump"            — sold into retail (large sell detected)
      "inactivity"      — monthly inactivity penalty
    """
    delta = 0
    if event == "profitable_hold":
        delta = PROFITABLE_HOLD_BONUS
    elif event == "dump":
        delta = DUMP_PENALTY
    elif event == "inactivity":
        delta = INACTIVITY_PENALTY_MONTHLY
    else:
        log.warning("Unknown reputation event: %s", event)
        return

    try:
        execute(
            """UPDATE wallet_reputation SET
                 reputation_score = GREATEST(0, LEAST(100, reputation_score + %s)),
                 total_entries = total_entries + 1
               WHERE wallet_address = %s""",
            (delta, wallet),
        )
        log.info("Reputation update for %s: %s (%+.0f)", wallet[:8], event, delta)

        # Check if wallet should be removed
        row = execute_one(
            "SELECT reputation_score FROM wallet_reputation WHERE wallet_address = %s",
            (wallet,),
        )
        if row and row[0] < MIN_REPUTATION:
            log.info("Wallet %s dropped below minimum reputation (%.0f). "
                     "No longer contributing to signals.", wallet[:8], row[0])

    except Exception as e:
        log.error("Failed to update reputation for %s: %s", wallet[:8], e)


def apply_inactivity_penalties():
    """Apply monthly inactivity penalty to wallets that haven't had entries."""
    try:
        # Wallets with no entries in last 30 days
        execute(
            """UPDATE wallet_reputation SET
                 reputation_score = GREATEST(0, reputation_score + %s)
               WHERE total_entries = 0
                 OR tracked_since < NOW() - INTERVAL '30 days'""",
            (INACTIVITY_PENALTY_MONTHLY,),
        )
        log.info("Applied inactivity penalties")
    except Exception as e:
        log.error("Failed to apply inactivity penalties: %s", e)


def get_tracked_wallets_summary() -> dict:
    """Get summary of all tracked wallets."""
    try:
        rows = execute(
            """SELECT wallet_address, reputation_score, total_entries,
                      positive_entries, tracked_since
               FROM wallet_reputation
               ORDER BY reputation_score DESC""",
            fetch=True,
        )
        if not rows:
            return {"total": 0, "active": 0, "avg_reputation": 0, "wallets": []}

        wallets = []
        for addr, rep, total, positive, since in rows:
            wallets.append({
                "address": addr[:8] + "...",
                "reputation": rep,
                "entries": total,
                "positive": positive,
                "tracked_since": since.isoformat() if since else None,
            })

        active = sum(1 for w in wallets if w["reputation"] >= MIN_REPUTATION)
        avg_rep = sum(w["reputation"] for w in wallets) / len(wallets) if wallets else 0

        return {
            "total": len(wallets),
            "active": active,
            "avg_reputation": round(avg_rep, 1),
            "wallets": wallets,
        }
    except Exception as e:
        log.error("Failed to get wallet summary: %s", e)
        return {"total": 0, "active": 0, "avg_reputation": 0, "wallets": []}
