"""Adoption Engine — scores protocol/token adoption on 0-100 scale.

Weighted factors:
  1. Fee/revenue growth (DeFiLlama):      20%
  2. Cohort retention 30d/60d:             15%
  3. Developer activity:                   15% (stub — manual input)
  4. Stablecoin inflow (DeFiLlama TVL):    10%
  5. Median wallet size:                   10%
  6. Distribution quality (Gini):          10%
  7. Tokenomics health (CoinGecko):        10%
  8. Liquidity + market structure:         10%

Exit triggers:
  - Revenue negative 2 consecutive months
  - Retention < 30%
  - Developer activity declining 3 months
"""

from config import COINGECKO_API_KEY, get_logger
from db.connection import execute
from quality_gate.helpers import get_json

log = get_logger("engines.adoption")

DEFILLAMA_PROTOCOL_URL = "https://api.llama.fi/protocol"
DEFILLAMA_TVL_URL = "https://api.llama.fi/tvl"
COINGECKO_TOKEN_URL = "https://api.coingecko.com/api/v3/coins"

# Known DeFiLlama slug mappings for Solana protocols
DEFILLAMA_SLUGS = {
    "jupiter": "jupiter",
    "raydium": "raydium",
    "drift": "drift",
    "tensor": "tensor",
    "jito": "jito",
    "marinade": "marinade-finance",
    "orca": "orca",
    "pyth": "pyth-network",
    "meteora": "meteora",
    "kamino": "kamino",
    "marginfi": "marginfi",
    "phoenix": "phoenix",
    "sanctum": "sanctum",
}


# ---------------------------------------------------------------------------
# External data fetchers
# ---------------------------------------------------------------------------

def _fetch_defillama_fees(protocol_slug: str | None) -> dict:
    """Fetch fee/revenue data from DeFiLlama (free, no key needed).
    Returns {"fees_30d": float|None, "revenue_30d": float|None, "fees_growth": float|None}"""
    if not protocol_slug:
        return {"fees_30d": None, "revenue_30d": None, "fees_growth": None}
    try:
        data = get_json(f"https://api.llama.fi/summary/fees/{protocol_slug}")
        total_30d = data.get("total30d")
        total_prev = data.get("totalAllTime")  # simplified: no 60d endpoint
        fees = float(total_30d) if total_30d else None
        growth = None
        if fees and total_prev:
            # Very rough growth estimate
            prev_30d = float(total_prev) - fees if float(total_prev) > fees else fees
            if prev_30d > 0:
                growth = ((fees - prev_30d) / prev_30d) * 100
        return {"fees_30d": fees, "revenue_30d": fees, "fees_growth": growth}
    except Exception as e:
        log.debug("DeFiLlama fees fetch failed for %s: %s", protocol_slug, e)
        return {"fees_30d": None, "revenue_30d": None, "fees_growth": None}


def _fetch_defillama_protocol(protocol_slug: str | None) -> dict:
    """Fetch full protocol data from DeFiLlama: TVL, TVL change, chain breakdown."""
    if not protocol_slug:
        return {"tvl": None, "tvl_change_7d": None, "tvl_change_30d": None}
    try:
        data = get_json(f"{DEFILLAMA_PROTOCOL_URL}/{protocol_slug}")
        current_tvl = float(data.get("currentChainTvls", {}).get("Solana", 0) or
                            data.get("tvl") or 0)

        # Calculate TVL changes from historical data
        tvl_history = data.get("tvl", []) if isinstance(data.get("tvl"), list) else []
        # Also try chainTvls for Solana-specific history
        chain_tvls = data.get("chainTvls", {}).get("Solana", {}).get("tvl", [])
        history = chain_tvls or tvl_history

        tvl_change_7d = None
        tvl_change_30d = None
        if history and len(history) >= 2:
            latest_tvl = float(history[-1].get("totalLiquidityUSD", 0))
            if len(history) >= 8:
                tvl_7d_ago = float(history[-8].get("totalLiquidityUSD", 0))
                if tvl_7d_ago > 0:
                    tvl_change_7d = ((latest_tvl - tvl_7d_ago) / tvl_7d_ago) * 100
            if len(history) >= 31:
                tvl_30d_ago = float(history[-31].get("totalLiquidityUSD", 0))
                if tvl_30d_ago > 0:
                    tvl_change_30d = ((latest_tvl - tvl_30d_ago) / tvl_30d_ago) * 100

        return {
            "tvl": current_tvl,
            "tvl_change_7d": tvl_change_7d,
            "tvl_change_30d": tvl_change_30d,
        }
    except Exception as e:
        log.debug("DeFiLlama protocol fetch failed for %s: %s", protocol_slug, e)
        return {"tvl": None, "tvl_change_7d": None, "tvl_change_30d": None}


