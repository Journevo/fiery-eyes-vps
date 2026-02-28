"""KOL Wallet Monitor — watches tracked wallets for token buys/sells.

Phase 1: Poll every 60 seconds for Tier 1 wallets, every 5 min for Tier 2.
Phase 2: Helius webhooks for instant detection.
"""

import time
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from config import HELIUS_API_KEY, HELIUS_RPC_URL, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json, post_json
from monitoring.degraded import record_api_call

log = get_logger("kol_tracking.monitor")

# SOL price cache (refreshed periodically)
_sol_price_cache = {'price': 0, 'updated': 0}

# Exit alert deduplication: {(wallet_id, token_address): timestamp}
_exit_alert_sent: dict[tuple, float] = {}

# Known program IDs
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
ASSOCIATED_TOKEN_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
WSOL_MINT = "So11111111111111111111111111111111111111112"


def _get_sol_price() -> float:
    """Get current SOL price from DexScreener or cache."""
    global _sol_price_cache
    now = time.time()
    if now - _sol_price_cache['updated'] < 300:  # 5min cache
        return _sol_price_cache['price']
    try:
        data = get_json("https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112")
        pairs = data.get("pairs", [])
        if pairs:
            price = float(pairs[0].get("priceUsd", 0) or 0)
            _sol_price_cache = {'price': price, 'updated': now}
            return price
    except Exception:
        pass
    return _sol_price_cache['price'] or 150  # fallback


def check_kol_wallets(tier_filter: int | None = None):
    """Main function: check active KOL wallets for new transactions.

    Args:
        tier_filter: If set, only check wallets of this tier (1 or 2).
                     If None, check all active wallets.
    """
    if not HELIUS_API_KEY:
        log.warning("HELIUS_API_KEY not set — KOL monitoring disabled")
        return

    try:
        if tier_filter is not None:
            wallets = execute(
                """SELECT id, name, wallet_address, tier, style,
                          conviction_filter_min_usd, conviction_filter_min_hold_sec
                   FROM kol_wallets
                   WHERE is_active = TRUE AND tier = %s
                   ORDER BY tier ASC""",
                (tier_filter,),
                fetch=True,
            )
        else:
            wallets = execute(
                """SELECT id, name, wallet_address, tier, style,
                          conviction_filter_min_usd, conviction_filter_min_hold_sec
                   FROM kol_wallets
                   WHERE is_active = TRUE
                   ORDER BY tier ASC""",
                fetch=True,
            )
    except Exception as e:
        log.error("Failed to fetch KOL wallets: %s", e)
        return

    if not wallets:
        log.info("No active KOL wallets to monitor (tier_filter=%s)", tier_filter)
        return

    sol_price = _get_sol_price()

    for wallet_id, name, address, tier, style, min_usd, min_hold_sec in wallets:
        try:
            _check_wallet(wallet_id, name, address, tier,
                          float(min_usd or 500), int(min_hold_sec or 600),
                          sol_price)
        except Exception as e:
            log.error("Error checking wallet %s (%s): %s", name, address[:12], e)


def _check_wallet(wallet_id: int, name: str, address: str, tier: int,
                   min_usd: float, min_hold_sec: int, sol_price: float):
    """Check a single wallet for new transactions via Helius RPC."""
    # Step 1: Get recent transaction signatures
    try:
        sig_resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignaturesForAddress",
            "params": [address, {"limit": 20}],
        })
        sigs = sig_resp.get("result", [])
        record_api_call("helius", True)
    except Exception as e:
        log.error("Helius RPC failed for %s: %s", name, e)
        record_api_call("helius", False)
        return

    if not sigs:
        return

    # Step 2: Fetch full transaction details for each signature
    for sig_info in sigs:
        sig = sig_info.get("signature")
        if not sig:
            continue

        # Skip already-processed signatures early (avoid unnecessary RPC calls)
        try:
            existing = execute_one(
                "SELECT id FROM kol_transactions WHERE tx_signature = %s", (sig,))
            if existing:
                continue
        except Exception:
            continue

        try:
            tx_resp = post_json(HELIUS_RPC_URL, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "jsonParsed",
                                 "maxSupportedTransactionVersion": 0}],
            })
            tx = tx_resp.get("result")
            if tx:
                _process_rpc_transaction(tx, sig, wallet_id, name, address,
                                         tier, min_usd, min_hold_sec, sol_price)
        except Exception as e:
            log.debug("Failed to fetch tx %s for %s: %s", sig[:16], name, e)


