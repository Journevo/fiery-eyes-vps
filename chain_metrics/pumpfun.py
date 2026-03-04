"""Pump.fun Protocol Metrics — DeFiLlama fees + volume collector.

Fetches protocol-level data for Pump.fun from DeFiLlama:
  - Daily fees / revenue (from /summary/fees/pump.fun)
  - Daily DEX volume (from /summary/dexs/pump.fun)

Stores in holdings_health table alongside SOL/JUP data.
Called as part of collect_holdings_health() every 4 hours.
"""

from config import get_logger
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("chain_metrics.pumpfun")


def fetch_pumpfun_protocol_metrics() -> dict:
    """Fetch Pump.fun protocol metrics from DeFiLlama.

    Returns:
        {
            "fees_24h": float,
            "fees_7d": float,
            "fees_30d": float,
            "fees_change_1d": float,  # % change day over day
            "volume_24h": float,
            "volume_7d": float,
            "volume_30d": float,
            "volume_change_1d": float,
            "fees_all_time": float,
            "volume_all_time": float,
        }
    """
    result = {}

    # 1. Fees / Revenue
    try:
        data = get_json("https://api.llama.fi/summary/fees/pump.fun")
        record_api_call("defillama_pumpfun_fees", True)
        result["fees_24h"] = float(data.get("total24h") or 0)
        result["fees_7d"] = float(data.get("total7d") or 0)
        result["fees_30d"] = float(data.get("total30d") or 0)
        result["fees_change_1d"] = float(data.get("change_1d") or 0)
        result["fees_all_time"] = float(data.get("totalAllTime") or 0)
        log.info("Pump.fun fees: $%.0f/24h, $%.0f/7d", result["fees_24h"], result["fees_7d"])
    except Exception as e:
        log.error("DeFiLlama Pump.fun fees fetch failed: %s", e)
        record_api_call("defillama_pumpfun_fees", False)

    # 2. DEX Volume
    try:
        data = get_json("https://api.llama.fi/summary/dexs/pump.fun")
        record_api_call("defillama_pumpfun_volume", True)
        result["volume_24h"] = float(data.get("total24h") or 0)
        result["volume_7d"] = float(data.get("total7d") or 0)
        result["volume_30d"] = float(data.get("total30d") or 0)
        result["volume_change_1d"] = float(data.get("change_1d") or 0)
        result["volume_all_time"] = float(data.get("totalAllTime") or 0)
        log.info("Pump.fun volume: $%.0f/24h, $%.0f/7d", result["volume_24h"], result["volume_7d"])
    except Exception as e:
        log.error("DeFiLlama Pump.fun volume fetch failed: %s", e)
        record_api_call("defillama_pumpfun_volume", False)

    return result
