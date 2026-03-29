"""unified_views.py — Consolidated single-message views for each main menu button.

Each view produces ONE message (max 4096 chars) with:
- Key signals/implications at the top
- Compact data tables
- Context (comparisons, direction words)
"""

from datetime import datetime, timezone
from db.connection import execute, execute_one
from config import get_logger

log = get_logger("unified_views")


def _pct_i(now, then):
    """Integer percentage change, or dash."""
    if now is None or then is None or then == 0:
        return "-"
    p = ((float(now) - float(then)) / abs(float(then))) * 100
    if abs(p) < 1 and p != 0:
        return "%+.1f%%" % p
    return "%+.0f%%" % p


def _v(val, fmt="%.2f"):
    if val is None:
        return "-"
    v = float(val)
    if abs(v) >= 10000:
        return "%dK" % (v / 1000)
    return fmt % v


def _get(series_key):
    row = execute_one(
        """SELECT current_value, value_1d, value_1w, value_1m, value_3m, value_6m, value_1y,
                  direction, acceleration
           FROM macro_dashboard_cache WHERE series_key = %s""",
        (series_key,))
    if not row:
        return {}
    return {"now": row[0], "1d": row[1], "1w": row[2], "1m": row[3],
            "3m": row[4], "6m": row[5], "1y": row[6], "dir": row[7], "accel": row[8]}


