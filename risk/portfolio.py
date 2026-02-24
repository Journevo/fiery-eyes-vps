"""Portfolio Tracker — five-tier allocation model with Kelly-derived sizing.

Tiers:
  Tier 1 Foundation (40%):  BTC, ETH, SOL
  Tier 2 Adoption/Infra (25%): 5-7 tokens, max 5% each
  Tier 3 Momentum/Memes (15%): 5-10 positions, max 3% each
  Tier 4 Scanner Early (10%): 20-40 micro positions, max 0.5% each
  Tier 5 Cash Reserve (10%): USDC/USDT

Position sizing: Kelly-derived, liquidity-adjusted.
"""

import math
from datetime import datetime, timezone
from config import get_logger
from db.connection import execute, execute_one

log = get_logger("risk.portfolio")

# Tier definitions
TIERS = {
    1: {"name": "Foundation",       "target_pct": 40.0, "max_per_token": 20.0, "max_positions": 3},
    2: {"name": "Adoption/Infra",   "target_pct": 25.0, "max_per_token": 5.0,  "max_positions": 7},
    3: {"name": "Momentum/Memes",   "target_pct": 15.0, "max_per_token": 3.0,  "max_positions": 10},
    4: {"name": "Scanner Early",    "target_pct": 10.0, "max_per_token": 0.5,  "max_positions": 40},
    5: {"name": "Cash Reserve",     "target_pct": 10.0, "max_per_token": 100.0, "max_positions": 3},
}


# ---------------------------------------------------------------------------
# Kelly-derived position sizing
# ---------------------------------------------------------------------------

