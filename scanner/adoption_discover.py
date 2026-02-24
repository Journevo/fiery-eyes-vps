"""Adoption Track Discovery — finds Solana protocols with growing TVL/fees.

Sources:
  - DeFiLlama: Solana protocol TVL, fees, revenue growth
  - Curated watchlist: scanner/watchlists/adoption.json
  - Light gate: contract_safety + liquidity only
  - Scoring: Momentum + Adoption engines

Runs daily at 05:30 UTC via scheduler.
"""

import requests
from config import get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json

log = get_logger("adoption_discover")

DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"
DEFILLAMA_PROTOCOL_URL = "https://api.llama.fi/protocol/{slug}"
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"

# Thresholds for DeFiLlama discovery
MIN_TVL_USD = 1_000_000        # $1M minimum TVL
MIN_TVL_GROWTH_7D_PCT = 10     # 10% TVL growth in 7 days
MIN_FEES_7D_USD = 50_000       # $50K weekly fees


def _fetch_growing_solana_protocols() -> list[dict]:
    """Fetch Solana protocols with growing TVL from DeFiLlama."""
    try:
        resp = requests.get(DEFILLAMA_PROTOCOLS_URL, timeout=20)
        resp.raise_for_status()
        protocols = resp.json()
    except Exception as e:
        log.error("DeFiLlama protocols fetch failed: %s", e)
        return []

    candidates = []
    for p in protocols:
        # Filter for Solana chain
        chains = p.get("chains", [])
        if "Solana" not in chains:
            continue

        tvl = float(p.get("tvl", 0) or 0)
        if tvl < MIN_TVL_USD:
            continue

        # Calculate TVL growth from change_7d
        change_7d = float(p.get("change_7d", 0) or 0)
        if change_7d < MIN_TVL_GROWTH_7D_PCT:
            continue

        symbol = p.get("symbol", "").upper()
        slug = p.get("slug", "")
        name = p.get("name", "")
        address = p.get("address", "")

        # Try to get Solana-specific address
        gecko_id = p.get("gecko_id", "")

        candidates.append({
            "symbol": symbol,
            "name": name,
            "slug": slug,
            "tvl": tvl,
            "tvl_growth_7d": change_7d,
            "address": address,
            "gecko_id": gecko_id,
        })

    log.info("Found %d growing Solana protocols from DeFiLlama", len(candidates))
    return candidates


def _load_adoption_watchlist() -> list[dict]:
    """Load curated adoption watchlist tokens."""
    import json
    from pathlib import Path

    watchlist_path = Path(__file__).parent / "watchlists" / "adoption.json"
    try:
        data = json.loads(watchlist_path.read_text())
        tokens = data.get("tokens", [])
        log.info("Loaded %d tokens from adoption watchlist", len(tokens))
        return tokens
    except Exception as e:
        log.error("Failed to load adoption watchlist: %s", e)
        return []


def _resolve_mint_from_dexscreener(symbol: str) -> str | None:
    """Try to find a Solana mint address from DexScreener search."""
    try:
        resp = requests.get(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": symbol},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs") or []

        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            base = pair.get("baseToken", {})
            if base.get("symbol", "").upper() == symbol.upper():
                return base.get("address")

        return None
    except Exception:
        return None


def _light_gate(mint: str) -> dict:
    """Run light quality gate: contract_safety + liquidity only.

    Returns:
        {"passed": bool, "checks": dict}
    """
    checks = {}

    # Contract safety
    try:
        from quality_gate import contract_safety
        cs_result = contract_safety.check(mint)
        checks["contract_safety"] = cs_result
    except Exception as e:
        log.error("Contract safety check failed for %s: %s", mint, e)
        checks["contract_safety"] = {"passed": False, "error": str(e)}

    # Liquidity
    try:
        from quality_gate import liquidity
        liq_result = liquidity.check(mint)
        checks["liquidity"] = liq_result
    except Exception as e:
        log.error("Liquidity check failed for %s: %s", mint, e)
        checks["liquidity"] = {"passed": False, "error": str(e)}

    passed = all(
        c.get("passed", False) for c in checks.values()
    )

    return {"passed": passed, "checks": checks}


