"""correlation_engine.py — 6 multi-signal correlation patterns + Cycle Confidence Score."""

import json
import requests
from datetime import datetime, timedelta, timezone

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute, execute_one

log = get_logger("correlation")

KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True, "is_persistent": True,
}

# ---------------------------------------------------------------------------
# 6 Correlation Patterns
# ---------------------------------------------------------------------------
PATTERNS = {
    "CREDIT_CRISIS": {
        "name": "Credit Crisis Warning",
        "severity": "critical",
        "conditions": [
            {"name": "VIX > 30", "check": lambda d: (d.get("VIX") or 0) > 30},
            {"name": "HYG -3% in 7d", "check": lambda d: (d.get("HYG_7d_pct") or 0) < -3},
            {"name": "KRE -5% in 7d", "check": lambda d: (d.get("KRE_7d_pct") or 0) < -5},
            {"name": "Copper -5% in 14d", "check": lambda d: (d.get("HG=F_30d_pct") or 0) < -5},
            {"name": "Yield curve inverted", "check": lambda d: (d.get("T10Y2Y") or 1) < 0},
        ],
        "action": "Maximum dry powder, no new positions",
        "history": "Preceded 2008, March 2020, SVB 2023",
    },
    "CARRY_UNWIND": {
        "name": "Carry Trade Unwind",
        "severity": "critical",
        "conditions": [
            {"name": "USDJPY -3% in 7d", "check": lambda d: (d.get("JPY=X_7d_pct") or 0) < -3},
            {"name": "Nikkei -5% in 7d", "check": lambda d: (d.get("^N225_7d_pct") or 0) < -5},
            {"name": "VIX spike >25% in 1d", "check": lambda d: (d.get("VIXCLS_1d_pct") or d.get("^VIX_1d_pct") or 0) > 25},
            {"name": "BOJ rate hiked", "check": lambda d: (d.get("IRSTCB01JPM156N_30d_pct") or 0) > 5},
        ],
        "action": "Reduce all risk, expect 5-10% global drop",
        "history": "August 2024 crash",
    },
    "STAGFLATION": {
        "name": "Stagflation Trap",
        "severity": "critical",
        "conditions": [
            {"name": "Oil Brent > $100", "check": lambda d: (d.get("oil_brent") or 0) > 100},
            {"name": "Unemployment > 4%", "check": lambda d: (d.get("unemployment") or 0) > 4},
            {"name": "10Y yield > 4.5%", "check": lambda d: (d.get("US10Y") or 0) > 4.5},
            {"name": "Fed rate unchanged 3m", "check": lambda d: abs(d.get("FEDFUNDS_30d_pct") or 999) < 1},
        ],
        "action": "Gold, energy stocks, cash. Avoid growth/crypto",
        "history": "1973-1974, possibly current",
    },
    "ACCUMULATION": {
        "name": "Accumulation Zone",
        "severity": "positive",
        "conditions": [
            {"name": "F&G < 15", "check": lambda d: (d.get("fear_greed") or 100) < 15},
            {"name": "BTC funding negative", "check": lambda d: (d.get("btc_funding") or 0) < 0},
            {"name": "VIX declining from peak", "check": lambda d: (d.get("VIXCLS_7d_pct") or d.get("^VIX_7d_pct") or 0) < -10},
            {"name": "M2 expanding", "check": lambda d: d.get("m2_dir") == "rising"},
        ],
        "action": "Begin DCA, increase deployment %",
    },
    "DDAY": {
        "name": "D-Day Signal (Risk-On)",
        "severity": "positive",
        "conditions": [
            {"name": "DXY falling >3% in 30d", "check": lambda d: (d.get("DX-Y.NYB_30d_pct") or 0) < -3},
            {"name": "10Y yield falling", "check": lambda d: (d.get("DGS10_30d_pct") or 0) < -5},
            {"name": "VIX < 20", "check": lambda d: (d.get("VIX") or 100) < 20},
            {"name": "F&G > 30", "check": lambda d: (d.get("fear_greed") or 0) > 30},
            {"name": "M2 expanding", "check": lambda d: d.get("m2_dir") == "rising"},
            {"name": "Cycle Score > 50", "check": lambda d: (d.get("cycle_score") or 0) > 50},
        ],
        "action": "Activate 3x leverage, deploy with conviction",
    },
    "DISTRIBUTION": {
        "name": "Distribution Warning",
        "severity": "warning",
        "conditions": [
            {"name": "F&G > 80", "check": lambda d: (d.get("fear_greed") or 0) > 80},
            {"name": "BTC funding > 0.05%", "check": lambda d: (d.get("btc_funding") or 0) > 0.05},
            {"name": "CBBI > 85", "check": lambda d: (d.get("cbbi") or 0) > 85},
            {"name": "YouTube >90% bullish", "check": lambda d: (d.get("yt_bullish_pct") or 0) > 90},
        ],
        "action": "Scale out, take profits, increase dry powder",
    },
}


