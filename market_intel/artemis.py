"""Artemis API — protocol revenue, dev activity, stablecoin flows.

Replaces stubs in Adoption Engine (fee_revenue, dev_activity, stablecoin_inflow)
and Infrastructure Engine (revenue_retained, treasury_runway).
Falls back to DeFiLlama when Artemis unavailable.
"""

from config import ARTEMIS_API_KEY, get_logger
from quality_gate.helpers import get_json

log = get_logger("market_intel.artemis")

ARTEMIS_BASE = "https://api.artemisanalytics.com/v1"
DEFILLAMA_FEES_URL = "https://api.llama.fi/summary/fees"
DEFILLAMA_PROTOCOL_URL = "https://api.llama.fi/protocol"


def _artemis_headers() -> dict:
    if ARTEMIS_API_KEY:
        return {"Authorization": f"Bearer {ARTEMIS_API_KEY}"}
    return {}


def get_protocol_revenue(protocol_id: str) -> dict:
    """Fetch protocol revenue metrics.

    Returns:
        {"revenue_30d": float|None, "revenue_growth_pct": float|None,
         "revenue_retained_pct": float|None, "source": str}
    """
    # Try Artemis first
    if ARTEMIS_API_KEY:
        try:
            data = get_json(
                f"{ARTEMIS_BASE}/protocol/{protocol_id}/revenue",
                headers=_artemis_headers(),
            )
            return {
                "revenue_30d": data.get("revenue_30d"),
                "revenue_growth_pct": data.get("revenue_growth_30d_pct"),
                "revenue_retained_pct": data.get("protocol_revenue_pct"),
                "source": "artemis",
            }
        except Exception as e:
            log.debug("Artemis revenue fetch failed for %s: %s", protocol_id, e)

    # Fallback: DeFiLlama
    try:
        data = get_json(f"{DEFILLAMA_FEES_URL}/{protocol_id}")
        total_30d = float(data.get("total30d") or 0)
        protocol_rev = float(data.get("totalProtocolRevenue30d") or total_30d)
        retained_pct = (protocol_rev / total_30d * 100) if total_30d > 0 else None
        return {
            "revenue_30d": total_30d if total_30d > 0 else None,
            "revenue_growth_pct": None,
            "revenue_retained_pct": retained_pct,
            "source": "defillama",
        }
    except Exception as e:
        log.debug("DeFiLlama revenue fallback failed for %s: %s", protocol_id, e)

    return {"revenue_30d": None, "revenue_growth_pct": None,
            "revenue_retained_pct": None, "source": "none"}


def get_dev_activity(protocol_id: str) -> dict:
    """Fetch developer activity metrics.

    Returns:
        {"commits_30d": int|None, "active_devs": int|None,
         "dev_trend": str|None, "source": str}
    """
    if ARTEMIS_API_KEY:
        try:
            data = get_json(
                f"{ARTEMIS_BASE}/protocol/{protocol_id}/developers",
                headers=_artemis_headers(),
            )
            return {
                "commits_30d": data.get("commits_30d"),
                "active_devs": data.get("active_developers"),
                "dev_trend": data.get("trend"),
                "source": "artemis",
            }
        except Exception as e:
            log.debug("Artemis dev activity failed for %s: %s", protocol_id, e)

    log.warning("Dev activity data unavailable for %s (no Artemis key)", protocol_id)
    return {"commits_30d": None, "active_devs": None,
            "dev_trend": None, "source": "none"}


def get_stablecoin_flows(chain: str = "solana") -> dict:
    """Fetch stablecoin inflow/outflow data.

    Returns:
        {"inflow_7d": float|None, "inflow_30d": float|None,
         "total_stablecoin_mcap": float|None, "source": str}
    """
    if ARTEMIS_API_KEY:
        try:
            data = get_json(
                f"{ARTEMIS_BASE}/chain/{chain}/stablecoins",
                headers=_artemis_headers(),
            )
            return {
                "inflow_7d": data.get("net_inflow_7d"),
                "inflow_30d": data.get("net_inflow_30d"),
                "total_stablecoin_mcap": data.get("total_mcap"),
                "source": "artemis",
            }
        except Exception as e:
            log.debug("Artemis stablecoin flows failed: %s", e)

    # Fallback: DeFiLlama stablecoins
    try:
        data = get_json("https://stablecoins.llama.fi/stablecoins?includePrices=false")
        total = sum(
            float(s.get("circulating", {}).get("peggedUSD", 0) or 0)
            for s in data.get("peggedAssets", [])
        )
        return {
            "inflow_7d": None,
            "inflow_30d": None,
            "total_stablecoin_mcap": total,
            "source": "defillama",
        }
    except Exception as e:
        log.debug("DeFiLlama stablecoin fallback failed: %s", e)

    return {"inflow_7d": None, "inflow_30d": None,
            "total_stablecoin_mcap": None, "source": "none"}


def get_treasury_data(protocol_id: str) -> dict:
    """Fetch treasury and runway data.

    Returns:
        {"treasury_usd": float|None, "runway_months": float|None,
         "burn_rate": float|None, "source": str}
    """
    if ARTEMIS_API_KEY:
        try:
            data = get_json(
                f"{ARTEMIS_BASE}/protocol/{protocol_id}/treasury",
                headers=_artemis_headers(),
            )
            return {
                "treasury_usd": data.get("treasury_usd"),
                "runway_months": data.get("runway_months"),
                "burn_rate": data.get("monthly_burn_rate"),
                "source": "artemis",
            }
        except Exception as e:
            log.debug("Artemis treasury fetch failed for %s: %s", protocol_id, e)

    # Fallback: DeFiLlama protocol data
    try:
        data = get_json(f"{DEFILLAMA_PROTOCOL_URL}/{protocol_id}")
        tvl = float(data.get("tvl") or 0)
        return {
            "treasury_usd": tvl if tvl > 0 else None,
            "runway_months": None,
            "burn_rate": None,
            "source": "defillama",
        }
    except Exception as e:
        log.debug("DeFiLlama treasury fallback failed for %s: %s", protocol_id, e)

    return {"treasury_usd": None, "runway_months": None,
            "burn_rate": None, "source": "none"}
