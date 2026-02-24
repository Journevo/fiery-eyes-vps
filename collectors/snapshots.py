"""Daily snapshot collector — gathers price, volume, holders, distribution
   for tokens that passed Quality Gate. Stores in snapshots_daily table."""

from datetime import date, datetime, timezone
from config import HELIUS_RPC_URL, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json, post_json
from quality_gate.sybil import score_wallets

log = get_logger("collectors.snapshots")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _fetch_dexscreener_data(mint: str) -> dict | None:
    """Fetch price, mcap, volume, liquidity from DexScreener."""
    try:
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            log.warning("No DexScreener pairs for %s", mint)
            return None

        # Use highest-volume pair
        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        return {
            "price": float(best.get("priceUsd") or 0),
            "mcap": float(best.get("marketCap") or best.get("fdv") or 0),
            "volume_24h": float(best.get("volume", {}).get("h24", 0) or 0),
            "liquidity_usd": float(best.get("liquidity", {}).get("usd", 0) or 0),
            "pair_address": best.get("pairAddress", ""),
            "social_links": len(best.get("info", {}).get("socials", []) if best.get("info") else []),
        }
    except Exception as e:
        log.error("DexScreener fetch failed for %s: %s", mint, e)
        return None


def _fetch_holder_count(mint: str) -> int | None:
    """Get approximate holder count via Helius getTokenLargestAccounts.
    Free tier doesn't have full holder count — use largest accounts as proxy."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint],
        })
        accounts = resp.get("result", {}).get("value", [])
        return len(accounts)
    except Exception as e:
        log.error("Holder count fetch failed for %s: %s", mint, e)
        return None


def _fetch_holder_distribution(mint: str) -> dict:
    """Get top-10/top-50 concentration and Gini coefficient estimate."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint],
        })
        accounts = resp.get("result", {}).get("value", [])

        supply_resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenSupply",
            "params": [mint],
        })
        total_supply = float(
            supply_resp.get("result", {}).get("value", {}).get("uiAmount", 0) or 0
        )

        if not accounts or total_supply == 0:
            return {"top10_pct": None, "top50_pct": None, "gini": None}

        amounts = [float(a.get("uiAmount", 0) or 0) for a in accounts]
        top10 = sum(amounts[:10])
        top50 = sum(amounts[:min(50, len(amounts))])

        top10_pct = (top10 / total_supply) * 100
        top50_pct = (top50 / total_supply) * 100

        # Simplified Gini from available data
        gini = _estimate_gini(amounts, total_supply)

        return {
            "top10_pct": round(top10_pct, 2),
            "top50_pct": round(top50_pct, 2),
            "gini": round(gini, 4) if gini is not None else None,
        }
    except Exception as e:
        log.error("Distribution fetch failed for %s: %s", mint, e)
        return {"top10_pct": None, "top50_pct": None, "gini": None}


def _estimate_gini(amounts: list[float], total_supply: float) -> float | None:
    """Estimate Gini coefficient from top-holder amounts.
    0 = perfectly equal, 1 = perfectly concentrated."""
    if not amounts or total_supply == 0:
        return None
    sorted_a = sorted(amounts)
    n = len(sorted_a)
    if n < 2:
        return None
    cumulative = 0
    area_under = 0
    for i, a in enumerate(sorted_a):
        cumulative += a
        area_under += cumulative
    # Normalize
    area_under = area_under / (total_supply * n)
    gini = 1 - 2 * (1 - area_under)
    return max(0.0, min(1.0, gini))


