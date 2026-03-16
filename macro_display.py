"""macro_display.py — Full Nimbus macro output with emoji formatting."""
from config import get_logger

log = get_logger("macro_display")


def _arrow(vals):
    if not vals or len(vals) < 2 or vals[-1] is None or vals[-2] is None:
        return ""
    d = vals[-1] - vals[-2]
    if len(vals) >= 3 and vals[-3] is not None:
        d2 = vals[-2] - vals[-3]
        if d > 0 and d2 > 0: return " \U0001f525"  # fire = accelerating
        if d < 0 and d2 < 0: return " \u2935\ufe0f"  # double down
    if d > 0.01: return " \U0001f53a"  # red triangle up
    if d < -0.01: return " \U0001f53b"  # red triangle down
    return " \u2192"  # arrow right


def _v(val, fmt="%.1f", none="?"):
    if val is None: return none
    return fmt % val


def generate_macro_signal(data, state):
    c = state["cycle"]
    conf = state["confidence"]
    rm = state["risk_meter"]
    lm = state["liq_momentum"]
    fa = state["final_action"]
    contras = state["contradictions"]
    m2l = state["m2_lag"]
    scores = state["scores"]

    season_emoji = {"SUMMER": "\u2600\ufe0f", "SPRING": "\U0001f331", "AUTUMN": "\U0001f342", "WINTER": "\u2744\ufe0f"}.get(c["season"], "\u2753")

    lines = []
    lines.append("\U0001f30d <b>MACRO DASHBOARD</b> \u2014 %s" % data.get("meta", {}).get("as_of_date", "?"))
    lines.append("")
    lines.append("%s Season: <b>%s</b> | Cycle: %s %s (Week %s)" % (season_emoji, c["season"], c["phase"], season_emoji, c["week"]))
    lines.append("\U0001f3af Confidence: <b>%s/%s</b> (%s%%)" % (conf["effective"], conf["max"], conf["pct"]))

    rm_emoji = "\U0001f534" if rm["value"] < 25 else "\U0001f7e1" if rm["value"] < 50 else "\U0001f7e2" if rm["value"] < 75 else "\U0001f525"
    lines.append("%s Risk Meter: <b>%s/100</b> \u2014 %s (%s)" % (rm_emoji, rm["value"], rm["band"], rm["action"]))
    lines.append("\U0001f4ca Cap: %s max (%s%%)" % (conf["cap_value"], int(conf["cap_value"] / conf["max"] * 100)))

    short_e = {"\u2191": "\U0001f53a", "-": "\u2192", "\u2193": "\U0001f53b"}.get(lm.get("short", ""), "\u2753")
    med_e = {"\u2191": "\U0001f53a", "-": "\u2192", "\u2193": "\U0001f53b"}.get(lm.get("medium", ""), "\u2753")
    lines.append("\U0001f4a7 Liq Momentum: short %s  medium %s" % (lm["display"][0], lm["display"][1]))
    lines.append("\u23f1 M2 Lag: Day %s (%s)" % (m2l["days_since"], m2l["status"]))
    lines.append("")

    # Scores
    lines.append("\U0001f4ca <b>CONFIDENCE SCORES</b>")
    for f, s in scores.items():
        label = f.replace("_", " ").title()
        icon = "\u2705" if s >= 4 else "\u26a0\ufe0f" if s >= 2 else "\u274c"
        bar = "\u25cf" * s + "\u25cb" * (5 - s)
        lines.append("  %s %-20s %s  %d/5" % (icon, label, bar, s))
    lines.append("")

    # Checklist
    cl = state["checklist"]
    lines.append("\U0001f6a6 <b>GREEN LIGHT: %d/%d</b>" % (cl["met"], cl["total"]))
    for k, v in cl["items"].items():
        icon = "\u2611\ufe0f" if v else "\u2610"
        lines.append("  %s %s" % (icon, k.replace("_", " ").title()))
    lines.append("")

    # Contradictions
    if contras:
        lines.append("\u26a0\ufe0f <b>CONTRADICTIONS (%d)</b>" % len(contras))
        for ct in contras:
            lines.append("  \U0001f534 %s" % ct)
        lines.append("")

    # What Changed
    wc = data.get("what_changed", {})
    if wc:
        lines.append("\U0001f4a5 <b>WHAT CHANGED</b>")
        cat_emoji = {"regime_shifts": "\U0001f500", "inflation": "\U0001f4b0", "risk_assets": "\U0001f4c9", "property": "\U0001f3e0"}
        for category, items in wc.items():
            e = cat_emoji.get(category, "\U0001f4cc")
            lines.append("  %s <b>%s</b>" % (e, category.upper().replace("_", " ")))
            for item in (items if isinstance(items, list) else [items]):
                lines.append("    \u2022 %s" % item)
        lines.append("")

    # Key Dates
    kd = data.get("key_dates", [])
    if kd:
        lines.append("\U0001f4c5 <b>KEY DATES</b>")
        for d in kd:
            if isinstance(d, dict):
                lines.append("  \u23f0 %s: %s" % (d.get("date", "?"), d.get("event", "?")))

    return "\n".join(lines)