# ═══════════════════════════════════════════════════════════════════
# 🌍 MACRO — consolidated single message
# ═══════════════════════════════════════════════════════════════════
def format_macro_unified() -> str:
    ts = datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")

    vix = _get("VIXCLS")
    hyg = _get("HYG")
    dxy = _get("DX-Y.NYB") or _get("DTWEXBGS")
    us10 = _get("DGS10")
    oil = _get("DCOILBRENTEU") or _get("BZ=F")
    gold = _get("GC=F")
    copper = _get("HG=F")
    gdp = _get("A191RL1Q225SBEA")
    unemp = _get("UNRATE")
    claims = _get("ICSA")
    fed = _get("FEDFUNDS")
    y2 = _get("DGS2")
    spread = _get("T10Y2Y")
    mtg = _get("MORTGAGE30US")
    cpi = _get("CPIAUCSL")
    pce = _get("PCEPILFE")

    # Generate key signals
    signals = []
    if oil.get("now") and float(oil["now"]) > 100:
        signals.append("Oil $%s (%s 3M) — supply shock, Fed can't cut" % (
            _v(oil["now"], "%.0f"), _pct_i(oil["now"], oil.get("3m"))))
    vix_v = float(vix.get("now") or 0)
    if vix_v > 30:
        signals.append("VIX %s (%s 3M) — crisis level" % (_v(vix["now"], "%.0f"), _pct_i(vix["now"], vix.get("3m"))))
    elif vix_v > 25:
        signals.append("VIX %s (%s 3M) — elevated fear, not crisis" % (_v(vix["now"], "%.0f"), _pct_i(vix["now"], vix.get("3m"))))
    if us10.get("now") and us10.get("1m") and float(us10["1m"]) > 0:
        y_pct = ((float(us10["now"]) - float(us10["1m"])) / float(us10["1m"])) * 100
        if y_pct > 5:
            signals.append("10Y yield %s%% rising — headwind for risk assets" % _v(us10["now"], "%.2f"))
    if dxy.get("now") and dxy.get("1m") and float(dxy["1m"]) > 0:
        d_pct = ((float(dxy["now"]) - float(dxy["1m"])) / float(dxy["1m"])) * 100
        if d_pct > 2:
            signals.append("DXY %s rising — dollar strength pressure" % _v(dxy["now"], "%.0f"))
    if gold.get("now") and gold.get("1m"):
        g_pct = ((float(gold["now"]) - float(gold["1m"])) / abs(float(gold["1m"]))) * 100
        if abs(g_pct) > 10:
            word = "crashing" if g_pct < 0 else "surging"
            signals.append("Gold %s 1M — %s" % (_pct_i(gold["now"], gold["1m"]), "liquidation event" if g_pct < 0 else "risk-off flight"))
    if gdp.get("now") and gdp.get("1y") and float(gdp["now"]) < float(gdp["1y"]):
        signals.append("GDP %s%% (was %s%% 1Y ago) — decelerating" % (_v(gdp["now"], "%.1f"), _v(gdp["1y"], "%.1f")))

    # Build message
    lines = ["🌍 <b>MACRO DASHBOARD</b> — %s\n" % ts]

    if signals:
        lines.append("⚡ <b>KEY SIGNALS:</b>")
        for s in signals[:5]:
            lines.append("• %s" % s)
        lines.append("")

    # Risk
    def _row(name, d, fmt="%.1f"):
        return "%-10s %7s %5s %5s %5s %5s" % (
            name, _v(d.get("now"), fmt),
            _pct_i(d.get("now"), d.get("1m")), _pct_i(d.get("now"), d.get("3m")),
            _pct_i(d.get("now"), d.get("6m")), _pct_i(d.get("now"), d.get("1y")))

    lines.append("━━━ RISK ━━━")
    lines.append("<pre>")
    lines.append("%-10s %7s %5s %5s %5s %5s" % ("", "Now", "1M", "3M", "6M", "1Y"))
    lines.append(_row("VIX", vix))
    lines.append(_row("HYG", hyg))
    lines.append(_row("DXY", dxy))
    lines.append(_row("10Y", us10, "%.2f"))
    lines.append(_row("Brent", oil))
    lines.append(_row("Gold", gold, "%.0f"))
    lines.append(_row("Copper", copper, "%.2f"))
    lines.append("</pre>")

    # Economy compact
    lines.append("\n━━━ ECONOMY ━━━")
    gdp_ctx = ""
    if gdp.get("1y"):
        gdp_ctx = " (was %s%% 1Y ago)" % _v(gdp["1y"], "%.1f")
    lines.append("GDP: %s%%%s" % (_v(gdp.get("now"), "%.1f"), gdp_ctx))
    lines.append("Unemployment: %s%% | Claims: %s (300K=recession)" % (
        _v(unemp.get("now"), "%.1f"),
        _v(claims.get("now"), "%.0f", ) if claims.get("now") and float(claims["now"]) > 100 else _v(claims.get("now"), "%.0f")))

    # Yields compact
    lines.append("\n━━━ YIELDS ━━━")
    cpi_yoy = _pct_i(cpi.get("now"), cpi.get("1y")) if cpi.get("1y") else "-"
    pce_yoy = _pct_i(pce.get("now"), pce.get("1y")) if pce.get("1y") else "-"
    lines.append("CPI YoY: %s | Core PCE: %s" % (cpi_yoy, pce_yoy))
    lines.append("Fed: %s%% | 10Y: %s%% | 2Y: %s%%" % (
        _v(fed.get("now"), "%.2f"), _v(us10.get("now"), "%.2f"), _v(y2.get("now"), "%.2f")))
    lines.append("Spread: %s | Mortgage: %s%%" % (
        _v(spread.get("now"), "%+.2f"), _v(mtg.get("now"), "%.2f")))

    # Global compact
    us_r = _get("FEDFUNDS")
    uk_r = _get("IUDSOIA")
    eu_r = _get("ECBMLFR")
    jp_r = _get("IRSTCB01JPM156N")
    if not jp_r.get("now"):
        jp_r = {"now": 0.50}
    us_10 = _get("DGS10")
    uk_10 = _get("IRLTLT01GBM156N")
    eu_10 = _get("IRLTLT01DEM156N")
    jp_10 = _get("IRLTLT01JPM156N")
    usdjpy = _get("JPY=X")

    lines.append("\n━━━ GLOBAL ━━━")
    lines.append("<pre>")
    lines.append("%-6s %5s %5s %5s" % ("", "Rate", "10Y", "GDP"))
    for flag, name, r, y, g in [
        ("🇺🇸", "US", us_r, us_10, gdp),
        ("🇬🇧", "UK", uk_r, uk_10, _get("NAEXKP01GBQ657S")),
        ("🇪🇺", "EU", eu_r, _get("IRLTLT01DEM156N"), _get("NAEXKP01EZQ657S")),
        ("🇯🇵", "JP", jp_r, jp_10, _get("NAEXKP01JPQ657S")),
    ]:
        lines.append("%s%-4s %5s %5s %5s" % (
            flag, name,
            _v(r.get("now"), "%.1f%%") if r.get("now") else "-",
            _v(y.get("now"), "%.1f%%") if y.get("now") else "-",
            _v(g.get("now"), "%.1f%%") if g.get("now") else "-"))
    lines.append("</pre>")

    jpy_v = float(usdjpy.get("now")) if usdjpy.get("now") else 0
    carry = "STABLE" if jpy_v > 150 else "WARNING" if jpy_v > 145 else "DANGER" if jpy_v > 140 else "CRISIS" if jpy_v else "?"
    lines.append("Carry: USDJPY %s %s (danger &lt;145)" % (_v(usdjpy.get("now"), "%.0f"), carry))

    msg = "\n".join(lines)
    return msg


