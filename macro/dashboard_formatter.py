"""macro/dashboard_formatter.py — Format macro data into Telegram dashboard messages."""

from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_logger
from db.connection import execute, execute_one
from macro.config import DIRECTION_SENTIMENT

log = get_logger("macro.dashboard")


def _get(series_key: str) -> dict:
    """Get dashboard cache entry for a series."""
    row = execute_one(
        """SELECT current_value, value_1d, value_1w, value_1m, value_3m, value_6m, value_1y,
                  direction, acceleration, as_of_date
           FROM macro_dashboard_cache WHERE series_key = %s""",
        (series_key,),
    )
    if not row:
        return {}
    return {
        "now": row[0], "1d": row[1], "1w": row[2], "1m": row[3],
        "3m": row[4], "6m": row[5], "1y": row[6],
        "dir": row[7] or "flat", "accel": row[8] or "stable",
        "date": row[9],
    }


def _v(val, fmt="%.2f", scale=1, suffix=""):
    """Format a value, returning '—' if None."""
    if val is None:
        return "—"
    v = float(val) * scale
    if abs(v) >= 10000:
        return "%dK%s" % (v / 1000, suffix)
    return (fmt % v) + suffix


def _pct(now, then):
    """Calculate percentage change."""
    if now is None or then is None or then == 0:
        return "—"
    p = ((float(now) - float(then)) / abs(float(then))) * 100
    return "%+.1f%%" % p


def _trend(d):
    """Direction + acceleration emoji."""
    direction = d.get("dir", "flat")
    accel = d.get("accel", "stable")
    series_key = ""  # Not needed for emoji
    if direction == "rising":
        base = "📈"
    elif direction == "falling":
        base = "📉"
    else:
        return "➡️"
    if accel == "accelerating":
        return base + "⚡"
    elif accel == "decelerating":
        return base + "🔻"
    return base


