"""Token symbol-to-address resolver with cache, DB-first lookup, DexScreener fallback.

Resolves $SYMBOL cashtags to Solana contract addresses for X intelligence signals.
"""

import time

from config import get_logger
from db.connection import execute_one
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("social.token_resolver")

# In-memory cache: symbol -> {"address", "symbol", "mcap", "liquidity", "ts"}
_cache: dict[str, dict] = {}
_CACHE_TTL = 3600  # 1 hour
_CACHE_MAX = 500
_MIN_LIQUIDITY = 5000  # $5K minimum to filter dust/dead tokens
_RATE_LIMIT_SLEEP = 0.3  # ~200/min, well under DexScreener's ~300/min


def resolve_token(symbol: str) -> dict | None:
    """Resolve a token symbol to its Solana address.

    Lookup order: cache → DB tokens table → DexScreener search API.

    Returns {"address": str, "symbol": str, "mcap": float, "liquidity": float}
    or None if unresolvable.
    """
    if not symbol:
        return None
    symbol_upper = symbol.upper().strip("$ ")
    if not symbol_upper or len(symbol_upper) < 2:
        return None

    # 1. Cache hit
    cached = _cache.get(symbol_upper)
    if cached and time.time() - cached["ts"] < _CACHE_TTL:
        return cached

    # 2. DB tokens table
    try:
        row = execute_one(
            "SELECT contract_address, symbol FROM tokens WHERE UPPER(symbol) = %s LIMIT 1",
            (symbol_upper,),
        )
        if row and row[0]:
            result = {"address": row[0], "symbol": row[1] or symbol_upper,
                       "mcap": 0, "liquidity": 0, "ts": time.time()}
            _cache_put(symbol_upper, result)
            return result
    except Exception as e:
        log.debug("DB lookup failed for %s: %s", symbol_upper, e)

    # 3. DexScreener search API
    return _dexscreener_resolve(symbol_upper)


def _dexscreener_resolve(symbol: str) -> dict | None:
    """Search DexScreener for a Solana token matching the symbol."""
    try:
        time.sleep(_RATE_LIMIT_SLEEP)
        data = get_json(
            f"https://api.dexscreener.com/latest/dex/search?q={symbol}",
        )
        record_api_call("dexscreener", True)

        pairs = data.get("pairs") or []
        # Filter: Solana chain, exact symbol match, sorted by liquidity desc
        candidates = []
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            base = pair.get("baseToken") or {}
            if base.get("symbol", "").upper() != symbol:
                continue
            liq = float((pair.get("liquidity") or {}).get("usd") or 0)
            if liq < _MIN_LIQUIDITY:
                continue
            candidates.append({
                "address": base["address"],
                "symbol": base.get("symbol", symbol),
                "mcap": float(pair.get("marketCap") or 0),
                "liquidity": liq,
            })

        if not candidates:
            log.debug("DexScreener: no Solana match for $%s", symbol)
            return None

        # Pick highest liquidity
        best = max(candidates, key=lambda c: c["liquidity"])
        best["ts"] = time.time()
        _cache_put(symbol, best)
        log.info("Resolved $%s → %s (liq=$%.0f)", symbol, best["address"][:12], best["liquidity"])
        return best

    except Exception as e:
        log.warning("DexScreener resolve failed for $%s: %s", symbol, e)
        record_api_call("dexscreener", False)
        return None


def _cache_put(symbol: str, result: dict):
    """Insert into cache, evict oldest if over max."""
    if len(_cache) >= _CACHE_MAX:
        oldest_key = min(_cache, key=lambda k: _cache[k]["ts"])
        del _cache[oldest_key]
    _cache[symbol] = result


def backfill_x_intelligence() -> dict:
    """Scan x_intelligence for rows with symbol but no address, resolve them.

    Returns {"resolved": int, "skipped": int, "total": int}.
    """
    from db.connection import execute

    rows = execute(
        """SELECT id, token_symbol FROM x_intelligence
           WHERE (token_address IS NULL OR token_address = '')
             AND token_symbol IS NOT NULL AND token_symbol <> ''
           ORDER BY detected_at DESC""",
        fetch=True,
    ) or []

    total = len(rows)
    resolved = 0
    skipped = 0

    log.info("Backfill: %d rows with symbol but no address", total)

    for row_id, symbol in rows:
        result = resolve_token(symbol)
        if result:
            try:
                execute(
                    "UPDATE x_intelligence SET token_address = %s WHERE id = %s",
                    (result["address"], row_id),
                )
                resolved += 1
            except Exception as e:
                log.error("Backfill update failed for id=%s: %s", row_id, e)
                skipped += 1
        else:
            skipped += 1

    log.info("Backfill complete: %d resolved, %d skipped, %d total",
             resolved, skipped, total)
    return {"resolved": resolved, "skipped": skipped, "total": total}


if __name__ == "__main__":
    result = backfill_x_intelligence()
    print(f"Backfill: {result['resolved']}/{result['total']} resolved, {result['skipped']} skipped")