# ---------------------------------------------------------------------------
# Data Gathering
# ---------------------------------------------------------------------------
def gather_data() -> dict:
    """Pull current values from macro_dashboard_cache + other sources."""
    data = {}

    # From macro_dashboard_cache
    rows = execute(
        "SELECT series_key, current_value, value_1d, value_1w, value_1m FROM macro_dashboard_cache",
        fetch=True)
    for key, current, v1d, v1w, v1m in (rows or []):
        if current is not None:
            c = float(current)
            data[key] = c
            if v1d and abs(float(v1d)) > 0.001:
                data["%s_1d_pct" % key] = ((c - float(v1d)) / abs(float(v1d))) * 100
            if v1w and abs(float(v1w)) > 0.001:
                data["%s_7d_pct" % key] = ((c - float(v1w)) / abs(float(v1w))) * 100
            if v1m and abs(float(v1m)) > 0.001:
                data["%s_30d_pct" % key] = ((c - float(v1m)) / abs(float(v1m))) * 100

    # Map friendly keys
    data["VIX"] = data.get("VIXCLS", data.get("^VIX", 0))
    data["oil_brent"] = data.get("DCOILBRENTEU", data.get("BZ=F", 0))
    data["US10Y"] = data.get("DGS10", 0)
    data["T10Y2Y"] = data.get("T10Y2Y", 0)
    data["unemployment"] = data.get("UNRATE", 0)
    data["USDJPY"] = data.get("JPY=X", 0)

    # F&G, funding, CBBI from existing sources
    try:
        from market_structure import run_market_structure
        ms = run_market_structure() or {}
        data["fear_greed"] = int(ms.get("fear_greed", {}).get("value", 50))
        data["btc_funding"] = float(ms.get("funding", {}).get("current_pct", 0))
    except Exception:
        data["fear_greed"] = 50
        data["btc_funding"] = 0

    try:
        from nimbus_sync import get_nimbus_data
        nd = get_nimbus_data() or {}
        cbbi_vals = nd.get("crypto", {}).get("cbbi", [])
        data["cbbi"] = cbbi_vals[-1] if isinstance(cbbi_vals, list) and cbbi_vals else 50
    except Exception:
        data["cbbi"] = 50

    # Directions from dashboard cache
    for key in ["DX-Y.NYB", "DTWEXBGS", "M2SL"]:
        row = execute_one(
            "SELECT direction FROM macro_dashboard_cache WHERE series_key = %s", (key,))
        if row:
            data["%s_dir" % key] = row[0]
    data["m2_dir"] = data.get("M2SL_dir", "flat")

    # Liquidity direction from liquidity table
    try:
        liq_row = execute_one("SELECT fred_regime FROM liquidity ORDER BY date DESC LIMIT 1")
        if liq_row:
            data["us_liq_regime"] = liq_row[0]
    except Exception:
        pass

    # YouTube consensus
    cons = execute_one(
        "SELECT bearish_pct, bullish_pct FROM consensus_daily WHERE token = 'BTC' AND date = CURRENT_DATE")
    data["yt_bearish_pct"] = int(cons[0]) if cons and cons[0] else 50
    data["yt_bullish_pct"] = int(cons[1]) if cons and cons[1] else 50

    return data