def _fetch_coingecko_tokenomics(coingecko_id: str | None) -> dict:
    """Fetch tokenomics data from CoinGecko free API.
    Returns {"circulating_ratio": float|None, "max_supply": float|None}"""
    if not coingecko_id:
        return {"circulating_ratio": None, "max_supply": None}
    try:
        headers = {}
        if COINGECKO_API_KEY:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
        data = get_json(f"{COINGECKO_TOKEN_URL}/{coingecko_id}", headers=headers)
        market = data.get("market_data", {})
        circulating = market.get("circulating_supply") or 0
        total = market.get("total_supply") or market.get("max_supply") or 0
        ratio = (circulating / total) if total > 0 else None
        return {
            "circulating_ratio": ratio,
            "max_supply": total,
        }
    except Exception as e:
        log.debug("CoinGecko fetch failed for %s: %s", coingecko_id, e)
        return {"circulating_ratio": None, "max_supply": None}


# ---------------------------------------------------------------------------
# Factor scoring (each returns 0-100)
# ---------------------------------------------------------------------------

def _score_fee_revenue(snapshots: list[dict], defillama: dict) -> float:
    """Score fee/revenue growth. Uses DeFiLlama data or snapshot revenue field."""
    # Try DeFiLlama first
    growth = defillama.get("fees_growth")
    if growth is not None:
        if growth >= 50:
            return 100
        if growth >= 20:
            return 80
        if growth >= 0:
            return 60
        if growth >= -20:
            return 35
        return 10

    # Fallback: snapshot revenue data
    revenues = [s.get("revenue") for s in snapshots if s.get("revenue") is not None]
    if len(revenues) >= 2:
        if revenues[-1] > revenues[0]:
            return 70
        return 35

    return 50.0  # no data, neutral


def _score_cohort_retention(snapshots: list[dict]) -> float:
    """Score 30d/60d cohort retention."""
    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    ret_30d = latest.get("retention_30d")

    if ret_30d is not None:
        if ret_30d >= 0.7:
            return 100
        if ret_30d >= 0.5:
            return 80
        if ret_30d >= 0.3:
            return 55
        if ret_30d >= 0.15:
            return 30
        return 10

    # Estimate from holder count trend
    if len(snapshots) >= 30:
        holders_now = snapshots[-1].get("holders_quality_adjusted") or 0
        holders_30d = snapshots[0].get("holders_quality_adjusted") or 0
        if holders_30d > 0:
            retention_est = min(1.0, holders_now / holders_30d)
            return retention_est * 100

    return 50.0


def _score_dev_activity(snapshots: list[dict]) -> float:
    """Developer activity score. STUB — uses snapshot dev_commits if available."""
    commits = [s.get("dev_commits") for s in snapshots if s.get("dev_commits") is not None]
    if not commits:
        return 50.0  # neutral — no data

    recent = commits[-1]
    if recent >= 50:
        return 95
    if recent >= 20:
        return 75
    if recent >= 5:
        return 55
    if recent >= 1:
        return 35
    return 15


def _score_stablecoin_inflow(protocol_data: dict) -> float:
    """Score stablecoin inflow using TVL and TVL growth as proxy."""
    tvl = protocol_data.get("tvl")
    if tvl is None:
        return 50.0

    # Base score from TVL magnitude
    if tvl >= 1_000_000_000:
        base = 95
    elif tvl >= 100_000_000:
        base = 85
    elif tvl >= 10_000_000:
        base = 75
    elif tvl >= 1_000_000:
        base = 60
    elif tvl >= 100_000:
        base = 45
    elif tvl >= 10_000:
        base = 30
    else:
        base = 15

    # Bonus/penalty for TVL growth
    change_7d = protocol_data.get("tvl_change_7d")
    if change_7d is not None:
        if change_7d >= 20:
            base = min(100, base + 10)
        elif change_7d >= 5:
            base = min(100, base + 5)
        elif change_7d <= -20:
            base = max(0, base - 10)
        elif change_7d <= -5:
            base = max(0, base - 5)

    return float(base)


def _score_median_wallet(snapshots: list[dict]) -> float:
    """Score median wallet balance — moderate balances indicate real users."""
    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    median = latest.get("median_wallet_balance")
    if median is None:
        return 50.0

    # Very small = potential bots, very large = whale dominated
    # Sweet spot: moderate balance indicates retail adoption
    if median <= 0:
        return 10
    if median < 10:
        return 30
    if median < 100:
        return 50
    if median < 10_000:
        return 80
    if median < 100_000:
        return 65
    return 40  # too whale-heavy


def _score_distribution_quality(snapshots: list[dict]) -> float:
    """Score distribution quality using Gini coefficient.
    Lower Gini = more equal distribution = better."""
    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    gini = latest.get("gini")
    if gini is None:
        return 50.0

    # 0 = perfectly equal, 1 = perfectly concentrated
    if gini <= 0.3:
        return 95
    if gini <= 0.5:
        return 75
    if gini <= 0.7:
        return 55
    if gini <= 0.85:
        return 35
    return 15