def _process_rpc_transaction(tx: dict, sig: str, wallet_id: int, name: str,
                              address: str, tier: int, min_usd: float,
                              min_hold_sec: int, sol_price: float):
    """Process a jsonParsed RPC transaction, detect token buys/sells."""
    # Extract block time
    block_time = tx.get("blockTime")
    tx_time = (datetime.fromtimestamp(block_time, tz=timezone.utc)
               if block_time else datetime.now(timezone.utc))

    # Parse inner instructions and main instructions for SPL token transfers
    meta = tx.get("meta", {})
    if meta.get("err") is not None:
        return  # failed transaction

    message = tx.get("transaction", {}).get("message", {})

    # Collect all token balance changes from pre/postTokenBalances
    pre_balances = {
        (b.get("mint"), b.get("owner")): float(b.get("uiTokenAmount", {}).get("uiAmount") or 0)
        for b in meta.get("preTokenBalances", [])
    }
    post_balances = {
        (b.get("mint"), b.get("owner")): float(b.get("uiTokenAmount", {}).get("uiAmount") or 0)
        for b in meta.get("postTokenBalances", [])
    }

    # Find all mints where this wallet's balance changed
    all_keys = set(pre_balances.keys()) | set(post_balances.keys())
    wallet_changes = []
    wsol_delta = 0.0  # track WSOL changes separately for SOL amount estimate
    for (mint, owner) in all_keys:
        if owner != address:
            continue
        pre = pre_balances.get((mint, owner), 0)
        post = post_balances.get((mint, owner), 0)
        delta = post - pre
        if abs(delta) < 1e-9:
            continue
        if mint == WSOL_MINT:
            wsol_delta = delta  # negative = SOL spent, positive = SOL received
            continue
        wallet_changes.append({
            'mint': mint,
            'delta': delta,
            'action': 'buy' if delta > 0 else 'sell',
            'token_amount': abs(delta),
        })

    if not wallet_changes:
        return

    # Calculate SOL spent/received: combine native balance change + WSOL change
    account_keys = [k.get("pubkey") if isinstance(k, dict) else k
                    for k in message.get("accountKeys", [])]
    wallet_idx = None
    for i, key in enumerate(account_keys):
        if key == address:
            wallet_idx = i
            break

    native_sol_delta = 0.0
    if wallet_idx is not None:
        pre_sol = meta.get("preBalances", [])[wallet_idx] if wallet_idx < len(meta.get("preBalances", [])) else 0
        post_sol = meta.get("postBalances", [])[wallet_idx] if wallet_idx < len(meta.get("postBalances", [])) else 0
        native_sol_delta = (pre_sol - post_sol) / 1e9  # positive = SOL spent

    # Total SOL involved = native change + WSOL unwrapped/wrapped
    sol_amount = abs(native_sol_delta) + abs(wsol_delta)

    for change in wallet_changes:
        mint = change['mint']
        action = change['action']
        token_amount = change['token_amount']

        # Get token symbol + price from DexScreener (single cached call)
        token_info = _get_token_info(mint)
        token_symbol = token_info['symbol']
        token_price_usd = token_info['price_usd']

        # USD value: prefer token_price * amount (accurate for any swap route)
        # Fall back to SOL-based estimate only if token price unavailable
        if token_price_usd > 0:
            amount_usd = token_amount * token_price_usd
        else:
            amount_usd = sol_amount * sol_price

        # Apply conviction filter — reject $0/None buys
        is_conviction = (action == "buy"
                         and amount_usd is not None
                         and amount_usd > 0
                         and amount_usd >= min_usd)

        # Derive SOL equivalent from USD (fallback to $150/SOL if price missing)
        effective_sol_price = sol_price if sol_price > 0 else 150
        est_sol = amount_usd / effective_sol_price

        # Log transaction
        try:
            execute(
                """INSERT INTO kol_transactions
                   (kol_wallet_id, token_address, token_symbol, tx_signature,
                    action, amount_sol, amount_usd, token_amount, detected_at,
                    is_conviction_buy, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tx_signature) DO NOTHING""",
                (wallet_id, mint, token_symbol, sig, action,
                 Decimal(str(round(est_sol, 9))), Decimal(str(round(amount_usd, 2))),
                 Decimal(str(token_amount)), tx_time, is_conviction,
                 f"tier={tier}"),
            )
        except Exception as e:
            log.error("Failed to log KOL transaction: %s", e)
            return

        log.info("KOL %s %s %s — $%.2f (%.4f SOL, conviction=%s)",
                 name, action, token_symbol or mint[:12], amount_usd, est_sol, is_conviction)

        # Trigger entry pipeline for conviction buys on Tier 1
        if is_conviction and action == "buy" and tier == 1:
            _trigger_entry(mint, token_symbol, name, amount_usd, address)

        # Trigger exit alert for large sells
        if action == "sell":
            _check_exit_signal(wallet_id, name, mint, token_symbol, token_amount, tier)


