"""Chain Adoption Metrics — DeFiLlama + Santiment data collector.

Fetches TVL, DEX volume, stablecoin market cap, fee revenue per chain from DeFiLlama.
Fetches daily active addresses from Santiment (free API, Solana + ETH).
Stores daily snapshots in chain_metrics table for trend analysis.
"""

import time
from datetime import date, datetime, timezone, timedelta
from config import get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("chain_metrics.adoption")

TARGET_CHAINS = ["Solana", "Ethereum", "Base", "Sui", "Arbitrum"]

# DeFiLlama normalises chain names; map for matching
_CHAIN_ALIASES = {
    "solana": "Solana",
    "ethereum": "Ethereum",
    "base": "Base",
    "sui": "Sui",
    "arbitrum": "Arbitrum",
}


def _normalise_chain(name: str) -> str | None:
    """Return canonical chain name if it's one we track, else None."""
    return _CHAIN_ALIASES.get(name.lower())


# ---------------------------------------------------------------------------
# DeFiLlama fetchers
# ---------------------------------------------------------------------------

def _fetch_chain_tvl() -> dict[str, float]:
    """GET https://api.llama.fi/v2/chains → {chain: tvl_usd}."""
    try:
        data = get_json("https://api.llama.fi/v2/chains")
        record_api_call("defillama_chains", True)
        result = {}
        for item in data:
            chain = _normalise_chain(item.get("name", ""))
            if chain:
                result[chain] = float(item.get("tvl", 0) or 0)
        return result
    except Exception as e:
        log.error("DeFiLlama chain TVL fetch failed: %s", e)
        record_api_call("defillama_chains", False)
        return {}


def _fetch_dex_volumes() -> dict[str, float]:
    """GET DeFiLlama DEX overview → {chain: daily_volume_usd}.

    DeFiLlama breakdown24h format: {chain_lowercase: {protocol_name: volume}}
    """
    try:
        url = (
            "https://api.llama.fi/overview/dexs"
            "?excludeTotalDataChart=true"
            "&excludeTotalDataChartBreakdown=true"
            "&dataType=dailyVolume"
        )
        data = get_json(url)
        record_api_call("defillama_dex", True)
        result = {}
        for protocol in data.get("protocols", []):
            breakdown = protocol.get("breakdown24h", {})
            if not breakdown:
                continue
            for chain_key, proto_data in breakdown.items():
                canon = _normalise_chain(chain_key)
                if not canon:
                    continue
                # proto_data is {protocol_name: volume_usd}
                vol = 0
                if isinstance(proto_data, dict):
                    for v in proto_data.values():
                        vol += float(v or 0)
                elif isinstance(proto_data, (int, float)):
                    vol = float(proto_data)
                result[canon] = result.get(canon, 0) + vol
        return result
    except Exception as e:
        log.error("DeFiLlama DEX volume fetch failed: %s", e)
        record_api_call("defillama_dex", False)
        return {}


def _fetch_stablecoin_chains() -> dict[str, float]:
    """GET https://stablecoins.llama.fi/stablecoinchains → {chain: stablecoin_mcap}."""
    try:
        data = get_json("https://stablecoins.llama.fi/stablecoinchains")
        record_api_call("defillama_stablecoins", True)
        result = {}
        for item in data:
            chain = _normalise_chain(item.get("name", ""))
            if chain:
                result[chain] = float(item.get("totalCirculatingUSD", {}).get("peggedUSD", 0) or 0)
        return result
    except Exception as e:
        log.error("DeFiLlama stablecoin chains fetch failed: %s", e)
        record_api_call("defillama_stablecoins", False)
        return {}


def _fetch_chain_fees() -> dict[str, float]:
    """GET DeFiLlama fee overview per chain → {chain: daily_fees_usd}.

    Uses per-chain endpoint: /overview/fees/{chain}?excludeTotalDataChart=true
    """
    result = {}
    # DeFiLlama chain slugs (lowercase)
    chain_slugs = {
        "Solana": "Solana",
        "Ethereum": "Ethereum",
        "Base": "Base",
        "Sui": "Sui",
        "Arbitrum": "Arbitrum",
    }
    for canon, slug in chain_slugs.items():
        try:
            url = (
                f"https://api.llama.fi/overview/fees/{slug}"
                "?excludeTotalDataChart=true"
                "&excludeTotalDataChartBreakdown=true"
            )
            data = get_json(url)
            total_24h = float(data.get("total24h") or 0)
            if total_24h:
                result[canon] = total_24h
            time.sleep(0.3)  # rate limiting between calls
        except Exception as e:
            log.debug("DeFiLlama fees for %s failed: %s", canon, e)
    if result:
        record_api_call("defillama_chain_fees", True)
        log.info("Chain fees fetched for %d chains", len(result))
    return result


