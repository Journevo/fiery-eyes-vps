"""System Health Dashboard — shows status of all data sources."""
from datetime import datetime, timezone
from db.connection import execute, execute_one
from config import get_logger

log = get_logger("system_health")


def generate_health_dashboard() -> str:
    """Generate system health dashboard."""
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%H:%M UTC")
    lines = []
    lines.append("=== SYSTEM HEALTH — %s ===" % now_str)
    lines.append("")

    # AUTO DATA
    lines.append("AUTO DATA:")

    # YouTube
    try:
        row = execute_one("SELECT COUNT(*) FROM youtube_videos WHERE processed_at > CURRENT_DATE")
        yt_count = row[0] if row else 0
        icon = "+" if yt_count >= 5 else ("!" if yt_count >= 1 else "X")
        lines.append("  [%s] YouTube: %d videos today" % (icon, yt_count))
    except Exception:
        lines.append("  [X] YouTube: DB error")

    # Prices
    try:
        from watchlist import fetch_prices
        # Don't actually fetch — just check last stored
        row = execute_one("SELECT MAX(date) FROM watchlist_daily")
        if row and row[0]:
            lines.append("  [+] Prices: last update %s" % str(row[0]))
        else:
            lines.append("  [!] Prices: no data in DB")
    except Exception:
        lines.append("  [!] Prices: available via CoinGecko")

    # Fear & Greed
    try:
        row = execute_one("SELECT value FROM market_structure ORDER BY date DESC LIMIT 1")
        if row:
            lines.append("  [+] F&G: available")
        else:
            lines.append("  [!] F&G: no data")
    except Exception:
        lines.append("  [!] F&G: via alternative.me")

    # Nimbus sync
    try:
        from nimbus_sync import get_staleness
        s = get_staleness()
        if s["days_stale"] <= 7:
            lines.append("  [+] Nimbus sync: %s (%dd ago)" % (s["as_of_date"], s["days_stale"]))
        else:
            lines.append("  [!] Nimbus sync: %s (%dd stale)" % (s["as_of_date"], s["days_stale"]))
    except Exception:
        lines.append("  [X] Nimbus sync: not available")

    # SunFlow
    try:
        row = execute_one("SELECT COUNT(*) FROM sunflow_signals WHERE created_at > NOW() - INTERVAL '7 days'")
        sf_count = row[0] if row else 0
        row2 = execute_one("SELECT MAX(created_at) FROM sunflow_signals")
        last_sf = str(row2[0])[:16] if row2 and row2[0] else "never"
        lines.append("  [+] SunFlow: %d signals (7d), last %s" % (sf_count, last_sf))
    except Exception:
        lines.append("  [!] SunFlow: listener may be down")

    # DeFi TVL
    try:
        from defi_llama import collect_defi_data
        lines.append("  [+] DeFi TVL: via DeFiLlama")
    except Exception:
        lines.append("  [!] DeFi TVL: import failed")

    # Jingubang
    try:
        import subprocess
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "root@134.209.176.180",
             "systemctl is-active jingubang"],
            capture_output=True, text=True, timeout=10)
        status = result.stdout.strip()
        icon = "+" if status == "active" else "X"
        lines.append("  [%s] Jingubang: %s" % (icon, status))
    except Exception:
        lines.append("  [!] Jingubang: could not check")

    lines.append("")

    # NOT BUILT
    lines.append("NOT BUILT:")
    not_built = [
        "BTC Dominance tracking",
        "Funding rates (Coinglass)",
        "Liquidations (Coinglass)",
        "Open Interest detail",
        "Discovery counter (YT mentions)",
        "Signal accuracy tracking",
        "H wallet on-chain reading",
    ]
    for item in not_built:
        lines.append("  [X] %s" % item)

    return "\n".join(lines)