def _trigger_entry(token_address: str, token_symbol: str | None, kol_name: str,
                   amount_usd: float, kol_wallet: str):
    """Trigger entry pipeline for a Tier 1 KOL conviction buy."""
    log.info("TRIGGER: Tier 1 KOL %s conviction buy %s ($%.0f)",
             kol_name, token_symbol or token_address[:12], amount_usd)

    try:
        from telegram_alpha.entry_pipeline import execute_entry
        execute_entry(
            token_address,
            entry_type='kol_wallet',
            token_data={
                'symbol': token_symbol,
                'kol_name': kol_name,
                'amount_usd': amount_usd,
                'kol_wallet': kol_wallet,
            },
        )
    except Exception as e:
        log.error("Failed to trigger entry pipeline: %s", e)

    # Send alert
    try:
        from telegram_bot.severity import route_alert
        msg = (f"🔴 <b>TIER 1 KOL BUY</b>\n"
               f"👤 {kol_name}\n"
               f"🪙 {token_symbol or token_address[:12]}\n"
               f"💰 ${amount_usd:,.0f}\n"
               f"⚡ Auto-entry triggered")
        route_alert(1, msg)
    except Exception as e:
        log.error("Failed to send KOL alert: %s", e)


def _check_exit_signal(wallet_id: int, name: str, token_address: str,
                       token_symbol: str | None, sold_amount: float, tier: int):
    """Check if this sell constitutes a major exit signal.

    Tracks TOTAL bought vs TOTAL sold across ALL transactions for this
    wallet+token pair.  Deduplicates alerts — skips if already sent for
    this wallet+token in the last 5 minutes.
    """
    global _exit_alert_sent

    try:
        row = execute_one(
            """SELECT SUM(CASE WHEN action='buy' THEN token_amount ELSE 0 END) as total_bought,
                      SUM(CASE WHEN action='sell' THEN token_amount ELSE 0 END) as total_sold
               FROM kol_transactions
               WHERE kol_wallet_id = %s AND token_address = %s""",
            (wallet_id, token_address),
        )
        if not row or not row[0] or float(row[0]) <= 0:
            return

        total_bought = float(row[0])
        total_sold = float(row[1] or 0)
        pct_sold = (total_sold / total_bought) * 100

        if pct_sold > 50 and tier <= 2:
            # Deduplicate: skip if we already sent for this wallet+token in last 5 min
            dedup_key = (wallet_id, token_address)
            now = time.time()
            last_sent = _exit_alert_sent.get(dedup_key, 0)
            if now - last_sent < 300:
                log.debug("Skipping duplicate exit alert for %s/%s (sent %.0fs ago)",
                          name, token_symbol or token_address[:12], now - last_sent)
                return

            _exit_alert_sent[dedup_key] = now

            log.warning("KOL EXIT SIGNAL: %s sold %.0f%% of %s (bought=%.2f sold=%.2f)",
                        name, pct_sold, token_symbol or token_address[:12],
                        total_bought, total_sold)
            try:
                from telegram_bot.severity import route_alert
                msg = (f"🔴 <b>KOL EXIT SIGNAL</b>\n"
                       f"👤 {name} sold {pct_sold:.0f}% of "
                       f"{token_symbol or token_address[:12]}")
                route_alert(1, msg)
            except Exception:
                pass
    except Exception as e:
        log.debug("Exit signal check failed: %s", e)


