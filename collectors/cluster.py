"""Wallet cluster detection — trace funding sources to identify coordinated wallets.
   Uses Helius getSignaturesForAddress to find common funding within time windows."""

import time
from collections import defaultdict
from config import HELIUS_RPC_URL, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import post_json

log = get_logger("collectors.cluster")

# Wallets funded from the same source within this window are clustered
CLUSTER_WINDOW_HOURS = 6
CLUSTER_WINDOW_SECS = CLUSTER_WINDOW_HOURS * 3600

# Max wallets to trace (rate-limit friendly for Helius free tier)
MAX_WALLETS_TO_TRACE = 20
MAX_SIGS_PER_WALLET = 10


def _get_token_holders(mint: str, limit: int = 20) -> list[str]:
    """Get top holder addresses for a token."""
    resp = post_json(HELIUS_RPC_URL, {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenLargestAccounts",
        "params": [mint],
    })
    accounts = resp.get("result", {}).get("value", [])
    return [acc["address"] for acc in accounts[:limit]]


def _get_funding_sources(wallet: str) -> list[dict]:
    """Get recent incoming SOL transfers to identify who funded this wallet.
    Looks at recent signatures and extracts fee payers (likely funders)."""
    try:
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": MAX_SIGS_PER_WALLET}],
        })
        sigs = resp.get("result", [])

        sources = []
        for sig_info in sigs:
            block_time = sig_info.get("blockTime")
            sig = sig_info.get("signature")
            if not sig or not block_time:
                continue

            # Fetch transaction to find the fee payer (funder)
            tx_resp = post_json(HELIUS_RPC_URL, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            })
            tx = tx_resp.get("result")
            if not tx:
                continue

            msg = tx.get("transaction", {}).get("message", {})
            account_keys = msg.get("accountKeys", [])
            if not account_keys:
                continue

            # First signer is the fee payer / likely funder
            first_key = account_keys[0]
            if isinstance(first_key, dict):
                funder = first_key.get("pubkey", "")
            else:
                funder = str(first_key)

            # Skip self-funding
            if funder and funder != wallet:
                sources.append({
                    "funder": funder,
                    "block_time": block_time,
                    "signature": sig,
                })

        return sources
    except Exception as e:
        log.debug("Failed to get funding sources for %s: %s", wallet[:8], e)
        return []


def _group_by_funder(wallet_sources: dict[str, list[dict]]) -> list[list[str]]:
    """Group wallets that share a common funder within the time window.

    Args:
        wallet_sources: {wallet_address: [{funder, block_time, ...}, ...]}

    Returns:
        List of clusters, each cluster is a list of wallet addresses.
    """
    # Build funder → [(wallet, timestamp)] mapping
    funder_map: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for wallet, sources in wallet_sources.items():
        for src in sources:
            funder_map[src["funder"]].append((wallet, src["block_time"]))

    clusters = []
    seen = set()

    for funder, wallet_times in funder_map.items():
        if len(wallet_times) < 2:
            continue

        # Sort by time
        wallet_times.sort(key=lambda x: x[1])

        # Sliding window: group wallets funded within CLUSTER_WINDOW_SECS
        cluster = set()
        window_start = 0

        for i in range(len(wallet_times)):
            while wallet_times[i][1] - wallet_times[window_start][1] > CLUSTER_WINDOW_SECS:
                window_start += 1

            if i - window_start >= 1:  # at least 2 wallets in window
                for j in range(window_start, i + 1):
                    cluster.add(wallet_times[j][0])

        if len(cluster) >= 2:
            cluster_key = frozenset(cluster)
            if cluster_key not in seen:
                seen.add(cluster_key)
                clusters.append(sorted(cluster))

    return clusters


def detect_clusters(mint: str) -> dict:
    """Detect wallet clusters for a token.

    Returns:
        {
            "holders_analyzed": int,
            "clusters": list[list[str]],  # each cluster = list of wallet addrs
            "cluster_count": int,
            "clustered_wallets": int,
            "effective_entity_count": int,
            "raw_holder_count": int,
        }
    """
    log.info("Detecting clusters for %s", mint)

    holders = _get_token_holders(mint, limit=MAX_WALLETS_TO_TRACE)
    if not holders:
        return {
            "holders_analyzed": 0,
            "clusters": [],
            "cluster_count": 0,
            "clustered_wallets": 0,
            "effective_entity_count": 0,
            "raw_holder_count": 0,
        }

    raw_count = len(holders)

    # Trace funding sources for each holder
    wallet_sources = {}
    for i, wallet in enumerate(holders):
        log.debug("Tracing wallet %d/%d: %s", i + 1, raw_count, wallet[:8])
        sources = _get_funding_sources(wallet)
        if sources:
            wallet_sources[wallet] = sources

    # Group by common funders within time window
    clusters = _group_by_funder(wallet_sources)

    # Calculate effective entity count
    # Each cluster counts as 1 entity, non-clustered wallets count as 1 each
    clustered_wallets = set()
    for cluster in clusters:
        clustered_wallets.update(cluster)

    independent_wallets = raw_count - len(clustered_wallets)
    effective_entities = independent_wallets + len(clusters)

    result = {
        "holders_analyzed": raw_count,
        "clusters": clusters,
        "cluster_count": len(clusters),
        "clustered_wallets": len(clustered_wallets),
        "effective_entity_count": max(1, effective_entities),
        "raw_holder_count": raw_count,
    }

    log.info("Cluster detection for %s: %d clusters, %d clustered wallets, "
             "effective entities: %d / %d raw",
             mint, result["cluster_count"], result["clustered_wallets"],
             result["effective_entity_count"], raw_count)

    return result


def update_token_cluster_metrics(mint: str, token_id: int) -> dict:
    """Run cluster detection and store quality-adjusted metrics in tokens table.

    Returns cluster detection result.
    """
    result = detect_clusters(mint)

    try:
        execute(
            """UPDATE tokens SET
                 updated_at = NOW()
               WHERE id = %s""",
            (token_id,),
        )
        # Store cluster data in today's snapshot
        if result["effective_entity_count"] > 0:
            execute(
                """UPDATE snapshots_daily SET
                     holders_quality_adjusted = LEAST(
                         holders_quality_adjusted,
                         %s
                     )
                   WHERE token_id = %s AND date = CURRENT_DATE""",
                (result["effective_entity_count"], token_id),
            )
        log.info("Updated cluster metrics for token_id=%d", token_id)
    except Exception as e:
        log.error("Failed to update cluster metrics for token_id=%d: %s", token_id, e)

    return result


def run_cluster_detection_all():
    """Run cluster detection for all gate-pass tokens."""
    log.info("=== Running cluster detection for all gate-pass tokens ===")

    try:
        rows = execute(
            """SELECT id, contract_address, symbol
               FROM tokens WHERE quality_gate_pass = TRUE""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to query tokens for cluster detection: %s", e)
        return

    if not rows:
        log.info("No gate-pass tokens for cluster detection")
        return

    for token_id, mint, symbol in rows:
        update_token_cluster_metrics(mint, token_id)

    log.info("Cluster detection complete for %d tokens", len(rows))
