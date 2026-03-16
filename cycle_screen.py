"""cycle_screen.py — THE KEY SCREEN with emojis."""
from datetime import datetime, timezone
from config import get_logger

log = get_logger("cycle_screen")


def generate_cycle_screen():
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    lines = []
    lines.append("\U0001f504 <b>CYCLE ASSESSMENT</b> \u2014 %s" % now)
    lines.append("")

    nimbus_state = None
    data = None
    try:
        from nimbus_sync import get_nimbus_data
        from nimbus_engine import compute_nimbus
        data = get_nimbus_data()
        if data:
            nimbus_state = compute_nimbus(data)
    except Exception as e:
        log.error("Nimbus engine failed: %s", e)

    # Layer 1: Nimbus Cycle
    if nimbus_state:
        c = nimbus_state["cycle"]
        conf = nimbus_state["confidence"]
        rm = nimbus_state["risk_meter"]
        season_e = {
            "SUMMER": "\u2600\ufe0f", "SPRING": "\U0001f331",
            "AUTUMN": "\U0001f342", "WINTER": "\u2744\ufe0f"
        }.get(c["season"], "\u2753")
        rm_e = "\U0001f534" if rm["value"] < 25 else "\U0001f7e1" if rm["value"] < 50 else "\U0001f7e2" if rm["value"] < 75 else "\U0001f525"

        lines.append("1\ufe0f\u20e3 <b>NIMBUS CYCLE</b>")
        lines.append("   %s <b>%s %s</b> (Week %s)" % (season_e, c["season"], c["phase"], c["week"]))
        lines.append("   \U0001f3af Confidence: %s/%s (%s%%)" % (conf["effective"], conf["max"], conf["pct"]))
        lines.append("   %s Risk Meter: %s/100 \u2014 %s" % (rm_e, rm["value"], rm["band"]))
        lines.append("   \U0001f4ca Cap: %s max (%s%%)" % (conf["cap_value"], int(conf["cap_value"] / conf["max"] * 100)))
        lines.append("")
    else:
        lines.append("1\ufe0f\u20e3 NIMBUS CYCLE: \u274c unavailable")
        lines.append("")

    # Layer 2: Liquidity
    liq_regime = "UNKNOWN"
    if nimbus_state:
        lm = nimbus_state["liq_momentum"]
        m2l = nimbus_state["m2_lag"]
        scores = nimbus_state["scores"]
        nl = scores.get("net_liq_direction", 0)
        m2 = scores.get("m2_growth", 0)
        dxy = scores.get("dxy_direction", 0)
        liq_total = nl + m2 + dxy
        liq_regime = "EXPANDING" if liq_total >= 10 else ("CONTRACTING" if liq_total <= 4 else "STALL")
        liq_e = "\u2705" if liq_regime == "EXPANDING" else "\u274c" if liq_regime == "CONTRACTING" else "\u23f3"

        lines.append("2\ufe0f\u20e3 <b>LIQUIDITY</b> %s %s" % (liq_e, liq_regime))
        lines.append("   \U0001f4a7 Net Liq: %s/5 | M2: %s/5 | DXY: %s/5" % (nl, m2, dxy))
        lines.append("   %s Momentum: %s" % ("\U0001f4ca", lm["display"]))
        lines.append("   \u23f1 M2 Lag: Day %s (%s)" % (m2l["days_since"], m2l["status"]))
        lines.append("")
    else:
        lines.append("2\ufe0f\u20e3 LIQUIDITY: \u274c unavailable")
        lines.append("")

    # Layer 3: BTC Cycle
    btc_price = None
    bear_pct = None
    try:
        from btc_cycle import fetch_btc_price, calculate_cycle
        btc_price = fetch_btc_price()
        if btc_price:
            cyc = calculate_cycle(btc_price)
            bear_pct = cyc.get("bear_progress_pct", 0)
            lines.append("3\ufe0f\u20e3 <b>BTC CYCLE</b> \u23f3 Bear ~%s%%" % ("%.0f" % bear_pct))
            lines.append("   \U0001f4b0 BTC: $%s" % "{:,.0f}".format(btc_price))
            lines.append("   \u23f0 ~%sd to est. bottom" % cyc.get("days_remaining", "?"))
    except Exception:
        lines.append("3\ufe0f\u20e3 BTC CYCLE: \u274c price unavailable")

    if data:
        crypto = data.get("crypto", {})
        cbbi_vals = crypto.get("cbbi", [])
        cbbi = cbbi_vals[-1] if isinstance(cbbi_vals, list) and cbbi_vals else None
        if cbbi:
            lines.append("   \U0001f4ca CBBI: %s/100" % cbbi)

    if nimbus_state:
        bt = nimbus_state["scores"].get("btc_technicals", 0)
        erlang = "ATTACK \u2694\ufe0f" if bt >= 3 else "DEFEND \U0001f6e1\ufe0f"
        lines.append("   %s Erlang: %s (tech %s/5)" % (
            "\U0001f7e2" if bt >= 3 else "\U0001f534", erlang, bt))
    lines.append("")

    # Layer 4: Market Structure
    fg_val = None
    try:
        from market_structure import run_market_structure
        ms = run_market_structure() or {}
        fg = ms.get("fear_greed", {})
        fg_val = fg.get("value")
        fg_e = "\U0001f631" if fg_val and fg_val <= 20 else "\U0001f628" if fg_val and fg_val <= 35 else "\U0001f610" if fg_val and fg_val <= 55 else "\U0001f929" if fg_val and fg_val <= 75 else "\U0001f525"
        fg_label = fg.get("label", "")
        lines.append("4\ufe0f\u20e3 <b>MARKET STRUCTURE</b>")
        if fg_val is not None:
            lines.append("   %s F&G: %s (%s)" % (fg_e, fg_val, fg_label))
        funding = ms.get("funding", {})
        if funding.get("current_pct") is not None:
            f_e = "\U0001f7e2" if funding["current_pct"] < 0 else "\U0001f534"
            lines.append("   %s BTC Funding: %s%%" % (f_e, funding["current_pct"]))
        oi = ms.get("oi", {})
        if oi.get("oi_usd"):
            lines.append("   \U0001f4b5 BTC OI: $%.1fB" % (oi["oi_usd"] / 1e9))
    except Exception:
        if data:
            fg_vals = data.get("crypto", {}).get("fear_greed", [])
            if fg_vals and fg_vals[-1]:
                fg_val = fg_vals[-1]
                lines.append("4\ufe0f\u20e3 <b>MARKET STRUCTURE</b>")
                lines.append("   F&G: %s (Nimbus)" % fg_val)
        else:
            lines.append("4\ufe0f\u20e3 MARKET STRUCTURE: \u274c unavailable")
    lines.append("")

    # Layer 5: Macro
    if data:
        rates = data.get("rates", {})
        commod = data.get("commodities", {})
        dxy_data = data.get("dxy", {})
        pmi = data.get("pmi", {})
        fed = rates.get("fed", {})
        oil = commod.get("wti_oil", {})
        dxy_vals = dxy_data.get("values", [])
        gw = pmi.get("global_weighted", [])
        geo = data.get("geopolitics", {})

        lines.append("5\ufe0f\u20e3 <b>MACRO</b>")
        lines.append("   \U0001f3ed PMI: %s | \U0001f3e6 Fed: %s%% | \U0001f6e2\ufe0f Oil: $%s | \U0001f4b5 DXY: %s" % (
            gw[-1] if gw else "?", fed.get("rate", "?"),
            oil.get("price", "?"), dxy_vals[-1] if dxy_vals else "?"))
        if geo.get("iran_war"):
            lines.append("   \U0001f1ee\U0001f1f7 Iran war: %s" % geo["iran_war"])
    else:
        lines.append("5\ufe0f\u20e3 MACRO: \u274c unavailable")
    lines.append("")

    # Contradictions
    if nimbus_state and nimbus_state["contradictions"]:
        lines.append("\u26a0\ufe0f <b>CONTRADICTIONS (%d)</b>" % len(nimbus_state["contradictions"]))
        for ct in nimbus_state["contradictions"]:
            lines.append("  \U0001f534 %s" % ct)
        lines.append("")

    # VERDICT
    nimbus_verdict = "CAUTIOUS"
    nimbus_conv = 5
    if nimbus_state:
        rm_val = nimbus_state["risk_meter"]["value"]
        fa = nimbus_state["final_action"]
        nimbus_action = fa["action"]
        action_map = {"WAIT": "DE-RISK", "WATCHLIST": "CAUTIOUS", "STARTER": "HOLD",
                      "FULL SIZE": "ACCUMULATE", "AGGRESSIVE": "ACCUMULATE"}
        nimbus_verdict = action_map.get(nimbus_action, "CAUTIOUS")

        if fg_val is not None and fg_val < 25 and nimbus_verdict in ("CAUTIOUS", "HOLD"):
            nimbus_verdict = "ACCUMULATE"
        if rm_val >= 75: nimbus_conv = 9
        elif rm_val >= 50: nimbus_conv = 7
        elif rm_val >= 25: nimbus_conv = 5
        else: nimbus_conv = 3
        if fg_val is not None and fg_val < 20 and rm_val >= 40:
            nimbus_conv = min(nimbus_conv + 2, 10)
        if nimbus_state.get("contradictions") and len(nimbus_state["contradictions"]) >= 3:
            nimbus_conv = max(nimbus_conv - 1, 1)

    verdict_e = {
        "ACCUMULATE": "\U0001f7e2 ACCUMULATE \U0001f4b0",
        "HOLD": "\U0001f7e1 HOLD \u23f8\ufe0f",
        "CAUTIOUS": "\U0001f7e0 CAUTIOUS \u26a0\ufe0f",
        "DE-RISK": "\U0001f534 DE-RISK \U0001f6a8",
    }.get(nimbus_verdict, nimbus_verdict)

    dots = "\u25cf" * nimbus_conv + "\u25cb" * (10 - nimbus_conv)
    lines.append("\U0001f3af <b>VERDICT: %s</b>" % verdict_e)
    lines.append("\U0001f4aa Conviction: %d/10  [%s]" % (nimbus_conv, dots))
    if nimbus_state:
        lines.append("\U0001f916 Nimbus: %s" % nimbus_state["decision_log"][:80])
    lines.append("")

    # Per token
    try:
        from research.research_manager import load_index
        index = load_index() or {}
        sf = {}
        try:
            from db.connection import execute
            rows = execute("SELECT token, conviction_score FROM sunflow_conviction ORDER BY conviction_score DESC", fetch=True)
            for r in (rows or []):
                sf[r[0]] = r[1]
        except Exception:
            pass

        lines.append("\U0001f6a6 <b>TOKEN TRAFFIC LIGHTS</b>")
        order = ["BTC", "SOL", "HYPE", "JUP", "RENDER", "SUI", "BONK", "PUMP", "PENGU", "FARTCOIN"]
        for tok in order:
            info = index.get(tok, {})
            score = info.get("score")
            sf_val = sf.get(tok)

            if nimbus_verdict == "ACCUMULATE" and score and score >= 70:
                action, icon = "ACCUMULATE \U0001f4b0", "\U0001f7e2"
            elif nimbus_verdict == "DE-RISK" and (not score or score < 80):
                action, icon = "REDUCE \U0001f53b", "\U0001f534"
            elif score and score >= 75:
                action, icon = "HOLD \u23f8\ufe0f", "\U0001f7e1"
            elif score and score >= 60:
                action, icon = "WATCH \U0001f440", "\U0001f7e1"
            else:
                action, icon = "HOLD", "\u26aa"

            detail = ""
            if score: detail += " DD:%s" % score
            if sf_val: detail += " SF:%.0f" % sf_val
            lines.append("  %s <b>%s</b> \u2014 %s%s" % (icon, tok, action, detail))
    except Exception as e:
        log.error("Token actions failed: %s", e)

    return "\n".join(lines)