def detect_convergence() -> list[dict]:
    """Check if 3+ KOL wallets bought same token in last 30 min.

    Returns list of convergence signals.
    """
    try:
        rows = execute(
            """SELECT token_address, token_symbol,
                      COUNT(DISTINCT kol_wallet_id) as wallet_count,
                      ARRAY_AGG(DISTINCT kw.name) as kol_names
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               WHERE kt.action = 'buy'
                 AND kt.is_conviction_buy = TRUE
                 AND kt.detected_at > NOW() - INTERVAL '30 minutes'
               GROUP BY token_address, token_symbol
               HAVING COUNT(DISTINCT kol_wallet_id) >= 3""",
            fetch=True,
        )

        convergences = []
        for token_addr, token_sym, count, names in (rows or []):
            convergences.append({
                'token_address': token_addr,
                'token_symbol': token_sym,
                'wallet_count': count,
                'kol_names': names,
                'type': 'kolscan_convergence',
            })
            log.info("KOL CONVERGENCE: %d wallets bought %s in 30min",
                     count, token_sym or token_addr[:12])

        return convergences
    except Exception as e:
        log.error("Convergence detection failed: %s", e)
        return []


def get_kol_status(token_address: str) -> dict:
    """Get KOL status for a token (used by health score KOL signal).

    Returns:
        {
            'triggering_kol': str|None,
            'status': 'adding'|'holding'|'selling'|'exited'|'none',
            'pct_sold': float,
            'wallets_holding': int,
        }
    """
    try:
        rows = execute(
            """SELECT kw.name, kw.tier, kt.action,
                      SUM(CASE WHEN kt.action='buy' THEN kt.token_amount ELSE 0 END) as bought,
                      SUM(CASE WHEN kt.action='sell' THEN kt.token_amount ELSE 0 END) as sold
               FROM kol_transactions kt
               JOIN kol_wallets kw ON kw.id = kt.kol_wallet_id
               WHERE kt.token_address = %s
               GROUP BY kw.name, kw.tier, kt.action
               ORDER BY kw.tier ASC""",
            (token_address,),
            fetch=True,
        )

        if not rows:
            return {'triggering_kol': None, 'status': 'none', 'pct_sold': 0, 'wallets_holding': 0}

        # Aggregate per KOL
        kols = {}
        for name, tier, action, bought, sold in rows:
            if name not in kols:
                kols[name] = {'tier': tier, 'bought': 0, 'sold': 0}
            kols[name]['bought'] += float(bought or 0)
            kols[name]['sold'] += float(sold or 0)

        # Find triggering KOL (first Tier 1, or highest buyer)
        triggering = None
        for name, data in sorted(kols.items(), key=lambda x: x[1]['tier']):
            if data['bought'] > 0:
                triggering = name
                break

        # Overall status
        total_bought = sum(k['bought'] for k in kols.values())
        total_sold = sum(k['sold'] for k in kols.values())
        pct_sold = (total_sold / total_bought * 100) if total_bought > 0 else 0

        wallets_holding = sum(1 for k in kols.values()
                              if k['bought'] > 0 and k['sold'] < k['bought'] * 0.5)

        if pct_sold > 80:
            status = 'exited'
        elif pct_sold > 30:
            status = 'selling'
        elif wallets_holding > 0:
            status = 'holding'
        else:
            status = 'adding'

        return {
            'triggering_kol': triggering,
            'status': status,
            'pct_sold': round(pct_sold, 1),
            'wallets_holding': wallets_holding,
        }

    except Exception as e:
        log.error("get_kol_status failed: %s", e)
        return {'triggering_kol': None, 'status': 'none', 'pct_sold': 0, 'wallets_holding': 0}


# Token info cache: {address: {'symbol': str|None, 'price_usd': float}}
_token_info_cache = {}


def _get_token_info(token_address: str) -> dict:
    """Get token symbol and USD price from DexScreener (with cache).

    Returns:
        {'symbol': str|None, 'price_usd': float}
    """
    if token_address in _token_info_cache:
        return _token_info_cache[token_address]

    info = {'symbol': None, 'price_usd': 0.0}
    try:
        data = get_json(f"https://api.dexscreener.com/latest/dex/tokens/{token_address}")
        pairs = data.get("pairs", [])
        if pairs:
            pair = pairs[0]
            info['symbol'] = pair.get("baseToken", {}).get("symbol")
            info['price_usd'] = float(pair.get("priceUsd") or 0)
    except Exception:
        pass

    _token_info_cache[token_address] = info
    return info