# Santiment slugs for active address queries
_SANTIMENT_SLUGS = {
    "Solana": "solana",
    "Ethereum": "ethereum",
}


def _fetch_active_addresses() -> dict[str, float]:
    """Fetch daily active addresses from Santiment free API (Solana + ETH).

    Santiment GraphQL API: https://api.santiment.net/graphql
    Free tier: 1,000 requests/month, no API key needed.
    Only Solana + Ethereum supported on free tier.
    """
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    # Build GraphQL query for all supported chains
    query_parts = []
    for canon, slug in _SANTIMENT_SLUGS.items():
        alias = canon.lower()
        query_parts.append(
            f'{alias}: getMetric(metric: "daily_active_addresses") {{\n'
            f'  timeseriesData(slug: "{slug}" from: "{yesterday}" to: "{today}" interval: "1d") {{\n'
            f'    datetime\n'
            f'    value\n'
            f'  }}\n'
            f'}}'
        )

    query = "{\n" + "\n".join(query_parts) + "\n}"

    try:
        import requests
        resp = requests.post(
            "https://api.santiment.net/graphql",
            json={"query": query},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        record_api_call("santiment_active_addresses", True)

        result = {}
        for canon, slug in _SANTIMENT_SLUGS.items():
            alias = canon.lower()
            ts_data = data.get(alias, {}).get("timeseriesData", [])
            if ts_data:
                # Take the most recent value
                latest = ts_data[-1].get("value", 0)
                if latest:
                    result[canon] = float(latest)

        if result:
            log.info("Active addresses: %s",
                     ", ".join(f"{k}: {v:,.0f}" for k, v in result.items()))
        return result
    except Exception as e:
        log.error("Santiment active addresses fetch failed: %s", e)
        record_api_call("santiment_active_addresses", False)
        return {}


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _store_chain_metric(d: date, chain: str, metric_name: str, value: float):
    """Upsert a single chain metric row."""
    execute(
        """INSERT INTO chain_metrics (date, chain, metric_name, value)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (date, chain, metric_name)
           DO UPDATE SET value = EXCLUDED.value""",
        (d, chain, metric_name, value),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_chain_metrics():
    """Daily entry point: fetch all chain data from DeFiLlama + Santiment and store."""
    log.info("Collecting chain adoption metrics...")
    today = date.today()

    tvl = _fetch_chain_tvl()
    dex = _fetch_dex_volumes()
    stables = _fetch_stablecoin_chains()
    fees = _fetch_chain_fees()
    active_addrs = _fetch_active_addresses()

    stored = 0
    for chain in TARGET_CHAINS:
        if chain in tvl:
            _store_chain_metric(today, chain, "tvl", tvl[chain])
            stored += 1
        if chain in dex:
            _store_chain_metric(today, chain, "dex_volume", dex[chain])
            stored += 1
        if chain in stables:
            _store_chain_metric(today, chain, "stablecoin_mcap", stables[chain])
            stored += 1
        if chain in fees:
            _store_chain_metric(today, chain, "fees", fees[chain])
            stored += 1
        if chain in active_addrs:
            _store_chain_metric(today, chain, "active_addresses", active_addrs[chain])
            stored += 1

    log.info("Chain metrics stored: %d rows for %s", stored, today)

    # Check for stablecoin flow alerts
    try:
        _check_stablecoin_flow_alert()
    except Exception as e:
        log.error("Stablecoin flow alert check failed: %s", e)

    return {"date": str(today), "rows_stored": stored}


def get_chain_scorecard() -> dict:
    """Query latest + 7d ago metrics, compute trends and market share.

    Returns:
        {
            "date": str,
            "chains": {
                "Solana": {
                    "tvl": float, "tvl_7d_pct": float,
                    "dex_volume": float, "dex_7d_pct": float,
                    "stablecoin_mcap": float, "stablecoin_7d_pct": float,
                    "tvl_share": float, "dex_share": float,
                },
                ...
            },
            "solana_trend": str,
        }
    """
    # Latest date with data
    row = execute_one("SELECT MAX(date) FROM chain_metrics")
    if not row or not row[0]:
        return {"date": None, "chains": {}, "solana_trend": "unknown"}
    latest_date = row[0]

    # Fetch latest metrics
    latest_rows = execute(
        "SELECT chain, metric_name, value FROM chain_metrics WHERE date = %s",
        (latest_date,), fetch=True,
    ) or []

    # Fetch 7d ago
    rows_7d = execute(
        "SELECT chain, metric_name, value FROM chain_metrics WHERE date = %s - INTERVAL '7 days'",
        (latest_date,), fetch=True,
    ) or []

    # Build lookup
    latest = {}
    for chain, metric, val in latest_rows:
        latest.setdefault(chain, {})[metric] = float(val or 0)
    prev = {}
    for chain, metric, val in rows_7d:
        prev.setdefault(chain, {})[metric] = float(val or 0)

    # Totals for market share
    total_tvl = sum(c.get("tvl", 0) for c in latest.values())
    total_dex = sum(c.get("dex_volume", 0) for c in latest.values())

    chains = {}
    for chain in TARGET_CHAINS:
        cur = latest.get(chain, {})
        prv = prev.get(chain, {})
        entry = {}
        for metric in ("tvl", "dex_volume", "stablecoin_mcap", "fees", "active_addresses"):
            cur_val = cur.get(metric, 0)
            prv_val = prv.get(metric, 0)
            pct = ((cur_val - prv_val) / prv_val * 100) if prv_val else 0
            entry[metric] = cur_val
            entry[f"{metric}_7d_pct"] = round(pct, 1)
        entry["tvl_share"] = round(cur.get("tvl", 0) / total_tvl * 100, 1) if total_tvl else 0
        entry["dex_share"] = round(cur.get("dex_volume", 0) / total_dex * 100, 1) if total_dex else 0
        chains[chain] = entry

    return {
        "date": str(latest_date),
        "chains": chains,
        "solana_trend": get_solana_trend(chains),
    }


def get_solana_trend(chains: dict | None = None) -> str:
    """Determine if Solana is gaining, steady, or losing ground.

    Based on 7d changes in TVL and DEX share vs overall.
    """
    if chains is None:
        scorecard = get_chain_scorecard()
        chains = scorecard.get("chains", {})

    sol = chains.get("Solana", {})
    tvl_pct = sol.get("tvl_7d_pct", 0)
    dex_pct = sol.get("dex_volume_7d_pct", 0)

    avg_change = (tvl_pct + dex_pct) / 2

    if avg_change > 5:
        return "accelerating"
    if avg_change > 1:
        return "gaining"
    if avg_change > -1:
        return "steady"
    if avg_change > -5:
        return "losing"
    return "decelerating"


# ---------------------------------------------------------------------------
# Stablecoin flow alerts — uses shared macro_alert_log for dedup
# ---------------------------------------------------------------------------

def _check_stablecoin_flow_alert():
    """Alert on large weekly stablecoin inflow/outflow on Solana (>$100M).

    Uses macro_alert_log table for 24h dedup, shared with macro alerts.
    """
    # Get latest Solana stablecoin mcap
    current = execute_one(
        """SELECT value FROM chain_metrics
           WHERE chain = 'Solana' AND metric_name = 'stablecoin_mcap'
           ORDER BY date DESC LIMIT 1""",
    )
    # Get ~7d ago
    prev = execute_one(
        """SELECT value FROM chain_metrics
           WHERE chain = 'Solana' AND metric_name = 'stablecoin_mcap'
             AND date <= CURRENT_DATE - INTERVAL '6 days'
           ORDER BY date DESC LIMIT 1""",
    )

    if not current or not prev or not current[0] or not prev[0]:
        return

    current_val = float(current[0])
    prev_val = float(prev[0])
    flow = current_val - prev_val

    if abs(flow) < 100_000_000:  # $100M threshold
        return

    # Check dedup via macro_alert_log
    from chain_metrics.macro import _ensure_alert_table, _alert_already_sent, _send_macro_alert

    _ensure_alert_table()

    if flow > 0:
        alert_type = "stablecoin_inflow"
        if _alert_already_sent(alert_type):
            return
        msg = (
            f"💵 <b>STABLECOIN INFLOW — SOLANA</b>\n"
            f"+${flow / 1e6:.0f}M this week (${current_val / 1e9:.1f}B total)\n"
            f"Capital flowing into Solana ecosystem"
        )
        _send_macro_alert(alert_type, 2, msg)
    else:
        alert_type = "stablecoin_outflow"
        if _alert_already_sent(alert_type):
            return
        msg = (
            f"⚠️ <b>STABLECOIN OUTFLOW — SOLANA</b>\n"
            f"-${abs(flow) / 1e6:.0f}M this week (${current_val / 1e9:.1f}B total)\n"
            f"Capital leaving Solana ecosystem"
        )
        _send_macro_alert(alert_type, 2, msg)
