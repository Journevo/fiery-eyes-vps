"""Infrastructure Track Discovery — monitors established protocol tokens.

Sources:
  - Curated watchlist: scanner/watchlists/infrastructure.json
  - CoinGecko: price, mcap, volume, market data
  - No gate required (established tokens)
  - Scoring: all 3 engines (Momentum + Adoption + Infrastructure)

Runs daily at 05:00 UTC via scheduler.
"""

import requests
from config import COINGECKO_API_KEY, get_logger
from db.connection import execute, execute_one

log = get_logger("infra_discover")

COINGECKO_API_URL = "https://api.coingecko.com/api/v3"


def _load_infra_watchlist() -> list[dict]:
    """Load curated infrastructure watchlist."""
    import json
    from pathlib import Path

    watchlist_path = Path(__file__).parent / "watchlists" / "infrastructure.json"
    try:
        data = json.loads(watchlist_path.read_text())
        tokens = data.get("tokens", [])
        log.info("Loaded %d tokens from infrastructure watchlist", len(tokens))
        return tokens
    except Exception as e:
        log.error("Failed to load infrastructure watchlist: %s", e)
        return []


def _fetch_coingecko_data(coingecko_id: str) -> dict | None:
    """Fetch market data from CoinGecko."""
    try:
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

        resp = requests.get(
            f"{COINGECKO_API_URL}/coins/{coingecko_id}",
            params={
                "localization": "false",
                "tickers": "false",
                "community_data": "false",
                "developer_data": "false",
                "sparkline": "false",
            },
            headers=headers,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        market = data.get("market_data", {})
        return {
            "coingecko_id": coingecko_id,
            "name": data.get("name", ""),
            "symbol": data.get("symbol", "").upper(),
            "price_usd": float(market.get("current_price", {}).get("usd", 0) or 0),
            "market_cap": float(market.get("market_cap", {}).get("usd", 0) or 0),
            "volume_24h": float(market.get("total_volume", {}).get("usd", 0) or 0),
            "price_change_24h_pct": float(market.get("price_change_percentage_24h", 0) or 0),
            "price_change_7d_pct": float(market.get("price_change_percentage_7d", 0) or 0),
            "price_change_30d_pct": float(market.get("price_change_percentage_30d", 0) or 0),
            "ath": float(market.get("ath", {}).get("usd", 0) or 0),
            "ath_change_pct": float(market.get("ath_change_percentage", {}).get("usd", 0) or 0),
            "circulating_supply": float(market.get("circulating_supply", 0) or 0),
            "total_supply": float(market.get("total_supply", 0) or 0),
        }
    except Exception as e:
        log.error("CoinGecko fetch failed for %s: %s", coingecko_id, e)
        return None


def _score_infra_token(symbol: str, cg_data: dict) -> dict | None:
    """Score infra token using DB token_id if available, else from CoinGecko data."""
    try:
        # Look up DB token_id
        row = execute_one(
            "SELECT id FROM tokens WHERE symbol = %s OR contract_address = %s",
            (symbol, cg_data.get("coingecko_id", "")),
        )

        if row:
            token_id = row[0]
            from engines.momentum import score as momentum_score
            from engines.adoption import score as adoption_score
            from engines.infrastructure import score as infra_score

            m = momentum_score(token_id)
            a = adoption_score(token_id)
            i = infra_score(token_id)

            return {
                "symbol": symbol,
                "token_id": token_id,
                "momentum_score": m.get("final_score", 0),
                "adoption_score": a.get("final_score", 0),
                "infrastructure_score": i.get("final_score", 0),
                "composite": (
                    m.get("final_score", 0) * 0.3
                    + a.get("final_score", 0) * 0.3
                    + i.get("final_score", 0) * 0.4
                ),
            }

        # No DB record yet — score from CoinGecko market data only
        mcap = cg_data.get("market_cap", 0)
        vol = cg_data.get("volume_24h", 0)
        change_7d = cg_data.get("price_change_7d_pct", 0)

        # Simple heuristic score based on market metrics
        momentum = min(100, max(0, 50 + change_7d))
        adoption = min(100, (vol / max(mcap, 1)) * 500) if mcap > 0 else 0
        infra = min(100, (mcap / 1e9) * 10) if mcap > 0 else 0

        return {
            "symbol": symbol,
            "token_id": None,
            "momentum_score": round(momentum, 1),
            "adoption_score": round(adoption, 1),
            "infrastructure_score": round(infra, 1),
            "composite": round(momentum * 0.3 + adoption * 0.3 + infra * 0.4, 1),
        }
    except Exception as e:
        log.error("Scoring failed for %s: %s", symbol, e)
        return None


def _upsert_infra_snapshot(symbol: str, cg_data: dict, scores: dict | None):
    """Store infrastructure token data in DB."""
    try:
        execute(
            """INSERT INTO tokens (contract_address, symbol, name, category,
               quality_gate_pass, gate_status, discovered_via, created_at)
               VALUES (%s, %s, %s, 'infrastructure', TRUE, 'passed', 'infra_discovery', NOW())
               ON CONFLICT (contract_address) DO UPDATE SET
                 symbol = EXCLUDED.symbol,
                 name = EXCLUDED.name""",
            (cg_data.get("coingecko_id", symbol.lower()), symbol, cg_data.get("name", symbol)),
        )
    except Exception as e:
        log.error("Failed to upsert infra token %s: %s", symbol, e)


def run_infrastructure_discovery() -> list[dict]:
    """Run the full infrastructure discovery pipeline.

    1. Load curated watchlist
    2. For each token: fetch CoinGecko data -> score through all 3 engines
    3. Store results

    Returns list of scored infrastructure tokens.
    """
    log.info("=== Infrastructure Track Discovery ===")
    results = []

    watchlist = _load_infra_watchlist()
    if not watchlist:
        log.warning("No infrastructure tokens in watchlist")
        return results

    for token in watchlist:
        symbol = token.get("symbol", "")
        coingecko_id = token.get("coingecko_id", "")

        if not coingecko_id:
            log.debug("No CoinGecko ID for %s — skipping", symbol)
            continue

        # Fetch market data
        cg_data = _fetch_coingecko_data(coingecko_id)
        if not cg_data:
            log.warning("No CoinGecko data for %s — skipping", symbol)
            continue

        log.info(
            "%s: $%.2f | mcap $%.0fM | vol $%.0fM | 7d %.1f%%",
            symbol,
            cg_data["price_usd"],
            cg_data["market_cap"] / 1e6,
            cg_data["volume_24h"] / 1e6,
            cg_data["price_change_7d_pct"],
        )

        # Score through all engines
        scores = _score_infra_token(symbol, cg_data)

        _upsert_infra_snapshot(symbol, cg_data, scores)

        result = {
            "symbol": symbol,
            "coingecko_id": coingecko_id,
            "market_data": cg_data,
            "scores": scores,
        }
        results.append(result)

    log.info("Infrastructure discovery complete: %d tokens processed", len(results))

    # Send summary alert
    try:
        from telegram_bot.alerts import send_message
        if results:
            lines = ["🏗 <b>Infrastructure Watch</b>"]
            for r in results:
                cg = r["market_data"]
                sc = r.get("scores") or {}
                lines.append(
                    f"  • {r['symbol']}: ${cg['price_usd']:.2f} "
                    f"({cg['price_change_7d_pct']:+.1f}% 7d) "
                    f"composite={sc.get('composite', 0):.0f}"
                )
            send_message("\n".join(lines))
    except Exception as e:
        log.error("Failed to send infra alert: %s", e)

    return results
