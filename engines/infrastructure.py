"""Infrastructure Engine — scores infra/protocol token value capture on 0-100 scale.

Weighted factors:
  1. Revenue retained:              20%
  2. Buyback/burn activity:         15%
  3. Treasury runway:               15%
  4. Value capture mechanics:       15%
  5. Partner adoption:              10% (stub)
  6. Tokenomics health:             10%
  7. Regulatory positioning:        10% (stub)
  8. Developer ecosystem:            5%

Exit triggers:
  - Revenue decline 2 consecutive quarters
  - Buyback/burn paused
  - Treasury runway < 12 months
"""

from config import COINGECKO_API_KEY, get_logger
from db.connection import execute
from quality_gate.helpers import get_json

log = get_logger("engines.infrastructure")

DEFILLAMA_FEES_URL = "https://api.llama.fi/summary/fees"
COINGECKO_TOKEN_URL = "https://api.coingecko.com/api/v3/coins"


# ---------------------------------------------------------------------------
# External data fetchers
# ---------------------------------------------------------------------------

def _fetch_revenue_data(protocol_slug: str | None) -> dict:
    """Fetch revenue metrics from DeFiLlama."""
    if not protocol_slug:
        return {"revenue_30d": None, "revenue_retained_pct": None}
    try:
        data = get_json(f"{DEFILLAMA_FEES_URL}/{protocol_slug}")
        total_30d = float(data.get("total30d") or 0)
        # Revenue retained = fees that go to protocol (not LPs)
        protocol_revenue = float(data.get("totalProtocolRevenue30d") or data.get("total30d") or 0)
        retained_pct = (protocol_revenue / total_30d * 100) if total_30d > 0 else None
        return {
            "revenue_30d": total_30d,
            "revenue_retained_pct": retained_pct,
        }
    except Exception as e:
        log.debug("DeFiLlama revenue fetch failed for %s: %s", protocol_slug, e)
        return {"revenue_30d": None, "revenue_retained_pct": None}


def _fetch_coingecko_market(coingecko_id: str | None) -> dict:
    """Fetch market data for tokenomics, supply, and price trend analysis."""
    if not coingecko_id:
        return {}
    try:
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
        data = get_json(f"{COINGECKO_TOKEN_URL}/{coingecko_id}", headers=headers)
        market = data.get("market_data", {})
        return {
            "circulating_supply": market.get("circulating_supply"),
            "total_supply": market.get("total_supply"),
            "max_supply": market.get("max_supply"),
            "mcap": market.get("market_cap", {}).get("usd"),
            "fdv": market.get("fully_diluted_valuation", {}).get("usd"),
            "volume_24h": market.get("total_volume", {}).get("usd"),
            "price_change_7d": market.get("price_change_percentage_7d"),
            "price_change_30d": market.get("price_change_percentage_30d"),
            "price_change_1y": market.get("price_change_percentage_1y"),
            "market_cap_rank": data.get("market_cap_rank"),
        }
    except Exception as e:
        log.debug("CoinGecko market fetch failed for %s: %s", coingecko_id, e)
        return {}


# ---------------------------------------------------------------------------
# Factor scoring (each returns 0-100)
# ---------------------------------------------------------------------------

def _score_revenue_retained(revenue_data: dict) -> float:
    """Score revenue retention — protocols that keep more fees score higher."""
    pct = revenue_data.get("revenue_retained_pct")
    if pct is None:
        return 50.0

    if pct >= 80:
        return 95
    if pct >= 60:
        return 80
    if pct >= 40:
        return 65
    if pct >= 20:
        return 45
    return 25


def _score_buyback_burn(snapshots: list[dict], coingecko: dict) -> float:
    """Score buyback/burn activity.
    Uses supply decrease over time as indicator."""
    # Check if total supply is decreasing (burn indicator)
    total = coingecko.get("total_supply")
    max_s = coingecko.get("max_supply")

    if total and max_s and max_s > 0:
        burn_ratio = 1 - (total / max_s)
        if burn_ratio > 0.1:
            return 90
        if burn_ratio > 0.05:
            return 75
        if burn_ratio > 0.01:
            return 60
        return 45

    # No burn data available — check volume trend as proxy
    if not snapshots:
        return 50.0

    volumes = [s.get("volume") for s in snapshots if s.get("volume")]
    if len(volumes) >= 7:
        recent = sum(volumes[-7:]) / 7
        older = sum(volumes[:7]) / 7
        if older > 0 and recent > older * 1.2:
            return 65  # growing volume suggests activity

    return 50.0  # neutral


