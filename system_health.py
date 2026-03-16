"""system_health.py — System health dashboard with emojis."""
from datetime import datetime, timezone
from db.connection import execute, execute_one
from config import get_logger

log = get_logger("system_health")


def generate_health_dashboard():
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%H:%M UTC")
    lines = []
    lines.append("\u2699\ufe0f <b>SYSTEM HEALTH</b> \u2014 %s" % now_str)
    lines.append("")
    lines.append("\U0001f4ca <b>AUTO DATA</b>")

    # YouTube
    try:
        row = execute_one("SELECT COUNT(*) FROM youtube_videos WHERE processed_at > CURRENT_DATE")
        yt_count = row[0] if row else 0
        icon = "\u2705" if yt_count >= 5 else ("\u26a0\ufe0f" if yt_count >= 1 else "\u274c")
        lines.append("  %s YouTube: %d videos today" % (icon, yt_count))
    except Exception:
        lines.append("  \u274c YouTube: DB error")

    # Prices
    try:
        from watchlist import fetch_prices
        lines.append("  \u2705 Prices: CoinGecko available")
    except Exception:
        lines.append("  \u26a0\ufe0f Prices: import failed")

    # F&G
    try:
        from market_structure import run_market_structure
        lines.append("  \u2705 F&G: alternative.me available")
    except Exception:
        lines.append("  \u26a0\ufe0f F&G: unavailable")

    # Nimbus sync
    try:
        from nimbus_sync import get_staleness
        s = get_staleness()
        if s["days_stale"] <= 7:
            lines.append("  \u2705 Nimbus: %s (%dd ago)" % (s["as_of_date"], s["days_stale"]))
        else:
            lines.append("  \u26a0\ufe0f Nimbus: %s (%dd stale)" % (s["as_of_date"], s["days_stale"]))
    except Exception:
        lines.append("  \u274c Nimbus: not synced")

    # SunFlow
    try:
        row = execute_one("SELECT COUNT(*) FROM sunflow_signals WHERE created_at > NOW() - INTERVAL '7 days'")
        sf = row[0] if row else 0
        row2 = execute_one("SELECT MAX(created_at) FROM sunflow_signals")
        last = str(row2[0])[:16] if row2 and row2[0] else "never"
        icon = "\u2705" if sf > 0 else "\u26a0\ufe0f"
        lines.append("  %s SunFlow: %d signals (7d), last %s" % (icon, sf, last))
    except Exception:
        lines.append("  \u26a0\ufe0f SunFlow: unknown")

    # DeFi
    lines.append("  \u2705 DeFi TVL: DeFiLlama available")

    # Jingubang
    try:
        import subprocess
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=5", "root@134.209.176.180", "systemctl is-active jingubang"],
            capture_output=True, text=True, timeout=10)
        status = result.stdout.strip()
        icon = "\u2705 \U0001f916" if status == "active" else "\u274c"
        lines.append("  %s Jingubang: %s" % (icon, status))
    except Exception:
        lines.append("  \u26a0\ufe0f Jingubang: could not check")

    # YouTube watchdog
    try:
        from youtube_watchdog import format_heartbeat_line
        lines.append("  %s" % format_heartbeat_line())
    except Exception:
        pass

    lines.append("")
    lines.append("\U0001f6a7 <b>NOT BUILT</b>")
    not_built = [
        "BTC Dominance tracking",
        "Funding rates (Coinglass)",
        "Liquidations (Coinglass)",
        "Open Interest detail",
        "Discovery counter",
        "Signal accuracy tracking",
        "H wallet on-chain reading",
    ]
    for item in not_built:
        lines.append("  \U0001f6a7 %s" % item)

    return "\n".join(lines)