def generate_pmi_table(data):
    pmi = data.get("pmi", {})
    months = pmi.get("months", [])
    lines = []
    lines.append("\U0001f3ed <b>PMI MANUFACTURING</b>")
    lines.append("<pre>        %s</pre>" % "  ".join("%-5s" % m for m in months))

    flags = [("\U0001f1fa\U0001f1f8 US  ", "us"), ("\U0001f1e8\U0001f1f3 CN  ", "cn"),
             ("\U0001f1ea\U0001f1fa EU  ", "eu"), ("\U0001f1ef\U0001f1f5 JP  ", "jp"),
             ("\U0001f30d Wgtd", "global_weighted")]
    for flag, key in flags:
        vals = pmi.get(key, [])
        row = "  ".join(_v(v, "%5.1f") for v in vals)
        lines.append("%s  %s%s" % (flag, row, _arrow(vals)))

    if pmi.get("us_services"):
        lines.append("\U0001f1fa\U0001f1f8 US Services: %s" % pmi["us_services"])
    lines.append("\U0001f4c5 as_of: %s" % pmi.get("as_of", "?"))
    return "\n".join(lines)


def generate_cpi_table(data):
    cpi = data.get("cpi", {})
    truf = data.get("truflation", {})
    months = cpi.get("months", [])
    lines = []
    lines.append("\U0001f4b0 <b>CPI INFLATION</b>")
    lines.append("<pre>        %s</pre>" % "  ".join("%-5s" % m for m in months))

    flags = [("\U0001f1fa\U0001f1f8 US  ", "us"), ("\U0001f1fa\U0001f1f8 Core", "us_core"),
             ("\U0001f1e8\U0001f1f3 CN  ", "cn"), ("\U0001f1ea\U0001f1fa EU  ", "eu"),
             ("\U0001f1ef\U0001f1f5 JP  ", "jp"), ("\U0001f1ec\U0001f1e7 UK  ", "uk")]
    for flag, key in flags:
        vals = cpi.get(key, [])
        row = "  ".join(_v(v, "%5.1f%%", "  TBD") for v in vals)
        lines.append("%s  %s%s" % (flag, row, _arrow(vals)))

    lines.append("")
    lines.append("\U0001f50d Truflation: %s%% | BLS: %s%%" % (truf.get("current", "?"), truf.get("bls_cpi", "?")))
    gap = None
    if truf.get("bls_cpi") and truf.get("current"):
        gap = round(truf["bls_cpi"] - truf["current"], 2)
    lines.append("\u26a0\ufe0f Gap: %s%% (largest in dataset)" % (gap if gap is not None else "?"))
    return "\n".join(lines)


def generate_gdp_table(data):
    gdp = data.get("gdp", {})
    quarters = gdp.get("quarters", [])
    lines = []
    lines.append("\U0001f4c8 <b>GDP GROWTH (QoQ%%)</b>")
    lines.append("<pre>        %s</pre>" % " ".join("%6s" % q for q in quarters))

    for flag, key in [("\U0001f1fa\U0001f1f8", "us"), ("\U0001f1e8\U0001f1f3", "cn"),
                      ("\U0001f1ea\U0001f1fa", "eu"), ("\U0001f1ef\U0001f1f5", "jp"),
                      ("\U0001f1ec\U0001f1e7", "uk")]:
        vals = gdp.get(key, [])
        row = " ".join("%+5.1f%%" % v for v in vals)
        lines.append("%s    %s" % (flag, row))

    fy = gdp.get("fy2025", {})
    if fy:
        lines.append("")
        lines.append("\U0001f4ca FY2025: \U0001f1fa\U0001f1f8 %s%% | \U0001f1e8\U0001f1f3 %s%% | \U0001f1ea\U0001f1fa %s%%" % (
            fy.get("us", "?"), fy.get("cn", "?"), fy.get("eu", "?")))
    return "\n".join(lines)


def generate_key_dates(data):
    kd = data.get("key_dates", [])
    geo = data.get("geopolitics", {})
    lines = ["\U0001f4c5 <b>KEY DATES & EVENTS</b>"]
    if geo.get("iran_war"):
        lines.append("\U0001f1ee\U0001f1f7 Iran war: <b>%s</b>" % geo["iran_war"])
        lines.append("")
    for d in kd:
        if isinstance(d, dict):
            lines.append("\u23f0 %s: %s" % (d.get("date", "?"), d.get("event", "?")))
    return "\n".join(lines)
