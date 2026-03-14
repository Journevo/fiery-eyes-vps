"""DeFiLlama Market-Wide Data — Task 9 of Fiery Eyes v5.1

Expanded scope: not just token revenues but full DeFi macro picture.
- Total DeFi TVL + trend
- Solana TVL + chain rank
- Solana vs SUI vs ETH vs Base DEX volume share
- Total stablecoin supply + flows
- HYPE/JUP/PUMP protocol revenue + ranking vs peers
- Top gaining/losing categories
"""

import requests
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("defi_llama")

BASE = "https://api.llama.fi"
STABLE_BASE = "https://stablecoins.llama.fi"

# Chains to compare
COMPARE_CHAINS = ["Solana", "Sui", "Ethereum", "Base"]

# Protocols to track revenue
REVENUE_PROTOCOLS = {
    "HYPE": "hyperliquid",
    "JUP": "jupiter",
    "PUMP": "pump.fun",
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS defi_market (
            date TEXT PRIMARY KEY,
            total_tvl REAL,
            sol_tvl REAL,
            sol_tvl_rank INTEGER,
            sol_dex_vol_24h REAL,
            sol_dex_share REAL,
            total_dex_vol_24h REAL,
            total_stablecoins REAL,
            hype_rev_24h REAL,
            hype_rev_30d REAL,
            jup_rev_24h REAL,
            jup_rev_30d REAL,
            pump_rev_24h REAL,
            pump_rev_30d REAL,
            chain_dex_data TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------
def fetch_chain_tvls() -> dict:
    """Fetch TVL for all chains, return sorted ranking."""
    try:
        resp = requests.get(f"{BASE}/v2/chains", timeout=15)
        resp.raise_for_status()
        chains = resp.json()

        # Sort by TVL descending
        chains.sort(key=lambda c: c.get("tvl", 0), reverse=True)

        result = {
            "total_tvl": sum(c.get("tvl", 0) for c in chains),
            "rankings": {},
        }

        for i, chain in enumerate(chains):
            name = chain.get("name", "")
            tvl = chain.get("tvl", 0)
            if name in COMPARE_CHAINS:
                result["rankings"][name] = {
                    "rank": i + 1,
                    "tvl": tvl,
                    "change_1d": chain.get("change_1d"),
                    "change_7d": chain.get("change_7d"),
                }

        log.info("Total DeFi TVL: $%.1fB, SOL rank: #%d",
                 result["total_tvl"] / 1e9,
                 result["rankings"].get("Solana", {}).get("rank", 0))
        return result
    except Exception as e:
        log.error("Chain TVL fetch failed: %s", e)
        return {}


def fetch_dex_volumes() -> dict:
    """Fetch DEX volume by chain for comparison."""
    result = {"total_24h": 0, "chains": {}}

    # Total DEX volume
    try:
        resp = requests.get(
            f"{BASE}/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
            timeout=15)
        resp.raise_for_status()
        data = resp.json()
        result["total_24h"] = data.get("total24h", 0)
    except Exception as e:
        log.error("Total DEX volume fetch failed: %s", e)

    # Per-chain volumes
    for chain in COMPARE_CHAINS:
        try:
            resp = requests.get(
                f"{BASE}/overview/dexs/{chain}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
                timeout=15)
            resp.raise_for_status()
            data = resp.json()
            vol_24h = data.get("total24h", 0)
            share = round(vol_24h / result["total_24h"] * 100, 1) if result["total_24h"] > 0 else 0
            result["chains"][chain] = {
                "vol_24h": vol_24h,
                "share": share,
                "change_1d": data.get("change_1d"),
                "change_7d": data.get("change_7d"),
            }
        except Exception as e:
            log.warning("DEX volume for %s failed: %s", chain, e)

    log.info("DEX volumes: total $%.1fB, SOL share %.1f%%",
             result["total_24h"] / 1e9,
             result["chains"].get("Solana", {}).get("share", 0))
    return result


def fetch_protocol_revenue(slug: str) -> dict:
    """Fetch protocol revenue from DeFiLlama."""
    try:
        resp = requests.get(
            f"{BASE}/summary/fees/{slug}?dataType=dailyRevenue",
            timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {
            "rev_24h": data.get("total24h", 0),
            "rev_7d": data.get("total7d", 0),
            "rev_30d": data.get("total30d", 0),
            "annualised": (data.get("total30d", 0) or 0) * 12,
        }
    except Exception as e:
        log.error("Revenue fetch for %s failed: %s", slug, e)
        return {}


def fetch_stablecoin_total() -> float:
    """Fetch total stablecoin supply."""
    try:
        resp = requests.get(f"{STABLE_BASE}/stablecoins?includePrices=false", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        total = 0
        for asset in data.get("peggedAssets", []):
            circ = asset.get("circulating", {})
            if isinstance(circ, dict):
                total += circ.get("peggedUSD", 0) or 0
        log.info("Total stablecoins: $%.1fB", total / 1e9)
        return total
    except Exception as e:
        log.error("Stablecoin fetch failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Main data collection
# ---------------------------------------------------------------------------
def collect_defi_data() -> dict:
    """Collect all DeFiLlama data in one call."""
    data = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d")}

    # Chain TVLs
    tvl_data = fetch_chain_tvls()
    data["total_tvl"] = tvl_data.get("total_tvl", 0)
    sol_ranking = tvl_data.get("rankings", {}).get("Solana", {})
    data["sol_tvl"] = sol_ranking.get("tvl", 0)
    data["sol_tvl_rank"] = sol_ranking.get("rank", 0)
    data["tvl_rankings"] = tvl_data.get("rankings", {})

    # DEX volumes
    dex_data = fetch_dex_volumes()
    data["total_dex_vol_24h"] = dex_data.get("total_24h", 0)
    sol_dex = dex_data.get("chains", {}).get("Solana", {})
    data["sol_dex_vol_24h"] = sol_dex.get("vol_24h", 0)
    data["sol_dex_share"] = sol_dex.get("share", 0)
    data["chain_dex"] = dex_data.get("chains", {})

    # Protocol revenues
    data["revenues"] = {}
    for symbol, slug in REVENUE_PROTOCOLS.items():
        rev = fetch_protocol_revenue(slug)
        data["revenues"][symbol] = rev
        log.info("%s revenue: 24h=$%s, 30d=$%s, ann=$%s",
                 symbol,
                 f"{rev.get('rev_24h', 0):,.0f}",
                 f"{rev.get('rev_30d', 0):,.0f}",
                 f"{rev.get('annualised', 0):,.0f}")

    # Stablecoins
    data["total_stablecoins"] = fetch_stablecoin_total()

    return data


def store_defi_data(data: dict):
    """Store DeFi market data."""
    ensure_table()
    import json

    hype = data.get("revenues", {}).get("HYPE", {})
    jup = data.get("revenues", {}).get("JUP", {})
    pump = data.get("revenues", {}).get("PUMP", {})

    execute("""
        INSERT INTO defi_market (date, total_tvl, sol_tvl, sol_tvl_rank,
            sol_dex_vol_24h, sol_dex_share, total_dex_vol_24h, total_stablecoins,
            hype_rev_24h, hype_rev_30d, jup_rev_24h, jup_rev_30d,
            pump_rev_24h, pump_rev_30d, chain_dex_data)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (date) DO UPDATE SET
            total_tvl = EXCLUDED.total_tvl,
            sol_tvl = EXCLUDED.sol_tvl,
            sol_tvl_rank = EXCLUDED.sol_tvl_rank,
            sol_dex_vol_24h = EXCLUDED.sol_dex_vol_24h,
            sol_dex_share = EXCLUDED.sol_dex_share,
            total_dex_vol_24h = EXCLUDED.total_dex_vol_24h,
            total_stablecoins = EXCLUDED.total_stablecoins,
            hype_rev_24h = EXCLUDED.hype_rev_24h,
            hype_rev_30d = EXCLUDED.hype_rev_30d,
            jup_rev_24h = EXCLUDED.jup_rev_24h,
            jup_rev_30d = EXCLUDED.jup_rev_30d,
            pump_rev_24h = EXCLUDED.pump_rev_24h,
            pump_rev_30d = EXCLUDED.pump_rev_30d,
            chain_dex_data = EXCLUDED.chain_dex_data
    """, (data["date"], data["total_tvl"], data["sol_tvl"], data["sol_tvl_rank"],
          data["sol_dex_vol_24h"], data["sol_dex_share"], data["total_dex_vol_24h"],
          data["total_stablecoins"],
          hype.get("rev_24h"), hype.get("rev_30d"),
          jup.get("rev_24h"), jup.get("rev_30d"),
          pump.get("rev_24h"), pump.get("rev_30d"),
          json.dumps(data.get("chain_dex", {}))))
    log.info("Stored DeFi market data for %s", data["date"])


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt_usd(v: float, compact: bool = True) -> str:
    if v >= 1e12:
        return f"${v/1e12:.1f}T"
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:,.0f}"


def _fmt_chg(v) -> str:
    if v is None:
        return "—"
    return f"{v:+.1f}%"


def format_defi_telegram(data: dict) -> str:
    """Format DeFi data for Telegram."""
    lines = ["📊 <b>DeFi MARKET</b>", ""]

    # TVL section
    lines.append(f"<b>TVL:</b> {_fmt_usd(data['total_tvl'])} total")
    rankings = data.get("tvl_rankings", {})
    for chain in COMPARE_CHAINS:
        r = rankings.get(chain, {})
        if r:
            chg = _fmt_chg(r.get("change_1d"))
            lines.append(f"  {chain}: {_fmt_usd(r['tvl'])} (#{r['rank']}) {chg} 24h")

    # DEX volume
    lines.append(f"\n<b>DEX Volume 24h:</b> {_fmt_usd(data['total_dex_vol_24h'])}")
    chain_dex = data.get("chain_dex", {})
    dex_line = []
    for chain in COMPARE_CHAINS:
        cd = chain_dex.get(chain, {})
        if cd:
            short = chain[:3].upper()
            dex_line.append(f"{short} {cd['share']}%")
    if dex_line:
        lines.append("  " + " | ".join(dex_line))

    # Stablecoins
    stables = data.get("total_stablecoins", 0)
    if stables:
        lines.append(f"\n<b>Stablecoins:</b> {_fmt_usd(stables)}")

    # Protocol revenue
    lines.append(f"\n<b>Protocol Revenue:</b>")
    for symbol in ["HYPE", "JUP", "PUMP"]:
        rev = data.get("revenues", {}).get(symbol, {})
        if rev:
            r24 = rev.get("rev_24h", 0)
            r30 = rev.get("rev_30d", 0)
            ann = rev.get("annualised", 0)
            lines.append(f"  {symbol}: {_fmt_usd(r24)}/day | {_fmt_usd(r30)}/30d | {_fmt_usd(ann)}/yr")

    return "\n".join(lines)


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_defi_tracker(send_to_telegram: bool = False) -> dict:
    """Collect all DeFiLlama data, store, optionally send to Telegram."""
    data = collect_defi_data()
    store_defi_data(data)

    msg = format_defi_telegram(data)
    log.info("DeFi report:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)

    return data


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_defi_tracker(send_to_telegram=send_tg)
    if result:
        print(format_defi_telegram(result))
    else:
        print("ERROR: Failed to collect DeFi data")
