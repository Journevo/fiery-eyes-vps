"""CoinGlass API — OI, funding rates, L/S ratio, liquidations, exchange netflows."""

from config import COINGLASS_API_KEY, get_logger
from quality_gate.helpers import get_json

log = get_logger("market_intel.coinglass")

COINGLASS_BASE = "https://open-api-v3.coinglass.com/api"


def _headers() -> dict:
    if not COINGLASS_API_KEY:
        log.warning("COINGLASS_API_KEY not set — returning defaults")
    return {"coinglassSecret": COINGLASS_API_KEY} if COINGLASS_API_KEY else {}


def get_open_interest(symbol: str) -> dict:
    """Fetch aggregated open interest data.

    Returns:
        {"oi_usd": float|None, "oi_change_24h": float|None,
         "oi_change_4h": float|None}
    """
    if not COINGLASS_API_KEY:
        return {"oi_usd": None, "oi_change_24h": None, "oi_change_4h": None}
    try:
        data = get_json(
            f"{COINGLASS_BASE}/futures/openInterest/chart",
            params={"symbol": symbol.upper(), "interval": "1h", "limit": 24},
            headers=_headers(),
        )
        items = data.get("data", [])
        if not items:
            return {"oi_usd": None, "oi_change_24h": None, "oi_change_4h": None}

        current = float(items[-1].get("openInterest", 0) or 0)
        oi_24h_ago = float(items[0].get("openInterest", 0) or 0) if len(items) >= 24 else None
        oi_4h_ago = float(items[-5].get("openInterest", 0) or 0) if len(items) >= 5 else None

        change_24h = ((current - oi_24h_ago) / oi_24h_ago * 100) if oi_24h_ago else None
        change_4h = ((current - oi_4h_ago) / oi_4h_ago * 100) if oi_4h_ago else None

        return {"oi_usd": current, "oi_change_24h": change_24h, "oi_change_4h": change_4h}
    except Exception as e:
        log.error("CoinGlass OI fetch failed for %s: %s", symbol, e)
        return {"oi_usd": None, "oi_change_24h": None, "oi_change_4h": None}


def get_funding_rates(symbol: str) -> dict:
    """Fetch funding rate data.

    Returns:
        {"current_rate": float|None, "avg_rate_8h": float|None,
         "predicted_rate": float|None}
    """
    if not COINGLASS_API_KEY:
        return {"current_rate": None, "avg_rate_8h": None, "predicted_rate": None}
    try:
        data = get_json(
            f"{COINGLASS_BASE}/futures/fundingRate/current",
            params={"symbol": symbol.upper()},
            headers=_headers(),
        )
        items = data.get("data", [])
        if not items:
            return {"current_rate": None, "avg_rate_8h": None, "predicted_rate": None}

        rates = [float(i.get("rate", 0) or 0) for i in items if i.get("rate") is not None]
        current = rates[0] if rates else None
        avg_rate = sum(rates) / len(rates) if rates else None
        predicted = float(items[0].get("predictedRate", 0) or 0) if items else None

        return {"current_rate": current, "avg_rate_8h": avg_rate, "predicted_rate": predicted}
    except Exception as e:
        log.error("CoinGlass funding rate failed for %s: %s", symbol, e)
        return {"current_rate": None, "avg_rate_8h": None, "predicted_rate": None}


def get_long_short_ratio(symbol: str) -> dict:
    """Fetch long/short ratio.

    Returns:
        {"long_pct": float|None, "short_pct": float|None,
         "ratio": float|None, "timeframe": str}
    """
    if not COINGLASS_API_KEY:
        return {"long_pct": None, "short_pct": None, "ratio": None, "timeframe": "24h"}
    try:
        data = get_json(
            f"{COINGLASS_BASE}/futures/globalLongShortAccountRatio/chart",
            params={"symbol": symbol.upper(), "interval": "1h", "limit": 1},
            headers=_headers(),
        )
        items = data.get("data", [])
        if not items:
            return {"long_pct": None, "short_pct": None, "ratio": None, "timeframe": "24h"}

        latest = items[-1]
        long_pct = float(latest.get("longRate", 50) or 50)
        short_pct = float(latest.get("shortRate", 50) or 50)
        ratio = long_pct / short_pct if short_pct > 0 else None

        return {"long_pct": long_pct, "short_pct": short_pct, "ratio": ratio, "timeframe": "24h"}
    except Exception as e:
        log.error("CoinGlass L/S ratio failed for %s: %s", symbol, e)
        return {"long_pct": None, "short_pct": None, "ratio": None, "timeframe": "24h"}


def get_liquidations(symbol: str, hours: int = 24) -> dict:
    """Fetch liquidation data.

    Returns:
        {"long_liq_usd": float|None, "short_liq_usd": float|None,
         "total_liq": float|None, "largest_single": float|None}
    """
    if not COINGLASS_API_KEY:
        return {"long_liq_usd": None, "short_liq_usd": None,
                "total_liq": None, "largest_single": None}
    try:
        data = get_json(
            f"{COINGLASS_BASE}/futures/liquidation/chart",
            params={"symbol": symbol.upper(), "interval": "1h", "limit": hours},
            headers=_headers(),
        )
        items = data.get("data", [])
        if not items:
            return {"long_liq_usd": None, "short_liq_usd": None,
                    "total_liq": None, "largest_single": None}

        long_total = sum(float(i.get("longLiquidationUsd", 0) or 0) for i in items)
        short_total = sum(float(i.get("shortLiquidationUsd", 0) or 0) for i in items)
        all_liqs = [float(i.get("longLiquidationUsd", 0) or 0) for i in items] + \
                   [float(i.get("shortLiquidationUsd", 0) or 0) for i in items]
        largest = max(all_liqs) if all_liqs else None

        return {
            "long_liq_usd": long_total,
            "short_liq_usd": short_total,
            "total_liq": long_total + short_total,
            "largest_single": largest,
        }
    except Exception as e:
        log.error("CoinGlass liquidation failed for %s: %s", symbol, e)
        return {"long_liq_usd": None, "short_liq_usd": None,
                "total_liq": None, "largest_single": None}


def get_exchange_netflows(symbol: str) -> dict:
    """Fetch exchange netflow data.

    Returns:
        {"netflow_24h": float|None, "netflow_7d": float|None,
         "exchange_reserves": float|None}
    """
    if not COINGLASS_API_KEY:
        return {"netflow_24h": None, "netflow_7d": None, "exchange_reserves": None}
    try:
        data = get_json(
            f"{COINGLASS_BASE}/exchange/netflow",
            params={"symbol": symbol.upper(), "interval": "1d", "limit": 7},
            headers=_headers(),
        )
        items = data.get("data", [])
        if not items:
            return {"netflow_24h": None, "netflow_7d": None, "exchange_reserves": None}

        netflow_24h = float(items[-1].get("netflow", 0) or 0) if items else None
        netflow_7d = sum(float(i.get("netflow", 0) or 0) for i in items) if items else None
        reserves = float(items[-1].get("reserves", 0) or 0) if items else None

        return {"netflow_24h": netflow_24h, "netflow_7d": netflow_7d,
                "exchange_reserves": reserves}
    except Exception as e:
        log.error("CoinGlass netflow failed for %s: %s", symbol, e)
        return {"netflow_24h": None, "netflow_7d": None, "exchange_reserves": None}
