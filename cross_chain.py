"""Cross-Chain Monitoring — Task 15 of Fiery Eyes v5.1

Weekly comparison: SOL vs SUI vs ETH vs Base on:
- TVL (growth rate)
- DEX volume (market share)
- Chain revenue/fees
- Stablecoin balances
- Active addresses (if available)

Alert thresholds per spec:
- SOL DEX volume share drops >5% WoW → warn
- SOL loses #1 DEX volume → urgent
- SUI overtakes SOL on 2+ metrics for 2+ weeks → reassess
- SOL stablecoin outflow >$500M/week → capital flight
"""

import json
import requests
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("cross_chain")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "💼 Portfolio", "📈 Market", "🔧 Tools"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


CHAINS = ["Solana", "Sui", "Ethereum", "Base"]
LLAMA_BASE = "https://api.llama.fi"


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS cross_chain (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            chain TEXT NOT NULL,
            tvl REAL,
            tvl_rank INTEGER,
            tvl_change_7d REAL,
            dex_vol_24h REAL,
            dex_share REAL,
            dex_change_7d REAL,
            fees_24h REAL,
            stablecoin_bal REAL,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (date, chain)
        )
    """)


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
def fetch_chain_data() -> dict:
    """Fetch comprehensive cross-chain comparison data."""
    data = {"chains": {}, "total_dex_vol": 0}

    # 1. TVL + rankings
    try:
        resp = requests.get(f"{LLAMA_BASE}/v2/chains", timeout=15)
        resp.raise_for_status()
        chains = resp.json()
        chains.sort(key=lambda c: c.get("tvl", 0), reverse=True)

        for i, chain in enumerate(chains):
            name = chain.get("name", "")
            if name in CHAINS:
                data["chains"][name] = {
                    "tvl": chain.get("tvl", 0),
                    "tvl_rank": i + 1,
                    "tvl_change_1d": chain.get("change_1d"),
                    "tvl_change_7d": chain.get("change_7d"),
                }
    except Exception as e:
        log.error("Chain TVL fetch failed: %s", e)

    # 2. DEX volume per chain
    try:
        resp = requests.get(
            f"{LLAMA_BASE}/overview/dexs?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
            timeout=15)
        resp.raise_for_status()
        total_vol = resp.json().get("total24h", 0)
        data["total_dex_vol"] = total_vol
    except Exception as e:
        log.error("Total DEX vol failed: %s", e)

    for chain in CHAINS:
        try:
            resp = requests.get(
                f"{LLAMA_BASE}/overview/dexs/{chain}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyVolume",
                timeout=15)
            resp.raise_for_status()
            d = resp.json()
            vol = d.get("total24h", 0)
            share = round(vol / data["total_dex_vol"] * 100, 1) if data["total_dex_vol"] > 0 else 0

            if chain not in data["chains"]:
                data["chains"][chain] = {}
            data["chains"][chain]["dex_vol_24h"] = vol
            data["chains"][chain]["dex_share"] = share
            data["chains"][chain]["dex_change_1d"] = d.get("change_1d")
            data["chains"][chain]["dex_change_7d"] = d.get("change_7d")
        except Exception as e:
            log.warning("DEX vol for %s failed: %s", chain, e)

    # 3. Chain fees/revenue
    for chain in CHAINS:
        try:
            resp = requests.get(
                f"{LLAMA_BASE}/overview/fees/{chain}?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true&dataType=dailyFees",
                timeout=15)
            resp.raise_for_status()
            fees = resp.json().get("total24h", 0)
            if chain in data["chains"]:
                data["chains"][chain]["fees_24h"] = fees
        except Exception as e:
            log.warning("Fees for %s failed: %s", chain, e)

    # 4. Stablecoin balances per chain
    try:
        resp = requests.get(f"https://stablecoins.llama.fi/stablecoinchains", timeout=15)
        resp.raise_for_status()
        for chain_data in resp.json():
            name = chain_data.get("name", "")
            if name in CHAINS and name in data["chains"]:
                total = chain_data.get("totalCirculatingUSD", {})
                if isinstance(total, dict):
                    bal = total.get("peggedUSD", 0)
                else:
                    bal = total or 0
                data["chains"][name]["stablecoin_bal"] = bal
    except Exception as e:
        log.error("Stablecoin chains failed: %s", e)

    return data


# ---------------------------------------------------------------------------
# Alert detection
# ---------------------------------------------------------------------------
def check_alerts(data: dict) -> list:
    """Check cross-chain alert thresholds."""
    alerts = []
    sol = data.get("chains", {}).get("Solana", {})

    # SOL DEX share drop
    sol_share = sol.get("dex_share", 0)
    sol_dex_7d = sol.get("dex_change_7d")
    if sol_dex_7d is not None and sol_dex_7d < -5:
        alerts.append({
            "level": "warn",
            "msg": f"SOL DEX volume share dropped {sol_dex_7d:+.1f}% WoW (now {sol_share}%)",
        })

    # SOL loses #1 DEX volume
    chain_vols = {c: d.get("dex_vol_24h", 0) for c, d in data.get("chains", {}).items()}
    if chain_vols:
        top_chain = max(chain_vols, key=chain_vols.get)
        if top_chain != "Solana" and sol.get("dex_vol_24h", 0) > 0:
            alerts.append({
                "level": "urgent",
                "msg": f"SOL lost #1 DEX volume to {top_chain} (SOL {sol_share}%)",
            })

    # SUI overtaking on metrics
    sui = data.get("chains", {}).get("Sui", {})
    sui_wins = 0
    if sui.get("tvl", 0) > sol.get("tvl", 0):
        sui_wins += 1
    if sui.get("dex_vol_24h", 0) > sol.get("dex_vol_24h", 0):
        sui_wins += 1
    if sui.get("fees_24h", 0) > sol.get("fees_24h", 0):
        sui_wins += 1
    if sui_wins >= 2:
        alerts.append({
            "level": "urgent",
            "msg": f"SUI overtaking SOL on {sui_wins} metrics — portfolio reassessment needed",
        })

    return alerts


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def store_cross_chain(data: dict):
    """Store cross-chain snapshot."""
    ensure_table()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for chain, d in data.get("chains", {}).items():
        execute("""
            INSERT INTO cross_chain (date, chain, tvl, tvl_rank, tvl_change_7d,
                dex_vol_24h, dex_share, dex_change_7d, fees_24h, stablecoin_bal)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, chain) DO UPDATE SET
                tvl = EXCLUDED.tvl, tvl_rank = EXCLUDED.tvl_rank,
                tvl_change_7d = EXCLUDED.tvl_change_7d,
                dex_vol_24h = EXCLUDED.dex_vol_24h, dex_share = EXCLUDED.dex_share,
                dex_change_7d = EXCLUDED.dex_change_7d,
                fees_24h = EXCLUDED.fees_24h, stablecoin_bal = EXCLUDED.stablecoin_bal
        """, (today, chain, d.get("tvl"), d.get("tvl_rank"), d.get("tvl_change_7d"),
              d.get("dex_vol_24h"), d.get("dex_share"), d.get("dex_change_7d"),
              d.get("fees_24h"), d.get("stablecoin_bal")))

    log.info("Stored cross-chain data for %d chains", len(data.get("chains", {})))


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt(v, kind="usd"):
    if v is None:
        return "—"
    if kind == "usd":
        if abs(v) >= 1e9: return f"${v/1e9:.1f}B"
        if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
        if abs(v) >= 1e3: return f"${v/1e3:.0f}K"
        return f"${v:,.0f}"
    if kind == "pct":
        return f"{v:+.1f}%"
    return str(v)


def format_cross_chain_telegram(data: dict, alerts: list) -> str:
    """Format cross-chain comparison for Telegram."""
    lines = ["🔗 <b>CROSS-CHAIN SCORECARD</b>", ""]

    # Header
    lines.append("<pre>")
    lines.append(f"{'Chain':<8s} {'TVL':>8s} {'Rank':>5s} {'DEX Vol':>8s} {'Share':>6s} {'Fees':>8s} {'Stables':>8s}")

    for chain in CHAINS:
        d = data.get("chains", {}).get(chain, {})
        short = chain[:3].upper() if chain != "Ethereum" else "ETH"
        tvl = _fmt(d.get("tvl"))
        rank = f"#{d['tvl_rank']}" if d.get("tvl_rank") else "—"
        dex = _fmt(d.get("dex_vol_24h"))
        share = f"{d['dex_share']}%" if d.get("dex_share") else "—"
        fees = _fmt(d.get("fees_24h"))
        stables = _fmt(d.get("stablecoin_bal"))
        lines.append(f"{short:<8s} {tvl:>8s} {rank:>5s} {dex:>8s} {share:>6s} {fees:>8s} {stables:>8s}")

    lines.append("</pre>")

    # WoW changes for Solana
    sol = data.get("chains", {}).get("Solana", {})
    changes = []
    if sol.get("tvl_change_7d") is not None:
        changes.append(f"TVL {sol['tvl_change_7d']:+.1f}%")
    if sol.get("dex_change_7d") is not None:
        changes.append(f"DEX {sol['dex_change_7d']:+.1f}%")
    if changes:
        lines.append(f"SOL 7d: {' | '.join(changes)}")

    # Alerts
    for a in alerts:
        emoji = "🚨" if a["level"] == "urgent" else "⚠️"
        lines.append(f"{emoji} {a['msg']}")

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
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_cross_chain(send_to_telegram: bool = False) -> dict:
    """Collect cross-chain data, check alerts, store."""
    data = fetch_chain_data()
    alerts = check_alerts(data)
    store_cross_chain(data)

    msg = format_cross_chain_telegram(data, alerts)
    log.info("Cross-chain:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)
        for a in alerts:
            if a["level"] == "urgent":
                send_telegram(f"🚨 <b>CROSS-CHAIN ALERT</b>\n{a['msg']}")

    return {"data": data, "alerts": alerts}


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_cross_chain(send_to_telegram=send_tg)
    print(format_cross_chain_telegram(result["data"], result["alerts"]))