# ═══════════════════════════════════════════════════════════════════
# ₿ CYCLE — consolidated single message
# ═══════════════════════════════════════════════════════════════════
def format_cycle_unified() -> str:
    ts = datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")

    # Bear progress
    peak = datetime(2025, 10, 6)
    est_bottom = datetime(2026, 10, 6)
    now = datetime.now()
    days_elapsed = (now - peak).days
    total_days = (est_bottom - peak).days
    bear_pct = min(100, max(0, (days_elapsed / total_days) * 100))
    days_remaining = max(0, total_days - days_elapsed)

    # Progress bar
    filled = int(bear_pct / 5)
    bar = "█" * filled + "░" * (20 - filled)

    # Market structure
    fg_val, funding_val, oi_str, dom_str = 50, 0, "?", "?"
    try:
        from market_structure import run_market_structure
        ms = run_market_structure() or {}
        fg = ms.get("fear_greed", {})
        fg_val = int(fg.get("value", 50))
        fg_label = fg.get("label", "?")
        fund = ms.get("funding", {})
        funding_val = float(fund.get("current_pct", 0))
        oi_val = ms.get("open_interest", {}).get("total_usd", 0)
        oi_str = "$%.1fB" % (oi_val / 1e9) if oi_val else "?"
        dom_str = str(ms.get("btc_dominance", "?"))
    except Exception:
        fg_label = "?"

    # BTC price
    btc_price = "?"
    try:
        from watchlist import fetch_prices
        prices = fetch_prices() or {}
        bp = prices.get("BTC", {}).get("price")
        if bp:
            btc_price = "$%s" % "{:,.0f}".format(float(bp))
            btc_pct = ((float(bp) - 126000) / 126000) * 100
    except Exception:
        btc_pct = -47

    # Cycle score
    try:
        from correlation_engine import calculate_cycle_score
        cs = calculate_cycle_score()
    except Exception:
        cs = {"score": 0, "level": 1, "level_name": "?", "deploy_pct": "?", "leverage": "?", "breakdown": {}}

    # CBBI
    cbbi = 54
    try:
        from nimbus_sync import get_nimbus_data
        nd = get_nimbus_data() or {}
        cv = nd.get("crypto", {}).get("cbbi", [])
        if isinstance(cv, list) and cv:
            cbbi = cv[-1]
    except Exception:
        pass

    # Key signals
    signals = []
    signals.append("Bear %d%% complete — bottom est. Oct 2026 (~%dd)" % (bear_pct, days_remaining))
    if fg_val < 15:
        signals.append("F&G %d = bottom 5%% historically = accumulation zone" % fg_val)
    elif fg_val < 30:
        signals.append("F&G %d = fear zone" % fg_val)
    if funding_val < 0:
        signals.append("Negative funding = shorts dominant = contrarian bullish")
    # Headwinds from macro
    oil = _get("DCOILBRENTEU") or _get("BZ=F")
    dxy = _get("DX-Y.NYB")
    headwinds = []
    if oil.get("now") and float(oil["now"]) > 100:
        headwinds.append("oil $%s" % _v(oil["now"], "%.0f"))
    if dxy.get("now") and dxy.get("dir") == "rising":
        headwinds.append("DXY rising")
    if headwinds:
        signals.append("%s headwinds — patience, not FOMO" % " + ".join(headwinds))
    signals.append("Cycle Score %d/100 = L%d: deploy %s, %s" % (
        cs["score"], cs["level"], cs["deploy_pct"], cs["leverage"]))

    # Consensus
    cons = execute_one("SELECT bearish_pct FROM consensus_daily WHERE token = 'BTC' ORDER BY date DESC LIMIT 1")
    bear_cons = int(cons[0]) if cons and cons[0] else 0

    # Correlations compact
    corr_lines = []
    try:
        from correlation_engine import PATTERNS, gather_data
        data = gather_data()
        for pid, p in PATTERNS.items():
            met = sum(1 for c in p["conditions"] if c["check"](data))
            total = len(p["conditions"])
            icon = "🔴" if met/total >= 0.8 else "⚠️" if met/total >= 0.6 else "🟡" if met/total >= 0.4 else "✅"
            short = p["name"].split("(")[0].strip()[:18]
            corr_lines.append("%s %s %d/%d" % (icon, short, met, total))
    except Exception:
        pass

    # Build
    lines = ["₿ <b>CYCLE INTELLIGENCE</b> — %s\n" % ts]
    lines.append("⚡ <b>WHAT THIS MEANS:</b>")
    for s in signals[:5]:
        lines.append("• %s" % s)
    lines.append("")

    lines.append("━━━ BTC CYCLE ━━━")
    lines.append("Peak: $126K (Oct 6) | Now: %s (%+.0f%%)" % (btc_price, btc_pct))
    lines.append("Bear: %s %d%%" % (bar, bear_pct))
    lines.append("~%d days to est. bottom | CBBI: %s" % (days_remaining, cbbi))
    lines.append("")

    lines.append("━━━ MARKET STRUCTURE ━━━")
    lines.append("F&G: %d (%s) | Funding: %s%%" % (fg_val, fg_label, _v(funding_val, "%.4f")))
    lines.append("OI: %s | Dominance: %s%%" % (oi_str, dom_str))
    lines.append("")

    lines.append("━━━ CYCLE SCORE: %d/100 → L%d (%s) ━━━" % (cs["score"], cs["level"], cs["level_name"]))
    ctx_map = {"Business Cycle": "GDP", "Liquidity": "US/global", "BTC Cycle": "bear %d%%" % bear_pct,
               "Market Structure": "F&G %d" % fg_val, "Macro Triggers": "oil+yields", "Token Signals": "consensus"}
    for layer, pts in cs.get("breakdown", {}).items():
        mx = 20 if layer in ("Business Cycle", "Liquidity", "BTC Cycle", "Market Structure") else 10
        lines.append("  %-18s %2d/%d  (%s)" % (layer, pts, mx, ctx_map.get(layer, "")))
    lines.append("Deploy: %s | Leverage: %s" % (cs["deploy_pct"], cs["leverage"]))

    if bear_cons:
        lines.append("\n━━━ CONSENSUS ━━━")
        lines.append("BTC: %d%% bearish" % bear_cons)

    if corr_lines:
        lines.append("\n━━━ CORRELATIONS ━━━")
        for i in range(0, len(corr_lines), 2):
            pair = corr_lines[i:i+2]
            lines.append("  ".join("%-22s" % c for c in pair))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 🪙 TOKENS — consolidated single message