# ---------------------------------------------------------------------------
# Correlation Check
# ---------------------------------------------------------------------------
def run_correlation_check(send_alerts: bool = True) -> list:
    """Check all 6 patterns. Returns list of triggered patterns."""
    data = gather_data()
    triggered = []

    for pid, pattern in PATTERNS.items():
        met = 0
        total = len(pattern["conditions"])
        details = []
        for cond in pattern["conditions"]:
            try:
                passed = cond["check"](data)
            except Exception:
                passed = False
            if passed:
                met += 1
            details.append({"name": cond["name"], "met": passed})

        if met >= total * 0.6:
            # Check recent alert
            recent = execute_one(
                "SELECT id FROM correlation_alerts WHERE pattern_name = %s AND alerted_at >= NOW() - INTERVAL '24 hours'",
                (pid,))
            if recent:
                continue

            severity = pattern["severity"]
            prefix = {"critical": "🔴🔴🔴", "positive": "🟢🟢🟢", "warning": "⚠️⚠️⚠️"}.get(severity, "ℹ️")
            met_list = [d["name"] for d in details if d["met"]]
            unmet_list = [d["name"] for d in details if not d["met"]]

            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            alert = (
                "%s <b>CORRELATION ALERT</b> %s\n📅 %s\n\n"
                "<b>%s</b> — %d/%d conditions met\n\n"
                "✅ MET:\n%s\n\n❌ NOT MET:\n%s\n\n"
                "Action: %s"
            ) % (prefix, prefix, ts, pattern["name"], met, total,
                 "\n".join("  ✅ " + c for c in met_list),
                 "\n".join("  ❌ " + c for c in unmet_list),
                 pattern["action"])

            if "history" in pattern:
                alert += "\nHistory: %s" % pattern["history"]

            if send_alerts:
                _send_tg(alert)
            execute(
                "INSERT INTO correlation_alerts (pattern_name, conditions_met, conditions_total, details, severity) VALUES (%s,%s,%s,%s,%s)",
                (pid, met, total, json.dumps(details), severity))
            triggered.append(pid)
            log.info("Correlation alert: %s (%d/%d)", pid, met, total)

    log.info("Correlation check: %d patterns triggered", len(triggered))
    return triggered


def format_correlations() -> str:
    """Format all patterns with current status for /correlations."""
    data = gather_data()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["🔗 <b>CORRELATION PATTERNS</b>\n📅 %s\n" % ts]

    for pid, pattern in PATTERNS.items():
        met = 0
        total = len(pattern["conditions"])
        for cond in pattern["conditions"]:
            try:
                if cond["check"](data):
                    met += 1
            except Exception:
                pass
        pct = met / total * 100 if total else 0
        if pct >= 80:
            icon = "🔴"
        elif pct >= 60:
            icon = "⚠️"
        elif pct >= 40:
            icon = "🟡"
        else:
            icon = "✅"
        lines.append("%s %s: %d/%d (%.0f%%)" % (icon, pattern["name"], met, total, pct))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cycle Confidence Score
# ---------------------------------------------------------------------------
def calculate_cycle_score() -> dict:
    """Calculate 0-100 score from 6 layers."""
    data = gather_data()
    breakdown = {}
    total = 0

    # Layer 1: Business Cycle (0-20)
    l1 = 10
    gdp = data.get("A191RL1Q225SBEA", 0) or 0
    unemp = data.get("UNRATE", 4.4) or 4.4
    if gdp > 2: l1 += 5
    elif gdp < 1: l1 -= 5
    if unemp < 4: l1 += 3
    elif unemp > 4.5: l1 -= 3
    l1 = max(0, min(20, l1))
    breakdown["Business Cycle"] = l1
    total += l1

    # Layer 2: Liquidity (0-20)
    l2 = 10
    regime = data.get("us_liq_regime", "UNKNOWN")
    dxy_dir = data.get("DX-Y.NYB_dir", data.get("DTWEXBGS_dir", "flat"))
    m2_dir = data.get("m2_dir", "flat")
    if regime == "EXPANDING": l2 += 4
    elif regime == "CONTRACTING": l2 -= 4
    if m2_dir == "rising": l2 += 2
    elif m2_dir == "falling": l2 -= 3
    if dxy_dir == "falling": l2 += 1
    elif dxy_dir == "rising": l2 -= 1
    l2 = max(0, min(20, l2))
    breakdown["Liquidity"] = l2
    total += l2

    # Layer 3: BTC Cycle (0-20)
    l3 = 5
    peak = datetime(2025, 10, 6)
    est_bottom = datetime(2026, 10, 6)
    now = datetime.now()
    days_elapsed = (now - peak).days
    total_days = (est_bottom - peak).days
    bear_pct = min(100, max(0, (days_elapsed / total_days) * 100))
    if bear_pct > 70: l3 += 8
    elif bear_pct > 50: l3 += 4
    elif bear_pct < 30: l3 -= 3
    cbbi = data.get("cbbi", 50) or 50
    if cbbi < 30: l3 += 3
    elif cbbi > 80: l3 -= 5
    l3 = max(0, min(20, l3))
    breakdown["BTC Cycle"] = l3
    total += l3

    # Layer 4: Market Structure (0-20)
    l4 = 10
    fg = data.get("fear_greed", 50) or 50
    funding = data.get("btc_funding", 0) or 0
    vix = data.get("VIX", 20) or 20
    if fg < 15: l4 += 3
    elif fg < 30: l4 += 1
    elif fg > 75: l4 -= 3
    if funding < -0.01: l4 += 3
    elif funding > 0.05: l4 -= 3
    if vix < 18: l4 += 3
    elif vix > 30: l4 -= 3
    l4 = max(0, min(20, l4))
    breakdown["Market Structure"] = l4
    total += l4

    # Layer 5: Macro Triggers (0-10)
    l5 = 5
    oil = data.get("oil_brent", 80) or 80
    us10y = data.get("US10Y", 4.0) or 4.0
    if oil < 80: l5 += 2
    elif oil > 120: l5 -= 3
    elif oil > 100: l5 -= 1
    if us10y < 3.5: l5 += 2
    elif us10y > 5.0: l5 -= 3
    elif us10y > 4.5: l5 -= 1
    l5 = max(0, min(10, l5))
    breakdown["Macro Triggers"] = l5
    total += l5

    # Layer 6: Token Signals (0-10)
    l6 = 5
    bearish = data.get("yt_bearish_pct", 50) or 50
    if bearish > 80: l6 -= 2
    elif bearish < 40: l6 += 2
    l6 = max(0, min(10, l6))
    breakdown["Token Signals"] = l6
    total += l6

    # Level
    if total <= 20: level, name = 1, "FEAR"
    elif total <= 40: level, name = 2, "CAUTIOUS"
    elif total <= 60: level, name = 3, "TREND"
    elif total <= 80: level, name = 4, "CONVICTION"
    else: level, name = 5, "EUPHORIA"

    deploy = {1: "10-20%", 2: "20-40%", 3: "40-60%", 4: "60-80%", 5: "scale back"}
    lev = {1: "none", 2: "spot only", 3: "3x begins", 4: "3-4x", 5: "reduce"}

    return {
        "score": total, "level": level, "level_name": name,
        "deploy_pct": deploy[level], "leverage": lev[level],
        "breakdown": breakdown, "bear_pct": round(bear_pct, 1),
    }


