"""Holdings Health Tracker — SOL, JUP, Pump.fun token monitoring.

Collects price, volume, and custom metrics every 4 hours.
Stores snapshots in holdings_health table for trend analysis.
"""

import json
from config import COINGECKO_API_KEY, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("chain_metrics.holdings")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# CoinGecko IDs for our holdings
_CG_IDS = {
    "SOL": "solana",
    "JUP": "jupiter-exchange-solana",
}

# DexScreener search terms for tokens not easily found on CoinGecko
_DEX_SEARCH = {
    "PUMPFUN": "pump.fun",
}


def _cg_headers() -> dict:
    h = {}
    if COINGECKO_API_KEY:
        h["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return h


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _fetch_cg_prices() -> dict:
    """Fetch SOL + JUP + BTC prices and 24h/7d changes from CoinGecko."""
    ids = "solana,jupiter-exchange-solana,bitcoin"
    try:
        data = get_json(
            f"{COINGECKO_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ids,
                "order": "market_cap_desc",
                "sparkline": "false",
                "price_change_percentage": "7d",
            },
            headers=_cg_headers(),
        )
        record_api_call("coingecko_holdings", True)
        result = {}
        for coin in data:
            result[coin["id"]] = {
                "price": coin.get("current_price") or 0,
                "mcap": coin.get("market_cap") or 0,
                "volume_24h": coin.get("total_volume") or 0,
                "change_24h": coin.get("price_change_percentage_24h") or 0,
                "change_7d": coin.get("price_change_percentage_7d_in_currency") or 0,
            }
        return result
    except Exception as e:
        log.error("CoinGecko holdings price fetch failed: %s", e)
        record_api_call("coingecko_holdings", False)
        return {}


def _fetch_sol_health(cg_data: dict) -> dict:
    """Build SOL health metrics."""
    sol = cg_data.get("solana", {})
    btc = cg_data.get("bitcoin", {})

    sol_price = sol.get("price", 0)
    btc_price = btc.get("price", 0)
    sol_btc = sol_price / btc_price if btc_price else 0

    return {
        "price_usd": sol_price,
        "custom_metrics": {
            "sol_btc_ratio": round(sol_btc, 6),
            "change_24h": round(sol.get("change_24h", 0), 2),
            "change_7d": round(sol.get("change_7d", 0), 2),
            "mcap": sol.get("mcap", 0),
            "volume_24h": sol.get("volume_24h", 0),
        },
    }


def _fetch_jup_health(cg_data: dict) -> dict:
    """Build JUP health metrics."""
    jup = cg_data.get("jupiter-exchange-solana", {})

    return {
        "price_usd": jup.get("price", 0),
        "custom_metrics": {
            "change_24h": round(jup.get("change_24h", 0), 2),
            "change_7d": round(jup.get("change_7d", 0), 2),
            "mcap": jup.get("mcap", 0),
            "volume_24h": jup.get("volume_24h", 0),
        },
    }