def _fetch_median_wallet_balance(mint: str) -> float | None:
    """Median wallet balance from available top-holder data."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint],
        })
        accounts = resp.get("result", {}).get("value", [])
        if not accounts:
            return None
        amounts = sorted(float(a.get("uiAmount", 0) or 0) for a in accounts)
        mid = len(amounts) // 2
        if len(amounts) % 2 == 0:
            return (amounts[mid - 1] + amounts[mid]) / 2
        return amounts[mid]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Snapshot collection
# ---------------------------------------------------------------------------

def collect_snapshot(mint: str, token_id: int) -> dict | None:
    """Collect a full daily snapshot for a token and store in DB.

    Returns the snapshot dict or None on failure.
    """
    log.info("Collecting snapshot for %s (token_id=%d)", mint, token_id)

    # Fetch data from multiple sources
    dex = _fetch_dexscreener_data(mint)
    if not dex:
        log.warning("Skipping snapshot for %s — no DexScreener data", mint)
        return None

    holders_raw = _fetch_holder_count(mint)
    dist = _fetch_holder_distribution(mint)
    median_bal = _fetch_median_wallet_balance(mint)

    # Wallet quality scoring
    wallet_scores = score_wallets(mint)
    qa_holders = wallet_scores["quality_adjusted_holders"]
    avg_quality = wallet_scores["avg_quality"]
    sybil_score = wallet_scores["sybil_score"]
    fresh_pct = wallet_scores["fresh_wallet_pct"]

    today = date.today()

    snapshot = {
        "token_id": token_id,
        "date": today,
        "price": dex["price"],
        "mcap": dex["mcap"],
        "volume": dex["volume_24h"],
        "liquidity_depth_10k": dex["liquidity_usd"],  # total liquidity as proxy
        "holders_raw": holders_raw,
        "holders_quality_adjusted": qa_holders,
        "top10_pct": dist["top10_pct"],
        "top50_pct": dist["top50_pct"],
        "gini": dist["gini"],
        "median_wallet_balance": median_bal,
        "social_velocity": float(dex.get("social_links", 0)),
        "fresh_wallet_pct": fresh_pct,
        "sybil_risk_score": sybil_score,
        "smart_money_netflow": None,  # stub — no wallet tracker yet
    }

    # Upsert into snapshots_daily
    try:
        execute(
            """INSERT INTO snapshots_daily
               (token_id, date, price, mcap, volume,
                liquidity_depth_10k,
                holders_raw, holders_quality_adjusted,
                top10_pct, top50_pct, gini,
                median_wallet_balance, social_velocity,
                fresh_wallet_pct, sybil_risk_score, smart_money_netflow)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (token_id, date) DO UPDATE SET
                 price = EXCLUDED.price,
                 mcap = EXCLUDED.mcap,
                 volume = EXCLUDED.volume,
                 liquidity_depth_10k = EXCLUDED.liquidity_depth_10k,
                 holders_raw = EXCLUDED.holders_raw,
                 holders_quality_adjusted = EXCLUDED.holders_quality_adjusted,
                 top10_pct = EXCLUDED.top10_pct,
                 top50_pct = EXCLUDED.top50_pct,
                 gini = EXCLUDED.gini,
                 median_wallet_balance = EXCLUDED.median_wallet_balance,
                 social_velocity = EXCLUDED.social_velocity,
                 fresh_wallet_pct = EXCLUDED.fresh_wallet_pct,
                 sybil_risk_score = EXCLUDED.sybil_risk_score,
                 smart_money_netflow = EXCLUDED.smart_money_netflow""",
            (token_id, today, dex["price"], dex["mcap"], dex["volume_24h"],
             dex["liquidity_usd"],
             holders_raw, qa_holders,
             dist["top10_pct"], dist["top50_pct"], dist["gini"],
             median_bal, float(dex.get("social_links", 0)),
             fresh_pct, sybil_score, None),
        )
        log.info("Snapshot saved for %s on %s", mint, today)
    except Exception as e:
        log.error("Failed to save snapshot for %s: %s", mint, e)
        return None

    return snapshot


def collect_all_snapshots():
    """Collect daily snapshots for all tokens that passed Quality Gate."""
    log.info("=== Starting daily snapshot collection ===")

    try:
        rows = execute(
            """SELECT id, contract_address, symbol
               FROM tokens WHERE quality_gate_pass = TRUE""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query gate-pass tokens: %s", e)
        return

    if not rows:
        log.info("No gate-pass tokens found for snapshots")
        return

    collected = 0
    failed = 0
    for token_id, mint, symbol in rows:
        result = collect_snapshot(mint, token_id)
        if result:
            collected += 1
        else:
            failed += 1

    log.info("Snapshot collection done: %d collected, %d failed (of %d total)",
             collected, failed, len(rows))


def collect_momentum_snapshots():
    """4-hourly snapshot for tokens with recent momentum score > 50.
    Uses the same snapshot function but only for momentum candidates."""
    log.info("=== Collecting momentum candidate snapshots ===")

    try:
        rows = execute(
            """SELECT t.id, t.contract_address, t.symbol
               FROM tokens t
               JOIN scores_daily s ON s.token_id = t.id
               WHERE s.date = CURRENT_DATE
                 AND s.momentum_score > 50
                 AND t.quality_gate_pass = TRUE""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query momentum candidates: %s", e)
        return

    if not rows:
        log.info("No momentum candidates for 4h snapshot")
        return

    for token_id, mint, symbol in rows:
        collect_snapshot(mint, token_id)

    log.info("Momentum snapshots done for %d tokens", len(rows))