# ═══════════════════════════════════════════════════════════════════
def format_tokens_unified() -> str:
    ts = datetime.now(timezone.utc).strftime("%b %d")

    # Watchlist prices
    try:
        from watchlist import fetch_prices
        prices = fetch_prices() or {}
    except Exception:
        prices = {}

    # SunFlow data
    sf_data = {}
    try:
        rows = execute(
            "SELECT token, conviction_score, net_flow_usd FROM sunflow_conviction ORDER BY conviction_score DESC",
            fetch=True)
        for r in rows:
            sf_data[r[0]] = {"conv": r[1], "flow": r[2]}
    except Exception:
        pass

    # Token scores
    scores = {}
    try:
        score_rows = execute(
            "SELECT token, total_score FROM token_scores WHERE date = (SELECT MAX(date) FROM token_scores)",
            fetch=True)
        for r in score_rows:
            scores[r[0]] = int(round(float(r[1]) * 6)) if r[1] else 0
    except Exception:
        pass

    # ISA proxies from macro cache
    mstr = _get("MSTR")
    coin = _get("COIN")

    watchlist = ["BTC", "SOL", "HYPE", "JUP", "RENDER", "BONK", "PUMP", "PENGU", "FARTCOIN"]

    lines = ["🪙 <b>TOKEN INTELLIGENCE</b> — %s\n" % ts]
    lines.append("<pre>")
    lines.append("%-8s %9s %5s %5s  %4s %3s" % ("Token", "Price", "24h", "%ATH", "SF", "Scr"))

    for token in watchlist:
        p = prices.get(token, {})
        price = p.get("price", 0)
        change = p.get("change_24h", 0)
        ath_pct = p.get("ath_change_pct", 0)

        if price:
            if float(price) >= 100:
                price_str = "$%s" % "{:,.0f}".format(float(price))
            elif float(price) >= 1:
                price_str = "$%.2f" % float(price)
            elif float(price) >= 0.001:
                price_str = "$%.4f" % float(price)
            else:
                price_str = "$%.2e" % float(price)
        else:
            price_str = "-"

        sf = sf_data.get(token, {})
        sf_str = "%.1f" % sf["conv"] if sf.get("conv") else "-"
        score = scores.get(token, 0)
        score_str = str(score) if score else "-"
        chg_str = "%+.0f%%" % float(change) if change else "-"
        ath_str = "%+.0f%%" % float(ath_pct) if ath_pct else "-"

        lines.append("%-8s %9s %5s %5s  %4s %3s" % (
            token, price_str[:9], chg_str, ath_str, sf_str, score_str))

    lines.append("</pre>")

    # ISA
    mstr_p = "$%.0f" % float(mstr["now"]) if mstr.get("now") else "?"
    coin_p = "$%.0f" % float(coin["now"]) if coin.get("now") else "?"
    lines.append("ISA: MSTR %s | COIN %s" % (mstr_p, coin_p))

    # Signals
    sigs = []
    for token in watchlist:
        sf = sf_data.get(token, {})
        if sf.get("conv") and float(sf["conv"]) >= 7:
            flow_str = ""
            if sf.get("flow"):
                flow_str = ", $%.1fM flow" % (abs(float(sf["flow"])) / 1e6)
            sigs.append("%s: SunFlow %.1f conviction%s" % (token, sf["conv"], flow_str))

    if sigs:
        lines.append("\n⚡ <b>SIGNALS:</b>")
        for s in sigs[:4]:
            lines.append("• %s" % s)

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 🧠 INTEL — consolidated single message
# ═══════════════════════════════════════════════════════════════════
def format_intel_unified() -> str:
    ts = datetime.now(timezone.utc).strftime("%b %d")

    # Video count today
    row = execute_one("SELECT COUNT(*) FROM youtube_videos WHERE processed_at >= CURRENT_DATE")
    count = row[0] if row else 0

    # Consensus
    cons = execute_one(
        "SELECT bullish_pct, bearish_pct FROM consensus_daily WHERE token = 'BTC' ORDER BY date DESC LIMIT 1")
    bull = int(cons[0]) if cons and cons[0] else 0
    bear = int(cons[1]) if cons and cons[1] else 0
    neut = max(0, 100 - bull - bear)

    # Top voices today (Sonnet analyses = essay_format in analysis_json)
    voices = execute(
        """SELECT channel_name, title, analysis_json
           FROM youtube_videos
           WHERE processed_at >= CURRENT_DATE
           AND analysis_json IS NOT NULL
           ORDER BY relevance_score DESC NULLS LAST, processed_at DESC
           LIMIT 5""",
        fetch=True)

    # Claims count
    claims_row = execute_one("SELECT COUNT(*) FROM voice_claims WHERE status = 'PENDING'")
    claims_count = claims_row[0] if claims_row else 0
    repeated_row = execute_one(
        "SELECT COUNT(*) FROM voice_claims WHERE status = 'PENDING' AND times_repeated > 1")
    repeated = repeated_row[0] if repeated_row else 0

    lines = ["🧠 <b>INTELLIGENCE</b> — %s\n" % ts]

    if count:
        lines.append("📺 %d videos processed today" % count)
    else:
        lines.append("📺 No videos yet today")

    if bear or bull:
        lines.append("Outlook: 🔴 %d%% bearish | 🟢 %d%% bullish | ⚪ %d%% neutral" % (bear, bull, neut))

    if voices:
        lines.append("\n🔑 <b>KEY VOICES:</b>")
        for ch, title, aj in voices:
            # Extract first sentence of summary
            summary = ""
            if aj:
                if isinstance(aj, dict):
                    summary = aj.get("summary", "")
                elif isinstance(aj, str):
                    try:
                        import json
                        d = json.loads(aj)
                        summary = d.get("summary", "")
                    except Exception:
                        summary = aj
            # First meaningful sentence
            if summary:
                for sent in summary.replace("\n", ". ").split(". "):
                    sent = sent.strip()
                    if len(sent) > 30:
                        summary = sent[:80]
                        break
                else:
                    summary = summary[:80]
            lines.append("• %s: %s" % (ch, summary or title[:60]))

    lines.append("\n📋 Claims: %d active%s" % (
        claims_count, " | %d repeated" % repeated if repeated else ""))
    lines.append("\n/digest for full dump | /voices for accuracy")

    return "\n".join(lines)