def _fetch_pumpfun_health() -> dict:
    """Try to find Pump.fun token price via DexScreener."""
    try:
        data = get_json(
            "https://api.dexscreener.com/latest/dex/search",
            params={"q": "pump.fun"},
        )
        record_api_call("dexscreener_pumpfun", True)
        pairs = data.get("pairs", [])
        # Look for the main Pump.fun token on Solana
        for pair in pairs:
            if pair.get("chainId") != "solana":
                continue
            base = pair.get("baseToken", {})
            symbol = (base.get("symbol") or "").upper()
            if "PUMP" in symbol:
                price = float(pair.get("priceUsd") or 0)
                volume = float(pair.get("volume", {}).get("h24") or 0)
                change_24h = float(pair.get("priceChange", {}).get("h24") or 0)
                mcap = float(pair.get("marketCap") or pair.get("fdv") or 0)
                return {
                    "price_usd": price,
                    "custom_metrics": {
                        "change_24h": round(change_24h, 2),
                        "volume_24h": volume,
                        "mcap": mcap,
                        "symbol": base.get("symbol", "PUMP"),
                    },
                }
        return {"price_usd": 0, "custom_metrics": {"note": "Pump.fun token not found on DexScreener"}}
    except Exception as e:
        log.error("DexScreener Pump.fun search failed: %s", e)
        record_api_call("dexscreener_pumpfun", False)
        return {"price_usd": 0, "custom_metrics": {"error": str(e)}}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_holding(token: str, price_usd: float, custom_metrics: dict):
    """Insert a holdings_health row."""
    execute(
        """INSERT INTO holdings_health (token, price_usd, custom_metrics)
           VALUES (%s, %s, %s)""",
        (token, price_usd, json.dumps(custom_metrics)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_holdings_health():
    """Every-4h entry point: fetch and store holdings snapshots."""
    log.info("Collecting holdings health...")

    cg_data = _fetch_cg_prices()

    stored = 0

    # SOL
    sol = _fetch_sol_health(cg_data)
    if sol["price_usd"]:
        _store_holding("SOL", sol["price_usd"], sol["custom_metrics"])
        stored += 1
        log.info("SOL: $%.2f | SOL/BTC: %.6f", sol["price_usd"],
                 sol["custom_metrics"].get("sol_btc_ratio", 0))

    # JUP
    jup = _fetch_jup_health(cg_data)
    if jup["price_usd"]:
        _store_holding("JUP", jup["price_usd"], jup["custom_metrics"])
        stored += 1
        log.info("JUP: $%.4f", jup["price_usd"])

    # Pump.fun token price
    pf = _fetch_pumpfun_health()
    # Enrich with protocol metrics from DeFiLlama
    try:
        from chain_metrics.pumpfun import fetch_pumpfun_protocol_metrics
        proto = fetch_pumpfun_protocol_metrics()
        if proto:
            pf["custom_metrics"]["fees_24h"] = proto.get("fees_24h", 0)
            pf["custom_metrics"]["fees_7d"] = proto.get("fees_7d", 0)
            pf["custom_metrics"]["fees_30d"] = proto.get("fees_30d", 0)
            pf["custom_metrics"]["fees_change_1d"] = proto.get("fees_change_1d", 0)
            pf["custom_metrics"]["protocol_volume_24h"] = proto.get("volume_24h", 0)
            pf["custom_metrics"]["protocol_volume_7d"] = proto.get("volume_7d", 0)
    except Exception as e:
        log.error("Pump.fun protocol metrics failed: %s", e)
    if pf["price_usd"] or pf["custom_metrics"].get("fees_24h"):
        _store_holding("PUMPFUN", pf["price_usd"], pf["custom_metrics"])
        stored += 1
        fees = pf["custom_metrics"].get("fees_24h", 0)
        log.info("PUMPFUN: $%.6f | Fees: $%.0f/24h", pf["price_usd"], fees)

    log.info("Holdings health stored: %d tokens", stored)
    return {"tokens_stored": stored}


def get_holdings_summary() -> dict:
    """Query latest + 7d ago holdings for each token.

    Returns:
        {
            "SOL": {"price": float, "change_7d": float, "sol_btc_ratio": float, ...},
            "JUP": {"price": float, "change_7d": float, ...},
            "PUMPFUN": {"price": float, "change_7d": float, ...},
        }
    """
    result = {}
    for token in ("SOL", "JUP", "PUMPFUN"):
        # Latest
        row = execute_one(
            """SELECT price_usd, custom_metrics
               FROM holdings_health
               WHERE token = %s
               ORDER BY timestamp DESC LIMIT 1""",
            (token,),
        )
        if not row:
            continue

        price, metrics = row
        metrics = metrics if isinstance(metrics, dict) else {}

        # 7d ago
        row_7d = execute_one(
            """SELECT price_usd
               FROM holdings_health
               WHERE token = %s AND timestamp < NOW() - INTERVAL '6 days'
               ORDER BY timestamp DESC LIMIT 1""",
            (token,),
        )
        price_7d = float(row_7d[0]) if row_7d and row_7d[0] else None
        change_7d = ((float(price) - price_7d) / price_7d * 100) if price_7d else metrics.get("change_7d", 0)

        result[token] = {
            "price": float(price or 0),
            "change_7d": round(change_7d, 2),
            **metrics,
        }

    return result