def _score_treasury_runway(revenue_data: dict, coingecko: dict) -> float:
    """Score treasury runway. STUB — uses revenue vs mcap as proxy.
    Higher revenue/mcap = longer runway potential."""
    rev = revenue_data.get("revenue_30d")
    mcap = coingecko.get("mcap")

    if rev and mcap and mcap > 0:
        annual_rev = rev * 12
        ratio = annual_rev / mcap
        if ratio >= 0.5:
            return 95
        if ratio >= 0.2:
            return 80
        if ratio >= 0.1:
            return 65
        if ratio >= 0.05:
            return 50
        return 30

    return 50.0


def _score_value_capture(revenue_data: dict, snapshots: list[dict],
                         coingecko: dict | None = None) -> float:
    """Score value capture mechanics — does token benefit from protocol usage?"""
    rev = revenue_data.get("revenue_30d")

    if rev is not None:
        if rev >= 1_000_000:
            return 90
        if rev >= 100_000:
            return 75
        if rev >= 10_000:
            return 60
        if rev > 0:
            return 45
        return 20

    # Fallback: use volume as proxy for utility
    vol = None
    if snapshots:
        vol = snapshots[-1].get("volume") or 0
    elif coingecko:
        vol = coingecko.get("volume_24h") or 0

    if vol:
        if vol >= 1_000_000_000:
            return 85
        if vol >= 100_000_000:
            return 75
        if vol >= 1_000_000:
            return 65
        if vol >= 100_000:
            return 55
    return 50.0


def _score_price_trend(coingecko: dict) -> float:
    """Score price trend from CoinGecko 7d/30d/1y changes.
    Replaces partner_adoption stub."""
    change_7d = coingecko.get("price_change_7d")
    change_30d = coingecko.get("price_change_30d")
    change_1y = coingecko.get("price_change_1y")

    if change_7d is None and change_30d is None:
        return 50.0

    scores = []

    if change_7d is not None:
        if change_7d >= 20:
            scores.append(95)
        elif change_7d >= 5:
            scores.append(75)
        elif change_7d >= -5:
            scores.append(55)
        elif change_7d >= -15:
            scores.append(35)
        else:
            scores.append(15)

    if change_30d is not None:
        if change_30d >= 30:
            scores.append(90)
        elif change_30d >= 10:
            scores.append(70)
        elif change_30d >= -10:
            scores.append(55)
        elif change_30d >= -30:
            scores.append(35)
        else:
            scores.append(15)

    if change_1y is not None:
        if change_1y >= 100:
            scores.append(90)
        elif change_1y >= 20:
            scores.append(70)
        elif change_1y >= -20:
            scores.append(50)
        else:
            scores.append(25)

    return sum(scores) / len(scores) if scores else 50.0


def _score_tokenomics(coingecko: dict) -> float:
    """Score tokenomics health — circulating ratio, FDV/mcap ratio."""
    circ = coingecko.get("circulating_supply") or 0
    total = coingecko.get("total_supply") or 0
    mcap = coingecko.get("mcap") or 0
    fdv = coingecko.get("fdv") or 0

    scores = []

    # Circulating ratio
    if total > 0:
        ratio = circ / total
        if ratio >= 0.8:
            scores.append(90)
        elif ratio >= 0.5:
            scores.append(70)
        elif ratio >= 0.3:
            scores.append(45)
        else:
            scores.append(25)

    # FDV/mcap ratio (closer to 1 = less dilution risk)
    if fdv and mcap and fdv > 0:
        dilution = mcap / fdv
        if dilution >= 0.8:
            scores.append(90)
        elif dilution >= 0.5:
            scores.append(65)
        elif dilution >= 0.3:
            scores.append(40)
        else:
            scores.append(20)

    return sum(scores) / len(scores) if scores else 50.0


def _score_market_rank(coingecko: dict) -> float:
    """Score market cap rank from CoinGecko. Higher rank = better infrastructure.
    Replaces regulatory stub."""
    rank = coingecko.get("market_cap_rank")
    if rank is None:
        return 50.0

    if rank <= 5:
        return 95
    if rank <= 20:
        return 85
    if rank <= 50:
        return 70
    if rank <= 100:
        return 55
    if rank <= 200:
        return 40
    return 25


def _score_dev_ecosystem(snapshots: list[dict]) -> float:
    """Developer ecosystem score from snapshot data."""
    if not snapshots:
        return 50.0

    commits = [s.get("dev_commits") for s in snapshots if s.get("dev_commits") is not None]
    active = [s.get("dev_active") for s in snapshots if s.get("dev_active") is not None]

    if commits:
        total = sum(commits)
        if total >= 100:
            return 90
        if total >= 30:
            return 70
        if total >= 10:
            return 50
        return 30

    if active and any(active):
        return 60

    return 50.0


# ---------------------------------------------------------------------------
# Exit trigger detection
# ---------------------------------------------------------------------------

