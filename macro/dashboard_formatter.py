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
    """Format a value, returning dash if None."""
    if val is None:
        return "-"
    v = float(val) * scale
    if abs(v) >= 10000:
        return "%dK%s" % (v / 1000, suffix)
    return (fmt % v) + suffix


def _pct(now, then):
    """Percentage change between two values."""
    if now is None or then is None or then == 0:
        return "-"
    p = ((float(now) - float(then)) / abs(float(then))) * 100
    return "%+.1f%%" % p


def _trend(d):
    """Direction + acceleration emoji."""
    direction = d.get("dir", "flat")
    accel = d.get("accel", "stable")
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


# ---------------------------------------------------------------------------
# RISK BAROMETER (fast-moving: Now | 1W | 1M | 3M | 6M)
# ---------------------------------------------------------------------------
def format_risk_barometer() -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    vix = _get("VIXCLS")
    hyg = _get("HYG")
    dxy = _get("DX-Y.NYB")
    if not dxy.get("now"):
        dxy = _get("DTWEXBGS")
    us10 = _get("DGS10")
    oil = _get("DCOILBRENTEU")
    if not oil.get("now"):
        oil = _get("BZ=F")
    gold = _get("GC=F")
    copper = _get("HG=F")

    def _fast(name, d, fmt="%.1f"):
        return "%-12s %7s %6s %6s %6s %6s %s" % (
            name, _v(d.get("now"), fmt),
            _pct(d.get("now"), d.get("1w")), _pct(d.get("now"), d.get("1m")),
            _pct(d.get("now"), d.get("3m")), _pct(d.get("now"), d.get("6m")),
            _trend(d))

    lines = [
        "📊 <b>MACRO DASHBOARD</b>",
        "📅 %s\n" % now_str,
        "━━━ RISK BAROMETER ━━━",
        "<pre>",
        "%-12s %7s %6s %6s %6s %6s" % ("", "Now", "1W", "1M", "3M", "6M"),
        _fast("VIX", vix),
        _fast("HYG", hyg),
        "</pre>\n",
        "━━━ DIRECTION ━━━",
        "<pre>",
        "%-12s %7s %6s %6s %6s %6s" % ("", "Now", "1W", "1M", "3M", "6M"),
        _fast("DXY", dxy),
        _fast("US 10Y", us10, "%.2f"),
        _fast("Brent", oil),
        _fast("Gold", gold, "%.0f"),
        _fast("Copper", copper, "%.2f"),
        "</pre>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# US ECONOMY (slow-moving: Now | 1M | 3M | 6M | 1Y)
# ---------------------------------------------------------------------------
def format_us_economy() -> str:
    gdp = _get("A191RL1Q225SBEA")
    unemp = _get("UNRATE")
    claims = _get("ICSA")
    nfp = _get("PAYEMS")
    jo = _get("JTSJOL")
    sent = _get("UMCSENT")
    housing = _get("HOUST")

    def _slow(name, d, fmt="%.1f", scale=1, suf=""):
        return "%-15s %7s %7s %7s %7s %7s %s" % (
            name, _v(d.get("now"), fmt, scale, suf),
            _v(d.get("1m"), fmt, scale, suf), _v(d.get("3m"), fmt, scale, suf),
            _v(d.get("6m"), fmt, scale, suf), _v(d.get("1y"), fmt, scale, suf),
            _trend(d))

    lines = [
        "━━━ 🇺🇸 US ECONOMY ━━━",
        "<pre>",
        "%-15s %7s %7s %7s %7s %7s" % ("", "Now", "1M", "3M", "6M", "1Y"),
        _slow("GDP Growth", gdp, "%.1f", suffix="%"),
        _slow("Unemployment", unemp, "%.1f", suffix="%"),
        _slow("Jobless Clms", claims, "%.0f", scale=0.001, suf="K"),
        _slow("Nonfarm", nfp, "%.0f", scale=0.001, suf="K"),
        _slow("Job Openings", jo, "%.0f", scale=0.001, suf="K"),
        _slow("Consumer Snt", sent, "%.1f"),
        _slow("Housing Strt", housing, "%.0f", scale=0.001, suf="K"),
        "</pre>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# INFLATION & YIELDS (slow-moving: Now | 1M | 3M | 6M | 1Y)
# ---------------------------------------------------------------------------
def format_inflation_yields() -> str:
    cpi = _get("CPIAUCSL")
    pce = _get("PCEPILFE")
    fed = _get("FEDFUNDS")
    y2 = _get("DGS2")
    y5 = _get("DGS5")
    y10 = _get("DGS10")
    y30 = _get("DGS30")
    spread = _get("T10Y2Y")
    mtg = _get("MORTGAGE30US")

    cpi_yoy = _pct(cpi.get("now"), cpi.get("1y")) if cpi.get("1y") else "-"
    pce_yoy = _pct(pce.get("now"), pce.get("1y")) if pce.get("1y") else "-"

    def _yld(name, d):
        return "%-15s %7s %7s %7s %7s %7s %s" % (
            name, _v(d.get("now"), "%.2f%%"),
            _v(d.get("1m"), "%.2f"), _v(d.get("3m"), "%.2f"),
            _v(d.get("6m"), "%.2f"), _v(d.get("1y"), "%.2f"),
            _trend(d))

    lines = [
        "━━━ INFLATION & YIELDS ━━━",
        "<pre>",
        "%-15s %7s %7s %7s %7s %7s" % ("", "Now", "1M", "3M", "6M", "1Y"),
        "%-15s %7s" % ("CPI YoY", cpi_yoy),
        "%-15s %7s" % ("Core PCE YoY", pce_yoy),
        _yld("Fed Rate", fed),
        _yld("US 2Y", y2),
        _yld("US 5Y", y5),
        _yld("US 10Y", y10),
        _yld("US 30Y", y30),
        "%-15s %7s %7s %7s %7s %7s %s" % (
            "2Y/10Y Spread", _v(spread.get("now"), "%+.2f"),
            _v(spread.get("1m"), "%+.2f"), _v(spread.get("3m"), "%+.2f"),
            _v(spread.get("6m"), "%+.2f"), _v(spread.get("1y"), "%+.2f"),
            _trend(spread)),
        _yld("30Y Mortgage", mtg),
        "</pre>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GLOBAL COMPARISON (slow-moving, compact)
# ---------------------------------------------------------------------------
def format_global_comparison() -> str:
    # Rates (with hardcoded fallbacks for stale FRED international data)
    us_r = _get("FEDFUNDS")
    uk_r = _get("IUDSOIA")
    eu_r = _get("ECBMLFR")
    jp_r = _get("IRSTCB01JPM156N")
    if not jp_r.get("now"):
        jp_r = {"now": 0.50, "dir": "rising"}  # BOJ raised to 0.5% Jan 2025

    # 10Y yields
    us_10 = _get("DGS10")
    uk_10 = _get("IRLTLT01GBM156N")
    eu_10 = _get("IRLTLT01DEM156N")
    jp_10 = _get("IRLTLT01JPM156N")

    # Unemployment
    us_u = _get("UNRATE")
    uk_u = _get("LRHUTTTTGBM156S")
    eu_u = _get("LRHUTTTTEZM156S")
    if not eu_u.get("now"):
        eu_u = _get("LRHUTTTTEUM156S")
    if not eu_u.get("now"):
        eu_u = {"now": 6.1, "dir": "flat"}  # Eurostat Dec 2025
    jp_u = _get("LRHUTTTTJPM156S")

    # GDP
    us_gdp = _get("A191RL1Q225SBEA")
    uk_gdp = _get("NAEXKP01GBQ657S")
    eu_gdp = _get("NAEXKP01EZQ657S")
    jp_gdp = _get("NAEXKP01JPQ657S")

    def _row(flag, name, gdp, unemp, rate, y10):
        return "%s %-5s %6s %6s %6s %6s" % (
            flag, name,
            _v(gdp.get("now"), "%.1f%%") if gdp.get("now") else "-",
            _v(unemp.get("now"), "%.1f%%") if unemp.get("now") else "-",
            _v(rate.get("now"), "%.2f%%") if rate.get("now") else "-",
            _v(y10.get("now"), "%.2f%%") if y10.get("now") else "-")

    # Carry trade
    usdjpy = _get("JPY=X")
    nikkei = _get("^N225")
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
    gap = "%.2f%%" % (fed_v - boj_v) if fed_v else "?"

    lines = [
        "━━━ 🌍 GLOBAL COMPARISON ━━━",
        "<pre>",
        "%-8s %6s %6s %6s %6s" % ("", "GDP", "Unemp", "Rate", "10Y"),
        _row("🇺🇸", "US", us_gdp, us_u, us_r, us_10),
        _row("🇬🇧", "UK", uk_gdp, uk_u, uk_r, uk_10),
        _row("🇪🇺", "EU", eu_gdp, eu_u, eu_r, eu_10),
        _row("🇯🇵", "JP", jp_gdp, jp_u, jp_r, jp_10),
        "</pre>\n",
        "━━━ 🇯🇵 CARRY TRADE ━━━",
        "BOJ: %s%% | Fed: %s%% | Gap: %s" % (_v(jp_r.get("now"), "%.2f"), _v(us_r.get("now"), "%.2f"), gap),
        "USD/JPY: %s  %s" % (_v(usdjpy.get("now"), "%.1f"), carry),
        "Nikkei: %s" % _v(nikkei.get("now"), "%.0f"),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMMODITIES & CURRENCIES (fast-moving: Now | 1W | 1M | 3M | 6M)
# ---------------------------------------------------------------------------
def format_commodities_currencies() -> str:
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

    def _fast(name, d, fmt="%.2f"):
        return "%-12s %7s %6s %6s %6s %6s %s" % (
            name, _v(d.get("now"), fmt),
            _pct(d.get("now"), d.get("1w")), _pct(d.get("now"), d.get("1m")),
            _pct(d.get("now"), d.get("3m")), _pct(d.get("now"), d.get("6m")),
            _trend(d))

    lines = [
        "━━━ COMMODITIES ━━━",
        "<pre>",
        "%-12s %7s %6s %6s %6s %6s" % ("", "Now", "1W", "1M", "3M", "6M"),
        _fast("Gold", gold, "%.0f"),
        _fast("Silver", silver),
        _fast("Platinum", plat, "%.0f"),
        _fast("Copper", copper),
        _fast("WTI", wti),
        _fast("Brent", brent),
        _fast("Nat Gas", gas),
        "</pre>\n",
        "━━━ CURRENCIES ━━━",
        "<pre>",
        "%-12s %7s %6s %6s %6s %6s" % ("", "Now", "1W", "1M", "3M", "6M"),
        _fast("DXY", dxy, "%.1f"),
        _fast("EUR/USD", eur, "%.4f"),
        _fast("GBP/USD", gbp, "%.4f"),
        _fast("USD/JPY", jpy, "%.1f"),
        _fast("USD/CNY", cny, "%.2f"),
        _fast("AUD/USD", aud, "%.4f"),
        "</pre>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# INDICES & KEY STOCKS (fast-moving: Now | 1W | 1M | 3M | 6M)
# ---------------------------------------------------------------------------
def format_indices_stocks() -> str:
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

    def _fast(name, d, fmt="%.0f"):
        return "%-12s %7s %6s %6s %6s %6s %s" % (
            name, _v(d.get("now"), fmt),
            _pct(d.get("now"), d.get("1w")), _pct(d.get("now"), d.get("1m")),
            _pct(d.get("now"), d.get("3m")), _pct(d.get("now"), d.get("6m")),
            _trend(d))

    lines = [
        "━━━ INDICES ━━━",
        "<pre>",
        "%-12s %7s %6s %6s %6s %6s" % ("", "Now", "1W", "1M", "3M", "6M"),
        _fast("S&P 500", spx),
        _fast("Nasdaq", ndx),
        _fast("Dow Jones", dji),
        _fast("Russell", rut),
        _fast("FTSE 100", ftse),
        _fast("Nikkei", nik),
        _fast("Hang Seng", hsi),
        "</pre>\n",
        "━━━ KEY STOCKS ━━━",
        "<pre>",
        "%-12s %7s %6s %6s %6s %6s" % ("", "Now", "1W", "1M", "3M", "6M"),
        _fast("NVDA", nvda, "%.1f"),
        _fast("TSLA", tsla, "%.1f"),
        _fast("MSTR", mstr, "%.1f"),
        _fast("COIN", coin, "%.1f"),
        _fast("KRE Banks", kre, "%.1f"),
        _fast("TLT Bonds", tlt, "%.1f"),
        _fast("HYG Junk", hyg, "%.1f"),
        "</pre>",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MACRO CHANGES (for morning brief)
# ---------------------------------------------------------------------------
def format_macro_changes() -> str:
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


# ---------------------------------------------------------------------------
# MACRO PULSE (for notebook / Huoyan pulse)
# ---------------------------------------------------------------------------
def format_macro_pulse() -> str:
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
        spx_pct = "-"

    return (
        "━━━ MACRO PULSE ━━━\n"
        "VIX: %s | 10Y: %s%% | Oil: $%s\n"
        "JPY: %s | SPX: %s\n"
        "/macro for full dashboard"
    ) % (
        _v(vix.get("now"), "%.1f"), _v(us10.get("now"), "%.2f"),
        _v(oil.get("now"), "%.0f"), _v(jpy.get("now"), "%.1f"), spx_pct
    )


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------
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