def _score_tokenomics(coingecko: dict) -> float:
    """Score tokenomics health from CoinGecko data.
    Higher circulating ratio = more unlocked = less sell pressure risk."""
    ratio = coingecko.get("circulating_ratio")
    if ratio is None:
        return 50.0

    if ratio >= 0.9:
        return 95
    if ratio >= 0.7:
        return 80
    if ratio >= 0.5:
        return 60
    if ratio >= 0.3:
        return 40
    return 20


def _score_liquidity_market(snapshots: list[dict]) -> float:
    """Score liquidity + market structure from snapshot data."""
    if not snapshots:
        return 50.0

    latest = snapshots[-1]
    liq = latest.get("liquidity_depth_10k") or 0
    mcap = latest.get("mcap") or 0

    # Liquidity / mcap ratio: higher = healthier
    if mcap <= 0:
        return 30

    ratio = liq / mcap
    if ratio >= 0.1:
        return 95
    if ratio >= 0.05:
        return 80
    if ratio >= 0.02:
        return 65
    if ratio >= 0.01:
        return 50
    return 25


# ---------------------------------------------------------------------------
# Exit trigger detection
# ---------------------------------------------------------------------------

def _check_exit_triggers(snapshots: list[dict], defillama: dict) -> list[str]:
    """Check codified exit triggers for adoption."""
    triggers = []

    # Revenue negative 2 months
    revenues = [s.get("revenue") for s in snapshots if s.get("revenue") is not None]
    if len(revenues) >= 60:
        month1 = sum(r for r in revenues[-30:] if r) / max(1, len([r for r in revenues[-30:] if r]))
        month2 = sum(r for r in revenues[-60:-30] if r) / max(1, len([r for r in revenues[-60:-30] if r]))
        if month1 < month2 and month2 > 0:
            decline_pct = (month2 - month1) / month2
            if decline_pct > 0.2:  # > 20% decline both months
                triggers.append("revenue_negative_2mo")

    # Retention < 30%
    if snapshots:
        ret = snapshots[-1].get("retention_30d")
        if ret is not None and ret < 0.3:
            triggers.append("retention_below_30pct")

    # Dev activity declining 3 months
    dev_commits = [s.get("dev_commits") for s in snapshots if s.get("dev_commits") is not None]
    if len(dev_commits) >= 90:
        m1 = sum(dev_commits[-30:])
        m2 = sum(dev_commits[-60:-30])
        m3 = sum(dev_commits[-90:-60])
        if m1 < m2 < m3:
            triggers.append("dev_decline_3mo")

    return triggers


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def _get_snapshots(token_id: int, days: int = 90) -> list[dict]:
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
    """Calculate Adoption Engine score for a token.

    Args:
        token_id: DB token ID
        protocol_slug: DeFiLlama protocol slug (optional)
        coingecko_id: CoinGecko coin ID (optional)

    Returns:
        {
            "adoption_score": float (0-100),
            "factors": dict[str, float],
            "exit_triggers": list[str],
            "data_points": int,
        }
    """
    snapshots = _get_snapshots(token_id, days=90)

    # Resolve DeFiLlama slug from known mappings
    resolved_slug = None
    if protocol_slug:
        resolved_slug = DEFILLAMA_SLUGS.get(protocol_slug, protocol_slug)

    # Fetch external data
    defillama = _fetch_defillama_fees(resolved_slug)
    protocol_data = _fetch_defillama_protocol(resolved_slug)
    coingecko = _fetch_coingecko_tokenomics(coingecko_id)

    factors = {
        "fee_revenue": _score_fee_revenue(snapshots, defillama),
        "cohort_retention": _score_cohort_retention(snapshots),
        "dev_activity": _score_dev_activity(snapshots),
        "stablecoin_inflow": _score_stablecoin_inflow(protocol_data),
        "median_wallet": _score_median_wallet(snapshots),
        "distribution_quality": _score_distribution_quality(snapshots),
        "tokenomics": _score_tokenomics(coingecko),
        "liquidity_market": _score_liquidity_market(snapshots),
    }

    weights = {
        "fee_revenue": 0.20,
        "cohort_retention": 0.15,
        "dev_activity": 0.15,
        "stablecoin_inflow": 0.10,
        "median_wallet": 0.10,
        "distribution_quality": 0.10,
        "tokenomics": 0.10,
        "liquidity_market": 0.10,
    }

    adoption_score = sum(factors[k] * weights[k] for k in weights)
    exit_triggers = _check_exit_triggers(snapshots, defillama)

    log.info("Adoption data for token_id=%d: slug=%s, fees_30d=%s, tvl=%s, tvl_7d=%s",
             token_id, resolved_slug,
             defillama.get("fees_30d"), protocol_data.get("tvl"),
             protocol_data.get("tvl_change_7d"))

    result = {
        "adoption_score": round(adoption_score, 1),
        "factors": {k: round(v, 1) for k, v in factors.items()},
        "exit_triggers": exit_triggers,
        "data_points": len(snapshots),
    }

    log.info("Adoption score for token_id=%d: %.1f (data_points=%d, triggers=%s)",
             token_id, adoption_score, len(snapshots), exit_triggers or "none")

    return result
