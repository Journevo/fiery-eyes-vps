"""cycle_screen.py — THE KEY SCREEN: should you be IN or OUT."""
from datetime import datetime, timezone
from config import get_logger

log = get_logger("cycle_screen")


def generate_cycle_screen():
    """Composite 5 decision layers into a single verdict."""
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = []
    lines.append("=== CYCLE ASSESSMENT — %s ===" % now)
    lines.append("")

    # Layer 1: Business Cycle
    pmi_val = None
    pmi_season = "UNKNOWN"
    try:
        from nimbus_sync import get_nimbus_section
        pmi = get_nimbus_section("pmi") or {}
        gw = pmi.get("global_weighted", [])
        if gw:
            pmi_val = gw[-1]
            prev = gw[-2] if len(gw) >= 2 else None
            if pmi_val and pmi_val > 50:
                pmi_season = "SUMMER" if (prev and pmi_val > prev) else "AUTUMN"
            elif pmi_val:
                pmi_season = "SPRING" if (prev and pmi_val > prev) else "WINTER"
    except Exception:
        pass

    icons = {"SPRING": "SPRING (recovery)", "SUMMER": "SUMMER (expansion)",
             "AUTUMN": "AUTUMN (slowdown)", "WINTER": "WINTER (contraction)", "UNKNOWN": "?"}
    lines.append("1. BUSINESS CYCLE: %s" % icons.get(pmi_season, "?"))
    if pmi_val:
        lines.append("   Global PMI: %.1f" % pmi_val)
    lines.append("")

    # Layer 2: Liquidity
    liq_regime = "UNKNOWN"
    try:
        from nimbus_sync import get_regimes
        r = get_regimes() or {}
        us = r.get("us_regime", "?")
        gl = r.get("global_regime", "?")
        m2 = r.get("m2_regime", "?")
        vals = [us, gl, m2]
        exp = sum(1 for v in vals if v == "EXPANDING")
        con = sum(1 for v in vals if v == "CONTRACTING")
        liq_regime = "EXPANDING" if exp >= 2 else ("CONTRACTING" if con >= 2 else "STALL")
        lines.append("2. LIQUIDITY: %s" % liq_regime)
        lines.append("   US: %s | Global: %s | M2: %s" % (us, gl, m2))
    except Exception:
        lines.append("2. LIQUIDITY: unavailable")
    lines.append("")

    # Layer 3: BTC Cycle
    btc_price = None
    bear_pct = None
    try:
        from btc_cycle import fetch_btc_price, calculate_cycle
        btc_price = fetch_btc_price()
        if btc_price:
            c = calculate_cycle(btc_price)
            bear_pct = c.get("bear_progress_pct")
            lines.append("3. BTC CYCLE: %s%% complete" % ("%.0f" % bear_pct if bear_pct else "?"))
            lines.append("   BTC: $%s | ~%sd to est. bottom" % (
                "{:,.0f}".format(btc_price), c.get("days_remaining", "?")))
    except Exception:
        lines.append("3. BTC CYCLE: unavailable")

    cbbi = None
    try:
        from nimbus_sync import get_nimbus_section
        crypto = get_nimbus_section("crypto") or {}
        cbbi_vals = crypto.get("cbbi", [])
        cbbi = cbbi_vals[-1] if isinstance(cbbi_vals, list) and cbbi_vals else None
        if cbbi:
            lines.append("   CBBI: %s/100" % cbbi)
    except Exception:
        pass
    lines.append("")

    # Layer 4: Market Structure
    fg_val = None
    try:
        from market_structure import run_market_structure
        ms = run_market_structure() or {}
        fg = ms.get("fear_greed", {})
        fg_val = fg.get("value")
        fg_label = fg.get("label", "")
        lines.append("4. MARKET STRUCTURE:")
        if fg_val is not None:
            lines.append("   F&G: %s (%s)" % (fg_val, fg_label))
        funding = ms.get("funding", {})
        if funding.get("current_pct") is not None:
            lines.append("   BTC Funding: %s%%" % funding["current_pct"])
        oi = ms.get("oi", {})
        if oi.get("oi_usd"):
            lines.append("   BTC OI: $%.1fB" % (oi["oi_usd"] / 1e9))
    except Exception:
        lines.append("4. MARKET STRUCTURE: unavailable")
    lines.append("")

    # Layer 5: Macro
    try:
        from nimbus_sync import get_nimbus_section
        rates = get_nimbus_section("rates") or {}
        commod = get_nimbus_section("commodities") or {}
        dxy_data = get_nimbus_section("dxy") or {}
        fed = rates.get("fed", {})
        oil = commod.get("wti_oil", {})
        dxy_vals = dxy_data.get("values", [])
        lines.append("5. MACRO:")
        parts = []
        parts.append("Fed %s%%" % fed.get("rate", "?"))
        parts.append("Oil $%s" % oil.get("price", "?"))
        parts.append("DXY %s" % (dxy_vals[-1] if dxy_vals else "?"))
        lines.append("   " + " | ".join(parts))
        geo = get_nimbus_section("geopolitics") or {}
        if geo.get("iran_war"):
            lines.append("   Iran war: %s" % geo["iran_war"])
    except Exception:
        lines.append("5. MACRO: unavailable")
    lines.append("")

    # Verdict
    pmi_above = pmi_val is not None and pmi_val > 50
    pmi_below = pmi_val is not None and pmi_val <= 50
    fg_fear = fg_val is not None and fg_val < 30

    if liq_regime == "EXPANDING" and pmi_above and fg_fear:
        verdict, conv = "ACCUMULATE", 9
        reason = "Liquidity expanding + growth + extreme fear = prime accumulation"
    elif liq_regime == "EXPANDING" and pmi_above:
        verdict, conv = "ACCUMULATE", 8
        reason = "Liquidity expanding + growth intact"
    elif liq_regime == "STALL" and pmi_above:
        verdict, conv = "HOLD", 6
        reason = "Growth intact but liquidity stalling"
    elif liq_regime == "CONTRACTING" and pmi_below:
        verdict, conv = "DE-RISK", 3
        reason = "Liquidity contracting + growth falling"
    elif liq_regime == "CONTRACTING":
        verdict, conv = "DE-RISK", 4
        reason = "Liquidity contracting"
    else:
        verdict, conv = "CAUTIOUS", 5
        reason = "Mixed signals"

    dots = "o" * conv + "." * (10 - conv)
    lines.append("=== VERDICT: %s ===" % verdict)
    lines.append("Conviction: %d/10  [%s]" % (conv, dots))
    lines.append(reason)
    lines.append("")

    # Per-token actions
    try:
        from research.research_manager import load_index
        index = load_index() or {}
        sf = {}
        try:
            rows = execute_sf()
            for r in rows:
                sf[r[0]] = r[1]
        except Exception:
            pass

        lines.append("PER TOKEN:")
        order = ["BTC", "SOL", "HYPE", "JUP", "RENDER", "SUI", "BONK", "PUMP", "PENGU", "FARTCOIN"]
        for tok in order:
            info = index.get(tok, {})
            score = info.get("score")
            rec = info.get("recommendation", "?")
            sf_val = sf.get(tok)

            if verdict == "ACCUMULATE" and score and score >= 70:
                action = "ACCUMULATE"
                icon = "^"
            elif verdict == "DE-RISK" and (not score or score < 80):
                action = "REDUCE"
                icon = "v"
            else:
                action = "HOLD"
                icon = "-"

            detail = ""
            if score:
                detail += " DD:%s" % score
            if sf_val:
                detail += " SF:%.0f" % sf_val
            lines.append("  [%s] %-8s %s%s" % (icon, tok, action, detail))
    except Exception:
        pass

    return "\n".join(lines)


def execute_sf():
    from db.connection import execute
    return execute("SELECT token, conviction_score FROM sunflow_conviction ORDER BY conviction_score DESC", fetch=True) or []
