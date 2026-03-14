"""Daily Intelligence Report — Task 5 of Fiery Eyes v5.1

ONE daily report combining BTC cycle, liquidity, watchlist, and large swaps.
Sent at 00:00 UTC + on-demand via /report command.
"""

import requests
from datetime import datetime, date, timezone, timedelta
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("daily_report")

# Token allocation targets (of deployed capital)
ALLOCATION_TARGETS = {
    "JUP": 25, "HYPE": 20, "RENDER": 17, "BONK": 15,
}

# Estimated beta to BTC (for downside calculation)
BTC_BETA = {
    "JUP": 1.5, "HYPE": 1.2, "RENDER": 1.5, "BONK": 1.8,
    "SOL": 1.3, "MSTR": 1.0, "COIN": 0.8,
}

# BTC $50K scenario drawdown from current ~$70K
BTC_50K_DRAWDOWN = (70000 - 50000) / 70000  # ~28.6%


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    elif price >= 1:
        return f"${price:.2f}"
    elif price >= 0.001:
        return f"${price:.4f}"
    else:
        return f"${price:.2e}"


def _progress_bar(pct: float, length: int = 20) -> str:
    filled = round(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)


def generate_report(send_to_telegram: bool = False) -> str:
    """Generate the complete daily intelligence report."""
    from btc_cycle import fetch_btc_price, calculate_cycle, SCENARIOS
    from watchlist import fetch_prices, calc_mstr_mnav, CORE_TOKENS, ISA_TOKENS, classify_zone
    from liquidity import run_liquidity_tracker
    from large_swaps import run_swap_detection

    today = datetime.now(timezone.utc)
    date_str = today.strftime("%b %-d %Y")

    sections = []
    sections.append(f"🌙 <b>FIERY EYES — {date_str}</b>\n")

    # ━━━ BTC CYCLE ━━━
    btc_price = fetch_btc_price()
    if btc_price:
        cycle = calculate_cycle(btc_price, today.date())
        bar = _progress_bar(cycle["bear_progress_pct"])
        scenarios_str = " | ".join(
            f"-{s['drawdown_pct']}% = ${s['target_price']:,}" for s in SCENARIOS
        )
        peak_date = datetime.strptime(cycle["peak_date"], "%Y-%m-%d").strftime("%b %-d")
        sections.append(
            f"━━━ <b>BTC CYCLE</b> ━━━\n"
            f"Peak: ${cycle['peak_price']:,} ({peak_date}) | Now: ${btc_price:,.0f} (-{cycle['drawdown_pct']}%)\n"
            f"Bear: {bar} {cycle['bear_progress_pct']:.0f}% (~{cycle['days_remaining']}d to est. bottom)\n"
            f"Scenarios: {scenarios_str}"
        )
    else:
        sections.append("━━━ <b>BTC CYCLE</b> ━━━\n⚠️ BTC price unavailable")

    # ━━━ LIQUIDITY ━━━
    liq = run_liquidity_tracker()
    if liq and liq.get("us_net_liq"):
        us_dir = "↗" if liq.get("fred_slope", 0) > 0.05 else ("↘" if liq.get("fred_slope", 0) < -0.05 else "→")
        us_str = f"${liq['us_net_liq']:.2f}T" if liq["us_net_liq"] else "N/A"
        m2_str = f"${liq['global_m2']:.0f}T" if liq.get("global_m2") else "N/A"
        dxy_str = f"{liq['dxy']:.0f}" if liq.get("dxy") else "N/A"
        regime = liq.get("fred_regime", "UNKNOWN")
        slope = liq.get("fred_slope", 0)
        m2_status = liq.get("m2_lag_status", "UNKNOWN")
        m2_days = liq.get("m2_lag_days", 0)
        alignment = liq.get("alignment", "UNKNOWN")

        sections.append(
            f"\n━━━ <b>LIQUIDITY</b> ━━━\n"
            f"US {us_str} ({us_dir}) | M2 {m2_str} | DXY {dxy_str}\n"
            f"FRED: {regime} (slope {slope:+.1f}%) | M2 lag: {m2_status} ({m2_days}d)\n"
            f"Alignment: {alignment}"
        )

    # ━━━ WATCHLIST ━━━
    prices = fetch_prices()
    if prices:
        watchlist_lines = []
        header = f"{'Token':<8s} {'Price':>9s}  {'%ATH':>5s}  {'Zone':<10s}  {'Down':>5s}"
        watchlist_lines.append(header)

        # BTC row
        if "BTC" in prices and btc_price:
            d = prices["BTC"]
            down_pct = round(BTC_50K_DRAWDOWN * 100)
            watchlist_lines.append(
                f"{'BTC':<8s} {_fmt_price(btc_price):>9s}  {d['pct_from_ath']:+.0f}%  {'Bear ' + str(round(cycle['bear_progress_pct'])) + '%':<10s}  -{down_pct}%"
            )

        # SOL row
        if "SOL" in prices:
            d = prices["SOL"]
            down = round(BTC_50K_DRAWDOWN * BTC_BETA.get("SOL", 1.3) * 100)
            watchlist_lines.append(
                f"{'SOL':<8s} {_fmt_price(d['price']):>9s}  {d['pct_from_ath']:+.0f}%  {d['zone']:<10s}  -{down}%"
            )

        # Core tokens
        for symbol in CORE_TOKENS:
            if symbol not in prices:
                continue
            d = prices[symbol]
            beta = BTC_BETA.get(symbol, 1.5)
            down = round(BTC_50K_DRAWDOWN * beta * 100)
            watchlist_lines.append(
                f"{symbol:<8s} {_fmt_price(d['price']):>9s}  {d['pct_from_ath']:+.0f}%  {d['zone']:<10s}  -{down}%"
            )

        sections.append("\n━━━ <b>WATCHLIST</b> ━━━\n<pre>" + "\n".join(watchlist_lines) + "</pre>")

        # ━━━ ISA PROXIES ━━━
        isa_parts = []
        for symbol in ISA_TOKENS:
            if symbol in prices:
                d = prices[symbol]
                part = f"{symbol}: {_fmt_price(d['price'])}"
                if symbol == "MSTR" and "BTC" in prices:
                    mnav = calc_mstr_mnav(d["price"], prices["BTC"]["price"], d["mcap"])
                    if mnav:
                        warn = " ⚠️ near book" if mnav < 1.2 else ""
                        part += f" (mNAV {mnav}{warn})"
                isa_parts.append(part)

        if isa_parts:
            sections.append("\n━━━ <b>ISA PROXIES</b> ━━━\n" + " | ".join(isa_parts))

    # ━━━ LARGE SWAPS (24h) ━━━
    recent_swaps = execute("""
        SELECT token, direction, amount_usd, pct_of_mcap, pool, alert_type
        FROM large_swaps
        WHERE timestamp > NOW() - INTERVAL '24 hours'
        ORDER BY amount_usd DESC
        LIMIT 5
    """, fetch=True)

    if recent_swaps:
        swap_lines = []
        for row in recent_swaps:
            token, direction, amount, pct_mcap, pool, alert_type = row
            amount_str = f"${amount/1e6:.1f}M" if amount >= 1e6 else f"${amount/1e3:.0f}K"
            swap_lines.append(f"{token}: {amount_str} {direction.lower()} on {pool} ({pct_mcap:.2f}% MCap) — {alert_type}")
        sections.append("\n━━━ <b>LARGE SWAPS (24h)</b> ━━━\n" + "\n".join(swap_lines))

    # ━━━ REGIME ━━━
    bear_pct = cycle["bear_progress_pct"] if btc_price else 0
    if bear_pct < 60:
        deploy = "40-50% max"
        dry = "50-60%"
    else:
        deploy = "60-70%"
        dry = "30-40%"

    cycle_says = "accumulate slowly, save for $50-60K BTC zone" if bear_pct < 60 else "nearing bottom, increase exposure"
    macro_says = "early expansion building" if liq and liq.get("fred_regime") == "EXPANDING" else "uncertain direction"

    sections.append(
        f"\n━━━ <b>REGIME</b> ━━━\n"
        f"Deploy: {deploy} | Dry powder: {dry}\n"
        f"Cycle says: {cycle_says}\n"
        f"Macro says: {macro_says}"
    )

    # ━━━ RECOMMENDATION ━━━
    # Determine recommendations based on data
    recs = []
    if prices:
        # Find deepest value core tokens
        core_by_depth = sorted(
            [(s, prices[s]) for s in CORE_TOKENS if s in prices],
            key=lambda x: x[1]["pct_from_ath"]
        )
        if core_by_depth:
            focus = core_by_depth[0]
            recs.append(f"FOCUS: {focus[0]} (deepest value at {focus[1]['pct_from_ath']:+.0f}% ATH)")

        if len(core_by_depth) > 1:
            watch = core_by_depth[1]
            recs.append(f"WATCH: {watch[0]} ({watch[1]['pct_from_ath']:+.0f}% ATH)")

        # Tokens in Mid range = patience
        mid_tokens = [s for s, d in prices.items() if s in CORE_TOKENS and "Mid" in d.get("zone", "")]
        for t in mid_tokens:
            recs.append(f"PATIENCE: {t} (wait for deeper pullback or cycle progress >60%)")

    if bear_pct < 50:
        recs.append("AVOID: adding new positions until bear >50%")

    if recs:
        sections.append("\n━━━ <b>RECOMMENDATION</b> ━━━\n" + "\n".join(recs))

    report = "\n".join(sections)

    log.info("Daily report generated (%d chars)", len(report))

    if send_to_telegram:
        send_telegram(report)

    return report


def send_telegram(text: str):
    """Send report to Telegram, splitting if needed (4096 char limit)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Split into chunks if too long
    max_len = 4000
    chunks = []
    if len(text) <= max_len:
        chunks = [text]
    else:
        # Split at section boundaries
        parts = text.split("\n━━━")
        current = parts[0]
        for part in parts[1:]:
            candidate = current + "\n━━━" + part
            if len(candidate) > max_len:
                chunks.append(current)
                current = "━━━" + part
            else:
                current = candidate
        if current:
            chunks.append(current)

    for chunk in chunks:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=15)
            if resp.status_code == 200:
                log.info("Telegram chunk sent (%d chars)", len(chunk))
            else:
                log.error("Telegram send failed: %s %s", resp.status_code, resp.text)
        except Exception as e:
            log.error("Telegram error: %s", e)


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    report = generate_report(send_to_telegram=send_tg)
    print(report)
