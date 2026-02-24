"""CoinAnk API — STUB module for liquidation heatmap and whale positions."""

from config import get_logger

log = get_logger("market_intel.coinank")


def get_liquidation_heatmap(symbol: str) -> dict:
    """Get liquidation heatmap data. STUB — returns placeholder data.

    Returns:
        {"symbol": str, "heatmap": list, "source": "stub"}
    """
    log.warning("STUB: CoinAnk liquidation heatmap not implemented for %s", symbol)
    return {
        "symbol": symbol,
        "heatmap": [],
        "source": "stub",
        "message": "CoinAnk API integration pending — COINANK_API_KEY required",
    }


def get_whale_positions(symbol: str) -> dict:
    """Get whale position data. STUB — returns placeholder data.

    Returns:
        {"symbol": str, "whales": list, "source": "stub"}
    """
    log.warning("STUB: CoinAnk whale positions not implemented for %s", symbol)
    return {
        "symbol": symbol,
        "whales": [],
        "total_whale_long": None,
        "total_whale_short": None,
        "source": "stub",
        "message": "CoinAnk API integration pending — COINANK_API_KEY required",
    }