def _score_token(mint: str, symbol: str) -> dict | None:
    """Score through Momentum + Adoption engines."""
    try:
        from engines.momentum import score as momentum_score
        from engines.adoption import score as adoption_score

        m = momentum_score(mint, mint=mint)
        a = adoption_score(mint)

        return {
            "symbol": symbol,
            "mint": mint,
            "momentum_score": m.get("final_score", 0),
            "adoption_score": a.get("final_score", 0),
            "combined": (m.get("final_score", 0) + a.get("final_score", 0)) / 2,
        }
    except Exception as e:
        log.error("Scoring failed for %s: %s", symbol, e)
        return None


def _already_tracked(mint: str) -> bool:
    """Check if a mint is already in the tokens table."""
    try:
        row = execute_one(
            "SELECT 1 FROM tokens WHERE contract_address = %s",
            (mint,),
        )
        return row is not None
    except Exception:
        return False


def _insert_adoption_token(mint: str, symbol: str, name: str, scores: dict):
    """Insert discovered adoption token into DB."""
    try:
        execute(
            """INSERT INTO tokens (contract_address, symbol, name, category,
               quality_gate_pass, gate_status, discovered_via, created_at)
               VALUES (%s, %s, %s, 'adoption', TRUE, 'passed', 'adoption_discovery', NOW())
               ON CONFLICT (contract_address) DO NOTHING""",
            (mint, symbol, name),
        )
        log.info("Inserted adoption token: %s (%s)", symbol, mint[:16])
    except Exception as e:
        log.error("Failed to insert adoption token %s: %s", symbol, e)


def run_adoption_discovery() -> list[dict]:
    """Run the full adoption discovery pipeline.

    1. Fetch growing protocols from DeFiLlama
    2. Load curated watchlist
    3. For each candidate: resolve mint -> light gate -> score
    4. Insert passing tokens

    Returns list of discovered and scored tokens.
    """
    log.info("=== Adoption Track Discovery ===")
    results = []

    # --- DeFiLlama candidates ---
    defi_candidates = _fetch_growing_solana_protocols()

    for c in defi_candidates:
        symbol = c["symbol"]
        if not symbol:
            continue

        # Try to resolve mint address
        mint = c.get("address")
        if not mint or not mint.startswith(("So", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z")):
            mint = _resolve_mint_from_dexscreener(symbol)

        if not mint:
            log.debug("No mint found for %s — skipping", symbol)
            continue

        if _already_tracked(mint):
            log.debug("%s already tracked — skipping", symbol)
            continue

        # Light gate
        gate = _light_gate(mint)
        if not gate["passed"]:
            log.debug("%s failed light gate — skipping", symbol)
            continue

        # Score
        scores = _score_token(mint, symbol)
        if scores:
            _insert_adoption_token(mint, symbol, c.get("name", symbol), scores)
            results.append({**scores, "source": "defillama", "tvl": c["tvl"]})

    # --- Watchlist candidates ---
    watchlist = _load_adoption_watchlist()

    for token in watchlist:
        symbol = token.get("symbol", "")
        mint = token.get("contract", "")

        if not mint:
            mint = _resolve_mint_from_dexscreener(symbol)

        if not mint:
            log.debug("No mint for watchlist token %s — skipping", symbol)
            continue

        if _already_tracked(mint):
            log.debug("Watchlist token %s already tracked", symbol)
            continue

        gate = _light_gate(mint)
        if not gate["passed"]:
            log.debug("Watchlist token %s failed light gate", symbol)
            continue

        scores = _score_token(mint, symbol)
        if scores:
            _insert_adoption_token(mint, symbol, token.get("name", symbol), scores)
            results.append({**scores, "source": "watchlist"})

    log.info("Adoption discovery complete: %d tokens discovered", len(results))

    # Send alert for high-score discoveries
    try:
        from telegram_bot.alerts import send_message
        if results:
            top = sorted(results, key=lambda r: r.get("combined", 0), reverse=True)[:5]
            lines = ["📈 <b>Adoption Discovery</b>"]
            for r in top:
                lines.append(
                    f"  • {r['symbol']}: M={r['momentum_score']:.0f} "
                    f"A={r['adoption_score']:.0f} (via {r.get('source', '?')})"
                )
            send_message("\n".join(lines))
    except Exception as e:
        log.error("Failed to send adoption alert: %s", e)

    return results