def _check_exit_triggers(snapshots: list[dict], revenue_data: dict) -> list[str]:
    """Check codified exit triggers for infrastructure tokens."""
    triggers = []

    # Revenue decline 2 consecutive quarters
    revenues = [s.get("revenue") for s in snapshots if s.get("revenue") is not None]
    if len(revenues) >= 180:  # ~6 months of data
        q1 = sum(r for r in revenues[-90:] if r)
        q2 = sum(r for r in revenues[-180:-90] if r)
        if q1 < q2 and q2 > 0:
            triggers.append("revenue_decline_2q")

    # Buyback/burn paused — hard to detect without specific data
    # Use volume decline as rough proxy
    volumes = [s.get("volume") for s in snapshots if s.get("volume")]
    if len(volumes) >= 30:
        recent = sum(volumes[-7:])
        baseline = sum(volumes[-30:]) / 30 * 7
        if baseline > 0 and recent < baseline * 0.3:
            triggers.append("activity_paused_proxy")

    # Treasury < 12 months (proxy: low revenue/mcap)
    rev = revenue_data.get("revenue_30d")
    if snapshots:
        mcap = snapshots[-1].get("mcap") or 0
        if rev is not None and mcap > 0:
            annual_rev = rev * 12
            if annual_rev / mcap < 0.03:
                triggers.append("low_treasury_proxy")

    return triggers


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def _get_snapshots(token_id: int, days: int = 180) -> list[dict]:
    """Fetch recent snapshots for a token."""
    try:
        rows = execute(
            """SELECT date, price, mcap, volume, liquidity_depth_10k,
                      holders_raw, holders_quality_adjusted,
                      retention_7d, retention_30d,
                      top10_pct, top50_pct, gini,
                      median_wallet_balance, fees, revenue,
                      stablecoin_inflow, dev_commits, dev_active,
                      social_velocity, smart_money_netflow
               FROM snapshots_daily
               WHERE token_id = %s AND date >= CURRENT_DATE - %s
               ORDER BY date ASC""",
            (token_id, days),
            fetch=True,
        )
        if not rows:
            return []

        keys = ["date", "price", "mcap", "volume", "liquidity_depth_10k",
                "holders_raw", "holders_quality_adjusted",
                "retention_7d", "retention_30d",
                "top10_pct", "top50_pct", "gini",
                "median_wallet_balance", "fees", "revenue",
                "stablecoin_inflow", "dev_commits", "dev_active",
                "social_velocity", "smart_money_netflow"]
        return [dict(zip(keys, row)) for row in rows]
    except Exception as e:
        log.error("Failed to fetch snapshots for token_id=%d: %s", token_id, e)
        return []


def score(token_id: int, protocol_slug: str | None = None,
          coingecko_id: str | None = None) -> dict:
    """Calculate Infrastructure Engine score for a token.

    Args:
        token_id: DB token ID
        protocol_slug: DeFiLlama protocol slug (optional)
        coingecko_id: CoinGecko coin ID (optional)

    Returns:
        {
            "infra_score": float (0-100),
            "factors": dict[str, float],
            "exit_triggers": list[str],
            "data_points": int,
        }
    """
    snapshots = _get_snapshots(token_id, days=180)

    # Fetch external data
    revenue_data = _fetch_revenue_data(protocol_slug)
    coingecko = _fetch_coingecko_market(coingecko_id)

    factors = {
        "revenue_retained": _score_revenue_retained(revenue_data),
        "buyback_burn": _score_buyback_burn(snapshots, coingecko),
        "treasury_runway": _score_treasury_runway(revenue_data, coingecko),
        "value_capture": _score_value_capture(revenue_data, snapshots, coingecko),
        "price_trend": _score_price_trend(coingecko),
        "tokenomics": _score_tokenomics(coingecko),
        "market_rank": _score_market_rank(coingecko),
        "dev_ecosystem": _score_dev_ecosystem(snapshots),
    }

    weights = {
        "revenue_retained": 0.20,
        "buyback_burn": 0.15,
        "treasury_runway": 0.15,
        "value_capture": 0.15,
        "price_trend": 0.10,
        "tokenomics": 0.10,
        "market_rank": 0.10,
        "dev_ecosystem": 0.05,
    }

    infra_score = sum(factors[k] * weights[k] for k in weights)
    exit_triggers = _check_exit_triggers(snapshots, revenue_data)

    result = {
        "infra_score": round(infra_score, 1),
        "factors": {k: round(v, 1) for k, v in factors.items()},
        "exit_triggers": exit_triggers,
        "data_points": len(snapshots),
    }

    log.info("Infra score for token_id=%s: %.1f (data_points=%d, triggers=%s)",
             token_id, infra_score, len(snapshots), exit_triggers or "none")

    return result