def format_risk_barometer() -> str:
    """Risk barometer + direction section."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vix = _get("VIXCLS")
    move = _get("^VIX")  # May overlap with FRED VIX
    hyg = _get("HYG")
    dxy = _get("DX-Y.NYB")
    if not dxy.get("now"):
        dxy = _get("DTWEXBGS")
    us10 = _get("DGS10")
    oil = _get("DCOILBRENTEU")
    if not oil.get("now"):
        oil = _get("BZ=F")
    gold = _get("GOLDAMGBD228NLBM")
    if not gold.get("now"):
        gold = _get("GC=F")
    copper = _get("HG=F")

    lines = [
        "📊 <b>MACRO DASHBOARD</b>",
        "📅 %s\n" % now_str,
        "━━━ RISK BAROMETER ━━━",
        "<pre>",
        "%-14s %8s %8s %8s %s" % ("", "Now", "1M", "3M", "Trend"),
        "%-14s %8s %8s %8s %s" % ("VIX", _v(vix.get("now"), "%.1f"), _v(vix.get("1m"), "%.1f"), _v(vix.get("3m"), "%.1f"), _trend(vix)),
        "%-14s %8s %8s %8s %s" % ("HYG", _v(hyg.get("now"), "%.1f"), _v(hyg.get("1m"), "%.1f"), _v(hyg.get("3m"), "%.1f"), _trend(hyg)),
        "</pre>\n",
        "━━━ DIRECTION ━━━",
        "<pre>",
        "%-14s %8s %8s %8s %s" % ("", "Now", "1M", "3M", "Trend"),
        "%-14s %8s %8s %8s %s" % ("DXY", _v(dxy.get("now"), "%.1f"), _v(dxy.get("1m"), "%.1f"), _v(dxy.get("3m"), "%.1f"), _trend(dxy)),
        "%-14s %8s %8s %8s %s" % ("US 10Y", _v(us10.get("now")), _v(us10.get("1m")), _v(us10.get("3m")), _trend(us10)),
        "%-14s %8s %8s %8s %s" % ("Oil Brent", _v(oil.get("now"), "%.1f"), _v(oil.get("1m"), "%.1f"), _v(oil.get("3m"), "%.1f"), _trend(oil)),
        "%-14s %8s %8s %8s %s" % ("Gold", _v(gold.get("now"), "%.0f"), _v(gold.get("1m"), "%.0f"), _v(gold.get("3m"), "%.0f"), _trend(gold)),
        "%-14s %8s %8s %8s %s" % ("Copper", _v(copper.get("now"), "%.2f"), _v(copper.get("1m"), "%.2f"), _v(copper.get("3m"), "%.2f"), _trend(copper)),
        "</pre>",
    ]
    return "\n".join(lines)


def format_us_economy() -> str:
    """US economy section."""
    gdp = _get("A191RL1Q225SBEA")
    unemp = _get("UNRATE")
    claims = _get("ICSA")
    nfp = _get("PAYEMS")
    jo = _get("JTSJOL")
    sent = _get("UMCSENT")
    housing = _get("HOUST")

    lines = [
        "━━━ 🇺🇸 US ECONOMY ━━━",
        "<pre>",
        "%-16s %7s %7s %7s %7s %s" % ("", "Now", "1M", "3M", "1Y", "Trend"),
        "%-16s %7s %7s %7s %7s %s" % ("GDP Growth", _v(gdp.get("now"), "%.1f", suffix="%"), _v(gdp.get("1m"), "%.1f"), _v(gdp.get("3m"), "%.1f"), _v(gdp.get("1y"), "%.1f"), _trend(gdp)),
        "%-16s %7s %7s %7s %7s %s" % ("Unemployment", _v(unemp.get("now"), "%.1f", suffix="%"), _v(unemp.get("1m"), "%.1f"), _v(unemp.get("3m"), "%.1f"), _v(unemp.get("1y"), "%.1f"), _trend(unemp)),
        "%-16s %7s %7s %7s %7s %s" % ("Jobless Claims", _v(claims.get("now"), "%.0f", scale=0.001, suffix="K"), _v(claims.get("1m"), "%.0f", scale=0.001, suffix="K"), _v(claims.get("3m"), "%.0f", scale=0.001, suffix="K"), _v(claims.get("1y"), "%.0f", scale=0.001, suffix="K"), _trend(claims)),
        "%-16s %7s %7s %7s %7s %s" % ("Nonfarm Payroll", _v(nfp.get("now"), "%.0f", scale=0.001, suffix="K"), _v(nfp.get("1m"), "%.0f", scale=0.001, suffix="K"), _v(nfp.get("3m"), "%.0f", scale=0.001, suffix="K"), _v(nfp.get("1y"), "%.0f", scale=0.001, suffix="K"), _trend(nfp)),
        "%-16s %7s %7s %7s %7s %s" % ("Job Openings", _v(jo.get("now"), "%.0f", scale=0.001, suffix="K"), _v(jo.get("1m"), "%.0f", scale=0.001, suffix="K"), _v(jo.get("3m"), "%.0f", scale=0.001, suffix="K"), _v(jo.get("1y"), "%.0f", scale=0.001, suffix="K"), _trend(jo)),
        "%-16s %7s %7s %7s %7s %s" % ("Consumer Sent", _v(sent.get("now"), "%.1f"), _v(sent.get("1m"), "%.1f"), _v(sent.get("3m"), "%.1f"), _v(sent.get("1y"), "%.1f"), _trend(sent)),
        "%-16s %7s %7s %7s %7s %s" % ("Housing Starts", _v(housing.get("now"), "%.0f", scale=0.001, suffix="K"), _v(housing.get("1m"), "%.0f", scale=0.001, suffix="K"), _v(housing.get("3m"), "%.0f", scale=0.001, suffix="K"), _v(housing.get("1y"), "%.0f", scale=0.001, suffix="K"), _trend(housing)),
        "</pre>",
    ]
    return "\n".join(lines)


def format_inflation_yields() -> str:
    """Inflation & yields section."""
    # For CPI/PCE indices, compute YoY %
    cpi = _get("CPIAUCSL")
    pce = _get("PCEPILFE")
    fed = _get("FEDFUNDS")
    y2 = _get("DGS2")
    y5 = _get("DGS5")
    y10 = _get("DGS10")
    y30 = _get("DGS30")
    spread = _get("T10Y2Y")
    mtg = _get("MORTGAGE30US")

    # CPI/PCE YoY
    cpi_yoy = _pct(cpi.get("now"), cpi.get("1y")) if cpi.get("1y") else _v(cpi.get("now"))
    pce_yoy = _pct(pce.get("now"), pce.get("1y")) if pce.get("1y") else _v(pce.get("now"))

    lines = [
        "━━━ INFLATION & YIELDS ━━━",
        "<pre>",
        "%-16s %7s %7s %7s %7s %s" % ("", "Now", "1M", "3M", "1Y", "Trend"),
        "%-16s %7s %7s %7s %7s %s" % ("CPI YoY", cpi_yoy, _pct(cpi.get("1m"), cpi.get("1y")), "", "", _trend(cpi)),
        "%-16s %7s %7s %7s %7s %s" % ("Core PCE YoY", pce_yoy, _pct(pce.get("1m"), pce.get("1y")), "", "", _trend(pce)),
        "%-16s %7s %7s %7s %7s %s" % ("Fed Rate", _v(fed.get("now"), "%.2f", suffix="%"), _v(fed.get("1m"), "%.2f"), _v(fed.get("3m"), "%.2f"), _v(fed.get("1y"), "%.2f"), _trend(fed)),
        "%-16s %7s %7s %7s %7s %s" % ("US 2Y", _v(y2.get("now"), "%.2f", suffix="%"), _v(y2.get("1m"), "%.2f"), _v(y2.get("3m"), "%.2f"), _v(y2.get("1y"), "%.2f"), _trend(y2)),
        "%-16s %7s %7s %7s %7s %s" % ("US 5Y", _v(y5.get("now"), "%.2f", suffix="%"), _v(y5.get("1m"), "%.2f"), _v(y5.get("3m"), "%.2f"), _v(y5.get("1y"), "%.2f"), _trend(y5)),
        "%-16s %7s %7s %7s %7s %s" % ("US 10Y", _v(y10.get("now"), "%.2f", suffix="%"), _v(y10.get("1m"), "%.2f"), _v(y10.get("3m"), "%.2f"), _v(y10.get("1y"), "%.2f"), _trend(y10)),
        "%-16s %7s %7s %7s %7s %s" % ("US 30Y", _v(y30.get("now"), "%.2f", suffix="%"), _v(y30.get("1m"), "%.2f"), _v(y30.get("3m"), "%.2f"), _v(y30.get("1y"), "%.2f"), _trend(y30)),
        "%-16s %7s %7s %7s %7s %s" % ("2Y/10Y Spread", _v(spread.get("now"), "%+.2f"), _v(spread.get("1m"), "%+.2f"), _v(spread.get("3m"), "%+.2f"), _v(spread.get("1y"), "%+.2f"), _trend(spread)),
        "%-16s %7s %7s %7s %7s %s" % ("30Y Mortgage", _v(mtg.get("now"), "%.2f", suffix="%"), _v(mtg.get("1m"), "%.2f"), _v(mtg.get("3m"), "%.2f"), _v(mtg.get("1y"), "%.2f"), _trend(mtg)),
        "</pre>",
    ]
    return "\n".join(lines)


def format_global_comparison() -> str:
    """Global comparison + carry trade."""
    us_r = _get("FEDFUNDS")
    uk_r = _get("BOERUKM")
    eu_r = _get("ECBMLFR")
    jp_r = _get("IRSTCB01JPM156N")

    us_10 = _get("DGS10")
    uk_10 = _get("IRLTLT01GBM156N")
    eu_10 = _get("IRLTLT01DEM156N")
    jp_10 = _get("IRLTLT01JPM156N")

    us_u = _get("UNRATE")
    uk_u = _get("LRHUTTTTGBM156S")
    eu_u = _get("LRHUTTTTEZM156S")
    jp_u = _get("LRHUTTTTJPM156S")

    usdjpy = _get("JPY=X")
    nikkei = _get("^N225")

    # Carry trade status
    jpy_val = float(usdjpy.get("now")) if usdjpy.get("now") else None
    if jpy_val and jpy_val > 150:
        carry = "✅ STABLE"
    elif jpy_val and jpy_val > 145:
        carry = "⚠️ WARNING"
    elif jpy_val and jpy_val > 140:
        carry = "🔴 UNWIND RISK"
    elif jpy_val:
        carry = "🔴🔴 CRISIS"
    else:
        carry = "?"

    fed_v = float(us_r.get("now")) if us_r.get("now") else 0
    boj_v = float(jp_r.get("now")) if jp_r.get("now") else 0
    gap = "%.2f%%" % (fed_v - boj_v) if fed_v and boj_v else "?"

    lines = [
        "━━━ 🌍 GLOBAL COMPARISON ━━━",
        "<pre>",
        "%-8s %6s %6s %6s %6s" % ("", "Rate", "10Y", "Unemp", "Trend"),
        "%-8s %6s %6s %6s" % ("🇺🇸 US", _v(us_r.get("now"), "%.2f%%"), _v(us_10.get("now"), "%.2f%%"), _v(us_u.get("now"), "%.1f%%")),
        "%-8s %6s %6s %6s" % ("🇬🇧 UK", _v(uk_r.get("now"), "%.2f%%"), _v(uk_10.get("now"), "%.2f%%"), _v(uk_u.get("now"), "%.1f%%")),
        "%-8s %6s %6s %6s" % ("🇪🇺 EU", _v(eu_r.get("now"), "%.2f%%"), _v(eu_10.get("now"), "%.2f%%"), _v(eu_u.get("now"), "%.1f%%")),
        "%-8s %6s %6s %6s" % ("🇯🇵 JP", _v(jp_r.get("now"), "%.2f%%"), _v(jp_10.get("now"), "%.2f%%"), _v(jp_u.get("now"), "%.1f%%")),
        "</pre>\n",
        "━━━ 🇯🇵 CARRY TRADE ━━━",
        "BOJ: %s%% | Fed: %s%% | Gap: %s" % (_v(jp_r.get("now"), "%.2f"), _v(us_r.get("now"), "%.2f"), gap),
        "USD/JPY: %s  %s" % (_v(usdjpy.get("now"), "%.1f"), carry),
        "Nikkei: %s" % _v(nikkei.get("now"), "%.0f"),
    ]
    return "\n".join(lines)


def format_commodities_currencies() -> str:
    """Commodities + currencies."""
    gold = _get("GC=F")
    silver = _get("SI=F")
    plat = _get("PL=F")
    copper = _get("HG=F")
    wti = _get("CL=F")
    brent = _get("BZ=F")
    gas = _get("NG=F")

    dxy = _get("DX-Y.NYB")
    eur = _get("EURUSD=X")
    gbp = _get("GBPUSD=X")
    jpy = _get("JPY=X")
    cny = _get("CNY=X")
    aud = _get("AUDUSD=X")

    def _row(name, d, fmt="%.2f"):
        return "%-14s %8s %8s %8s %8s %s" % (
            name, _v(d.get("now"), fmt), _pct(d.get("now"), d.get("1w")),
            _pct(d.get("now"), d.get("1m")), _pct(d.get("now"), d.get("3m")), _trend(d))

    lines = [
        "━━━ COMMODITIES ━━━",
        "<pre>",
        "%-14s %8s %8s %8s %8s %s" % ("", "Now", "1W", "1M", "3M", ""),
        _row("Gold", gold, "%.0f"),
        _row("Silver", silver),
        _row("Platinum", plat, "%.0f"),
        _row("Copper", copper),
        _row("WTI Crude", wti),
        _row("Brent Crude", brent),
        _row("Nat Gas", gas),
        "</pre>\n",
        "━━━ CURRENCIES ━━━",
        "<pre>",
        "%-14s %8s %8s %8s %8s %s" % ("", "Now", "1W", "1M", "3M", ""),
        _row("DXY", dxy, "%.1f"),
        _row("EUR/USD", eur, "%.4f"),
        _row("GBP/USD", gbp, "%.4f"),
        _row("USD/JPY", jpy, "%.1f"),
        _row("USD/CNY", cny, "%.2f"),
        _row("AUD/USD", aud, "%.4f"),
        "</pre>",
    ]
    return "\n".join(lines)


def format_indices_stocks() -> str:
    """Indices + key stocks."""
    spx = _get("^GSPC")
    ndx = _get("^NDX")
    dji = _get("^DJI")
    rut = _get("^RUT")
    ftse = _get("^FTSE")
    nik = _get("^N225")
    hsi = _get("^HSI")

    nvda = _get("NVDA")
    tsla = _get("TSLA")
    mstr = _get("MSTR")
    coin = _get("COIN")
    kre = _get("KRE")
    tlt = _get("TLT")
    hyg = _get("HYG")

    def _row(name, d, fmt="%.0f"):
        return "%-14s %8s %8s %8s %8s %s" % (
            name, _v(d.get("now"), fmt), _pct(d.get("now"), d.get("1w")),
            _pct(d.get("now"), d.get("1m")), _pct(d.get("now"), d.get("3m")), _trend(d))

    lines = [
        "━━━ INDICES ━━━",
        "<pre>",
        "%-14s %8s %8s %8s %8s %s" % ("", "Now", "1W", "1M", "3M", ""),
        _row("S&P 500", spx),
        _row("Nasdaq 100", ndx),
        _row("Dow Jones", dji),
        _row("Russell 2000", rut),
        _row("FTSE 100", ftse),
        _row("Nikkei 225", nik),
        _row("Hang Seng", hsi),
        "</pre>\n",
        "━━━ KEY STOCKS ━━━",
        "<pre>",
        "%-14s %8s %8s %8s %8s %s" % ("", "Now", "1W", "1M", "3M", ""),
        _row("NVDA", nvda, "%.1f"),
        _row("TSLA", tsla, "%.1f"),
        _row("MSTR", mstr, "%.1f"),
        _row("COIN", coin, "%.1f"),
        _row("KRE (Banks)", kre, "%.1f"),
        _row("TLT (Bonds)", tlt, "%.1f"),
        _row("HYG (Junk)", hyg, "%.1f"),
        "</pre>",
    ]
    return "\n".join(lines)


def format_macro_changes() -> str:
    """Overnight changes for morning brief."""
    rows = execute(
        """SELECT series_key, name, current_value, value_1d
           FROM macro_dashboard_cache
           WHERE value_1d IS NOT NULL AND current_value IS NOT NULL""",
        fetch=True,
    )
    if not rows:
        return "━━━ MACRO ━━━\nNo data yet ⚠️"

    changes = []
    for skey, name, current, prev in rows:
        if prev == 0:
            continue
        pct = abs(float(current) - float(prev)) / abs(float(prev)) * 100
        critical = skey in ("VIXCLS", "^VIX", "DGS10", "DCOILBRENTEU", "BZ=F", "JPY=X", "T10Y2Y")
        if pct > 1.0 or (critical and pct > 0.3):
            arrow = "📈" if float(current) > float(prev) else "📉"
            changes.append((pct, "%s %s: %s → %s (%+.1f%%)" % (
                arrow, name, _v(float(prev)), _v(float(current)), 
                ((float(current) - float(prev)) / abs(float(prev))) * 100)))

    if not changes:
        return "━━━ MACRO ━━━\nNo significant changes overnight ✅"

    changes.sort(key=lambda x: -x[0])
    lines = ["━━━ MACRO CHANGES (overnight) ━━━"]
    for _, line in changes[:10]:
        lines.append("  " + line)
    return "\n".join(lines)


def format_macro_pulse() -> str:
    """One-line macro summary for the Huoyan pulse."""
    vix = _get("VIXCLS")
    us10 = _get("DGS10")
    oil = _get("DCOILBRENTEU")
    if not oil.get("now"):
        oil = _get("BZ=F")
    jpy = _get("JPY=X")
    spx = _get("^GSPC")

    spx_pct = ""
    if spx.get("now") and spx.get("1d") and float(spx.get("1d")) != 0:
        p = ((float(spx["now"]) - float(spx["1d"])) / abs(float(spx["1d"]))) * 100
        spx_pct = "%+.1f%%" % p
    else:
        spx_pct = "—"

    return (
        "━━━ MACRO PULSE ━━━\n"
        "VIX: %s | 10Y: %s%% | Oil: $%s\n"
        "JPY: %s | SPX: %s\n"
        "/macro for full dashboard"
    ) % (
        _v(vix.get("now"), "%.1f"), _v(us10.get("now"), "%.2f"),
        _v(oil.get("now"), "%.0f"), _v(jpy.get("now"), "%.1f"), spx_pct
    )


def format_full_dashboard() -> list[str]:
    """Generate complete macro dashboard as list of Telegram messages."""
    return [
        format_risk_barometer(),
        format_us_economy(),
        format_inflation_yields(),
        format_global_comparison(),
        format_commodities_currencies(),
        format_indices_stocks(),
    ]


def format_section(section: str) -> str:
    """Format a specific dashboard section."""
    section_map = {
        "dashboard": format_risk_barometer,
        "risk": format_risk_barometer,
        "economy": format_us_economy,
        "employment": format_us_economy,
        "yields": format_inflation_yields,
        "inflation": format_inflation_yields,
        "global": format_global_comparison,
        "carry": format_global_comparison,
        "commodities": format_commodities_currencies,
        "currencies": format_commodities_currencies,
        "indices": format_indices_stocks,
        "stocks": format_indices_stocks,
    }
    fn = section_map.get(section.lower())
    if fn:
        return fn()
    return "Unknown section: %s\nAvailable: %s" % (section, ", ".join(sorted(section_map.keys())))