def kelly_position_size(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Kelly criterion for optimal position sizing.
    Returns fraction of bankroll to allocate (0.0–1.0).

    Uses half-Kelly for safety (divide by 2).

    Args:
        win_rate: historical win probability (0-1)
        avg_win: average winning return multiple (e.g. 2.0 = 2x)
        avg_loss: average losing return fraction (e.g. 0.5 = lose 50%)
    """
    if avg_loss == 0 or avg_win == 0:
        return 0.01  # minimum position

    # Kelly formula: f* = (p * b - q) / b
    # where p = win_rate, q = 1-p, b = avg_win/avg_loss
    b = avg_win / avg_loss
    p = win_rate
    q = 1 - p

    kelly_full = (p * b - q) / b
    kelly_half = kelly_full / 2

    # Clamp to reasonable range
    return max(0.005, min(0.10, kelly_half))


def liquidity_adjusted_size(base_size_pct: float, liquidity_usd: float,
                            portfolio_value_usd: float) -> float:
    """Cap position size by what can be exited within 2% slippage.

    Args:
        base_size_pct: Kelly-derived position size as % of portfolio
        liquidity_usd: total pool liquidity in USD
        portfolio_value_usd: total portfolio value

    Returns:
        Adjusted position size as % of portfolio.
    """
    if portfolio_value_usd <= 0:
        return base_size_pct

    position_usd = portfolio_value_usd * (base_size_pct / 100)

    # Rule: position should be <2% of pool liquidity for clean exit
    max_position_usd = liquidity_usd * 0.02
    if position_usd > max_position_usd and max_position_usd > 0:
        adjusted_pct = (max_position_usd / portfolio_value_usd) * 100
        log.info("Liquidity cap: %.2f%% -> %.2f%% (liq=$%.0f)",
                 base_size_pct, adjusted_pct, liquidity_usd)
        return adjusted_pct

    return base_size_pct


def recommend_position(composite_score: float, confidence: float,
                       category: str, liquidity_usd: float,
                       portfolio_value_usd: float = 100_000,
                       regime_multiplier: float = 1.0) -> dict:
    """Recommend position tier and size for a token.

    Returns:
        {
            "tier": int,
            "tier_name": str,
            "base_size_pct": float,
            "adjusted_size_pct": float,
            "reason": str,
        }
    """
    # Determine tier from category + score
    if category == "meme":
        if composite_score >= 80 and confidence >= 60:
            tier = 3
        else:
            tier = 4
    elif category == "adoption":
        if composite_score >= 85:
            tier = 2
        elif composite_score >= 70:
            tier = 3
        else:
            tier = 4
    elif category == "infra":
        if composite_score >= 85:
            tier = 2
        elif composite_score >= 70:
            tier = 3
        else:
            tier = 4
    else:
        tier = 4

    tier_info = TIERS[tier]

    # Kelly-derived base size
    # Use score as win-rate proxy, typical crypto returns
    win_rate = min(0.7, composite_score / 100 * 0.8)
    avg_win = 2.0 if tier <= 2 else 3.0  # higher upside for memes
    avg_loss = 0.5  # typical max loss
    kelly_size = kelly_position_size(win_rate, avg_win, avg_loss) * 100

    # Cap by tier max
    base_size = min(kelly_size, tier_info["max_per_token"])

    # Apply regime modifier
    base_size *= regime_multiplier

    # Apply confidence adjustment
    base_size *= max(0.5, confidence / 100)

    # Liquidity adjustment
    adjusted = liquidity_adjusted_size(base_size, liquidity_usd, portfolio_value_usd)

    return {
        "tier": tier,
        "tier_name": tier_info["name"],
        "base_size_pct": round(base_size, 3),
        "adjusted_size_pct": round(adjusted, 3),
        "reason": f"Score {composite_score:.0f}, Conf {confidence:.0f}%, "
                  f"Regime {regime_multiplier:.2f}",
    }


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

def open_position(token_id: int, entry_price: float, size_pct: float,
                  tier: int, thesis: str = "",
                  invalidation_rules: dict | None = None) -> int | None:
    """Record a new position in the DB. Returns position ID."""
    import json
    try:
        row = execute_one(
            """INSERT INTO positions
               (token_id, entry_price, size_pct, tier, thesis, invalidation_rules_json, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'open')
               RETURNING id""",
            (token_id, entry_price, size_pct, tier, thesis,
             json.dumps(invalidation_rules or {})),
        )
        pos_id = row[0] if row else None
        log.info("Opened position %s: token_id=%d tier=%d size=%.2f%%",
                 pos_id, token_id, tier, size_pct)
        return pos_id
    except Exception as e:
        log.error("Failed to open position for token_id=%d: %s", token_id, e)
        return None


def close_position(position_id: int, status: str = "closed"):
    """Close a position."""
    try:
        execute(
            "UPDATE positions SET status = %s WHERE id = %s",
            (status, position_id),
        )
        log.info("Closed position %d (status=%s)", position_id, status)
    except Exception as e:
        log.error("Failed to close position %d: %s", position_id, e)


def get_open_positions() -> list[dict]:
    """Get all open positions with token info."""
    try:
        rows = execute(
            """SELECT p.id, p.token_id, t.symbol, t.contract_address, t.category,
                      p.entry_price, p.size_pct, p.tier, p.entry_date, p.thesis
               FROM positions p
               JOIN tokens t ON t.id = p.token_id
               WHERE p.status = 'open'
               ORDER BY p.tier, p.entry_date""",
            fetch=True,
        )
        if not rows:
            return []
        keys = ["id", "token_id", "symbol", "mint", "category",
                "entry_price", "size_pct", "tier", "entry_date", "thesis"]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        log.error("Failed to get open positions: %s", e)
        return []


def get_portfolio_summary() -> dict:
    """Get portfolio summary by tier."""
    positions = get_open_positions()

    tier_summary = {}
    for tier_num, tier_info in TIERS.items():
        tier_positions = [p for p in positions if p["tier"] == tier_num]
        allocated = sum(p["size_pct"] for p in tier_positions)
        tier_summary[tier_num] = {
            "name": tier_info["name"],
            "target_pct": tier_info["target_pct"],
            "allocated_pct": round(allocated, 2),
            "position_count": len(tier_positions),
            "max_positions": tier_info["max_positions"],
            "tokens": [p["symbol"] for p in tier_positions],
        }

    total_allocated = sum(t["allocated_pct"] for t in tier_summary.values())

    return {
        "total_allocated_pct": round(total_allocated, 2),
        "cash_pct": round(100 - total_allocated, 2),
        "open_positions": len(positions),
        "tiers": tier_summary,
    }