def format_cycle_score() -> str:
    """Format cycle score for Telegram."""
    r = calculate_cycle_score()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "🎯 <b>CYCLE CONFIDENCE SCORE</b>",
        "📅 %s\n" % ts,
        "Score: <b>%d/100</b> → LEVEL %d (%s)\n" % (r["score"], r["level"], r["level_name"]),
    ]
    for layer, points in r["breakdown"].items():
        max_pts = 20 if layer in ("Business Cycle", "Liquidity", "BTC Cycle", "Market Structure") else 10
        lines.append("  %-18s %2d/%d" % (layer, points, max_pts))
    lines.append("")
    lines.append("Bear: %.0f%% complete" % r["bear_pct"])
    lines.append("Deploy: %s | Leverage: %s" % (r["deploy_pct"], r["leverage"]))
    return "\n".join(lines)


def format_cycle_score_pulse() -> str:
    """Compact cycle score for pulse."""
    r = calculate_cycle_score()
    return "━━━ CYCLE CONFIDENCE ━━━\nScore: %d/100 → L%d (%s) | Deploy: %s" % (
        r["score"], r["level"], r["level_name"], r["deploy_pct"])


# ---------------------------------------------------------------------------
# Contrarian Flag
# ---------------------------------------------------------------------------
def check_contrarian():
    """Flag when consensus >90% in either direction for 5+ days."""
    for token in ["BTC", "SOL"]:
        rows = execute(
            "SELECT bearish_pct FROM consensus_daily WHERE token = %s AND date >= CURRENT_DATE - 7 ORDER BY date",
            (token,), fetch=True)
        if len(rows) < 5:
            continue
        avg_bear = sum(float(r[0]) for r in rows if r[0]) / len(rows)
        if avg_bear > 90:
            recent = execute_one(
                "SELECT id FROM correlation_alerts WHERE pattern_name = %s AND alerted_at >= NOW() - INTERVAL '7 days'",
                ("CONTRARIAN_%s_BEAR" % token,))
            if not recent:
                _send_tg(
                    "⚠️⚠️⚠️ <b>CONTRARIAN FLAG</b> ⚠️⚠️⚠️\n\n"
                    "%s: %.0f%% bearish for 7+ days\n"
                    "Extreme consensus historically marks reversals.\n"
                    "NOT a buy signal — review your thesis." % (token, avg_bear))
                execute(
                    "INSERT INTO correlation_alerts (pattern_name, conditions_met, conditions_total, severity) VALUES (%s, 1, 1, 'warning')",
                    ("CONTRARIAN_%s_BEAR" % token,))


def _send_tg(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True,
                  "reply_markup": KEYBOARD_JSON}, timeout=15)
    except Exception as e:
        log.error("Telegram: %s", e)
