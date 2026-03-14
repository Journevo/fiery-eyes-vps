"""Impact Assessment — shared helper for all intelligence outputs.

Every piece of intelligence must end with a PORTFOLIO IMPACT line.
"""


def assess_impact(signal_type: str, token: str = None, direction: str = None,
                  amount_usd: float = None, context: str = None) -> str:
    """Generate one-line portfolio impact assessment.

    Returns a specific IMPACT line like:
    "IMPACT: Bullish RENDER — AI narrative catalyst. Watch for entry below $1.50."
    """
    WATCHLIST = {"JUP", "HYPE", "RENDER", "BONK", "PUMP", "PENGU", "FARTCOIN"}

    if token and token.upper() in WATCHLIST:
        tok = token.upper()
        if direction and direction.upper() == "BUY":
            return f"IMPACT: Bullish {tok} — smart money accumulation. Monitor for entry confirmation."
        elif direction and direction.upper() == "SELL":
            return f"IMPACT: Bearish {tok} — whale distribution detected. Review stop loss levels."
        else:
            return f"IMPACT: {tok} activity detected — watch for directional confirmation."

    if signal_type == "geopolitical":
        return f"IMPACT: Geopolitical risk — monitor for 2nd order effects on DXY/oil/risk sentiment. {context or ''}"
    elif signal_type == "macro":
        return f"IMPACT: Macro signal — may shift regime/deployment %. {context or ''}"
    elif signal_type == "market_structure":
        return f"IMPACT: Market structure shift — review positioning. {context or ''}"

    return f"IMPACT: {context or 'Monitor for portfolio relevance.'}"
