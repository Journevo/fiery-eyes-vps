"""
NIMBUS v2.2 — Compute Engine
All scoring, risk, contradictions, checklist.
Pure functions. No side effects.
"""

from datetime import datetime
from nimbus_config import (
    DOMAINS, CONFIDENCE_FACTORS, FACTOR_MAX, CONFIDENCE_MAX,
    CAP_PERCENTAGE, CAP_CONDITIONS, RISK_BANDS, ACTION_LADDER,
    CYCLE_PHASES, MVRV_BANDS, CONFIDENCE_THRESHOLD_FOR_CHECKLIST,
    CONTRADICTION_MAX_ACTION,
)


# ━━━ SCORING RUBRICS (locked v2.2) ━━━

def score_pmi_regime(data):
    pmi = data["pmi"]["global_weighted"]
    latest = pmi[-1]
    prev = pmi[-2] if len(pmi) >= 2 else None
    prev2 = pmi[-3] if len(pmi) >= 3 else None
    above_50 = latest > 50
    rising_2mo = prev is not None and prev2 is not None and latest > prev > prev2
    rising_1mo = prev is not None and latest > prev
    if above_50 and rising_2mo:    return 5
    if above_50 and rising_1mo:    return 4
    if above_50:                   return 3
    if not above_50 and rising_2mo: return 2
    if not above_50 and rising_1mo: return 1
    return 0


def score_cpi_direction(data):
    cpi = data["cpi"]["us"]
    latest = cpi[-1]
    prev = cpi[-2] if len(cpi) >= 2 else None
    prev2 = cpi[-3] if len(cpi) >= 3 else None
    if latest is None:
        return 2
    falling_3 = prev2 is not None and prev is not None and latest < prev < prev2
    falling_2 = prev is not None and latest < prev and prev2 is not None and prev < prev2
    falling_1 = prev is not None and latest < prev
    below_25 = latest < 2.5
    below_30 = latest < 3.0
    rising = prev is not None and latest > prev
    base = 0
    if falling_3 and below_25:     base = 5
    elif falling_2 and below_30:   base = 4
    elif falling_1 and below_30:   base = 3
    elif not rising and below_30:  base = 2
    elif rising and latest < 3.5:  base = 1
    else:                          base = 0
    oil_yoy = data["commodities"]["wti_oil"]["yoy_pct"]
    if oil_yoy < 0 and falling_1:
        base = min(base + 1, 5)
    elif oil_yoy > 20 and rising:
        base = max(base - 1, 0)
    return base


def score_net_liq_direction(data):
    liq = data["liquidity"]
    net_liqs = []
    for i in range(len(liq["fed_bs_T"])):
        nl = liq["fed_bs_T"][i] - liq["tga_T"][i] - liq["rrp_T"][i]
        net_liqs.append(nl)
    if len(net_liqs) >= 3:
        if net_liqs[-1] > net_liqs[-2] > net_liqs[-3]:
            return 4
        if net_liqs[-1] > net_liqs[-2]:
            return 3
        if abs(net_liqs[-1] - net_liqs[-2]) < 0.02:
            return 2
        if net_liqs[-1] < net_liqs[-2] < net_liqs[-3]:
            return 0
        return 1
    return 0


def score_m2_growth(data):
    m2_yoy = data["liquidity"]["global_m2_yoy"]
    latest = m2_yoy[-1]
    prev = m2_yoy[-2] if len(m2_yoy) >= 2 else None
    rising = prev is not None and latest > prev
    if latest > 5 and rising:    return 5
    if latest > 3 and rising:    return 4
    if latest > 3:               return 3
    if latest > 0:               return 2
    if latest < 0 and rising:    return 1
    return 0


def score_dxy_direction(data):
    vals = data["dxy"]["values"]
    if len(vals) < 2:
        return 2
    oldest = vals[0]
    latest = vals[-1]
    pct = ((latest - oldest) / oldest) * 100
    if pct <= -10:  return 5
    if pct <= -5:   return 4
    if pct <= -2:   return 3
    if abs(pct) < 2: return 2
    if pct <= 5:    return 1
    return 0


def score_btc_technicals(data):
    c = data["crypto"]
    golden = c["sma_20_above_50"]
    above_200 = not c.get("below_200ma", True)
    turning_up = c.get("sma_20_turning_up", False)
    lower_lows = c.get("making_lower_lows", False)
    if golden and above_200:                return 5
    if golden:                              return 4
    if not golden and turning_up:           return 3
    if not golden and not lower_lows:       return 2
    if not golden and not above_200 and lower_lows: return 0
    return 1


def score_btc_valuation(data):
    z = data["crypto"]["mvrv_z"]
    for low, high, _label, score in MVRV_BANDS:
        if low <= z < high:
            return score
    return 2


def score_yield_curve(data):
    y = data["yields"]
    spread = y["spread_10_2"][-1]
    driver = y.get("steepening_driver", "")
    two_y_falling = "2Y falling" in driver or "cuts" in driver.lower()
    if spread > 0.50 and two_y_falling: return 5
    if spread > 0.25 and two_y_falling: return 4
    if spread > 0:                       return 3
    if -0.25 <= spread <= 0:            return 2
    if -0.75 <= spread < -0.25:         return 1
    return 0


def score_real_rate_room(data):
    rr = data["real_rates"]["us"]
    if rr < 0:    return 5
    if rr < 0.5:  return 4
    if rr < 1.0:  return 3
    if rr < 1.5:  return 2
    if rr < 2.0:  return 1
    return 0


SCORERS = {
    "pmi_regime":        score_pmi_regime,
    "cpi_direction":     score_cpi_direction,
    "net_liq_direction": score_net_liq_direction,
    "m2_growth":         score_m2_growth,
    "dxy_direction":     score_dxy_direction,
    "btc_technicals":    score_btc_technicals,
    "btc_valuation":     score_btc_valuation,
    "yield_curve":       score_yield_curve,
    "real_rate_room":    score_real_rate_room,
}


# ━━━ COMPUTE FUNCTIONS ━━━

def compute_all_scores(data):
    return {f: SCORERS[f](data) for f in CONFIDENCE_FACTORS}


def compute_confidence(scores):
    raw = sum(scores.values())
    cap_active = all(scores.get(f, 0) == 0 for f in CAP_CONDITIONS)
    cap_value = int(CONFIDENCE_MAX * CAP_PERCENTAGE)
    effective = min(raw, cap_value) if cap_active else raw
    return {
        "raw": raw, "max": CONFIDENCE_MAX, "cap_value": cap_value,
        "cap_active": cap_active, "effective": effective,
        "pct": round(effective / CONFIDENCE_MAX * 100, 1),
    }


def compute_risk_meter(scores):
    subs = {}
    for domain, factors in DOMAINS.items():
        ds = sum(scores.get(f, 0) for f in factors)
        dm = len(factors) * FACTOR_MAX
        subs[domain] = round(ds / dm * 25, 1)
    total = max(0, min(100, round(sum(subs.values()))))
    band_name, band_action = "UNKNOWN", ""
    for lo, hi, name, action in RISK_BANDS:
        if lo <= total <= hi:
            band_name, band_action = name, action
            break
    return {"value": total, "band": band_name, "action": band_action, "sub_scores": subs}


def compute_domain_status(scores):
    statuses = {}
    confirmed = 0
    for domain, factors in DOMAINS.items():
        fs = {f: scores.get(f, 0) for f in factors}
        avg = sum(fs.values()) / len(factors) if factors else 0
        has_high = any(s >= 3 for s in fs.values())
        has_low = any(s <= 1 for s in fs.values())
        is_split = has_high and has_low
        if avg >= 3 and not is_split:
            status = "CONFIRMED"
            confirmed += 1
        elif is_split:
            status = "SPLIT"
        else:
            status = "NOT_CONFIRMED"
        statuses[domain] = {"status": status, "factors": fs, "avg": round(avg, 1)}
    return statuses, confirmed


def compute_contradictions(data, scores):
    cs = []
    if scores.get("pmi_regime", 0) >= 3 and scores.get("btc_technicals", 0) == 0:
        cs.append("PMI expanding WHILE BTC death cross + Net Liq falling")
    hy = data["yields"]["hy_spread"][-1]
    sp = data["indices"]["sp500"]["ytd"]
    nk = data["indices"]["nikkei"]["ytd"]
    if hy < 3.0 and sp < nk:
        cs.append(f"HY spreads ATL ({hy}%) WHILE US underperforms intl")
    truf = data["truflation"]["current"]
    uk_cpi = next((v for v in reversed(data["cpi"]["uk"]) if v is not None), None)
    if truf is not None and uk_cpi is not None and abs(truf - uk_cpi) > 2.0:
        cs.append(f"Truflation {truf}% WHILE UK CPI {uk_cpi}%")
    btc = data["crypto"]["btc_price"][-1]
    btc_ath = data["crypto"]["btc_ath"]
    dd = (btc_ath - btc) / btc_ath * 100
    if scores.get("m2_growth", 0) >= 3 and dd > 30:
        cs.append(f"Global M2 ATH WHILE BTC -{dd:.0f}% from ATH")
    return cs


def compute_cycle(data):
    cross = datetime.strptime(data["cycle"]["pmi_crossed_50_date"], "%Y-%m-%d").date()
    now = datetime.strptime(data["meta"]["as_of_date"], "%Y-%m-%d").date()
    if now < cross:
        return {"season": data["cycle"]["season"], "phase": "PRE", "week": 0, "start": str(cross)}
    week = (now - cross).days // 7 + 1
    phase = "UNKNOWN"
    for lo, hi, name in CYCLE_PHASES:
        if lo <= week <= hi:
            phase = name
            break
    return {"season": data["cycle"]["season"], "phase": phase, "week": week, "start": str(cross)}


def compute_m2_lag(data):
    liq = data["liquidity"]
    inf_date = datetime.strptime(liq["m2_inflection_month"] + "-15", "%Y-%m-%d").date()
    now = datetime.strptime(data["meta"]["as_of_date"], "%Y-%m-%d").date()
    days = (now - inf_date).days
    lo, hi = liq["btc_lag_range_days"]
    if days < lo:
        status = "PENDING"
    elif days <= hi:
        status = "WINDOW_OPEN"
    else:
        status = "OPEN_DELAYED" if score_net_liq_direction(data) <= 1 else "EXPIRED"
    return {
        "inflection_month": liq["m2_inflection_month"],
        "days_since": days, "lag_range": [lo, hi], "status": status,
        "definition": liq["m2_inflection_definition"],
    }


def compute_net_liq(data):
    liq = data["liquidity"]
    vals = [round(liq["fed_bs_T"][i] - liq["tga_T"][i] - liq["rrp_T"][i], 2)
            for i in range(len(liq["fed_bs_T"]))]
    bc = {
        "net_liq_improving_2": len(vals) >= 3 and vals[-1] > vals[-2] > vals[-3],
        "fed_bs_stopped": len(liq["fed_bs_T"]) >= 2 and liq["fed_bs_T"][-1] >= liq["fed_bs_T"][-2],
        "tga_not_rising": len(liq["tga_T"]) >= 2 and liq["tga_T"][-1] <= liq["tga_T"][-2],
    }
    return {"values_T": vals, "months": liq["months"],
            "bottom_conditions": bc, "bottom_confirmed": all(bc.values())}


def compute_checklist(data, scores, confidence):
    threshold = int(CONFIDENCE_MAX * CONFIDENCE_THRESHOLD_FOR_CHECKLIST)
    nl = compute_net_liq(data)
    items = {
        "btc_golden_cross": data["crypto"]["sma_20_above_50"],
        "net_liq_bottom": nl["bottom_confirmed"],
        "confidence_above_threshold": confidence["effective"] > threshold,
        "mstr_mnav_above_1": data["stocks"]["mstr"]["mnav"] > 1.0,
    }
    met = sum(1 for v in items.values() if v)
    key = min(met, max(ACTION_LADDER.keys()))
    name, desc = ACTION_LADDER[key]
    return {"items": items, "met": met, "total": len(items),
            "action": name, "action_desc": desc, "threshold": threshold}


def compute_final_action(checklist, contradictions, domain_count):
    idx = checklist["met"]
    if len(contradictions) >= 2:
        idx = min(idx, CONTRADICTION_MAX_ACTION)
    if domain_count < 2 and idx >= 2:
        idx = 1
    name, desc = ACTION_LADDER[idx]
    overrides = []
    if len(contradictions) >= 2:
        overrides.append(f"{len(contradictions)} contradictions active")
    if domain_count < 2 and checklist["met"] >= 2:
        overrides.append(f"only {domain_count} domain(s) confirmed")
    return {"action": name, "action_desc": desc, "action_idx": idx, "overrides": overrides}


def compute_liq_momentum(scores):
    nl = scores.get("net_liq_direction", 0)
    m2 = scores.get("m2_growth", 0)
    short = "up" if nl >= 3 else ("flat" if nl == 2 else "dn")
    med = "up" if m2 >= 3 else ("flat" if m2 == 2 else "dn")
    return {"short": short, "medium": med,
            "display": f"{'↗' if nl>=3 else '→' if nl==2 else '↘'}{'↗' if m2>=3 else '→' if m2==2 else '↘'}"}


def compute_decision_log(fa, scores, contradictions, cycle):
    drivers = []
    if scores.get("net_liq_direction", 0) <= 1: drivers.append("NetLiq dn")
    if scores.get("btc_technicals", 0) == 0: drivers.append("DeathCross")
    if len(contradictions) >= 2: drivers.append(f"{len(contradictions)} contradictions")
    positives = []
    if scores.get("pmi_regime", 0) >= 3: positives.append("PMI>50")
    if scores.get("m2_growth", 0) >= 3: positives.append("M2 ATH")
    return (f"Action={fa['action']}. Driver: {' + '.join(drivers) or 'none'} "
            f"despite {' + '.join(positives) or 'none'}.")


def compute_nimbus(data):
    scores = compute_all_scores(data)
    confidence = compute_confidence(scores)
    risk_meter = compute_risk_meter(scores)
    domains, dc = compute_domain_status(scores)
    contradictions = compute_contradictions(data, scores)
    cycle = compute_cycle(data)
    m2_lag = compute_m2_lag(data)
    net_liq = compute_net_liq(data)
    lm = compute_liq_momentum(scores)
    checklist = compute_checklist(data, scores, confidence)
    fa = compute_final_action(checklist, contradictions, dc)
    dl = compute_decision_log(fa, scores, contradictions, cycle)
    return {
        "scores": scores, "confidence": confidence, "risk_meter": risk_meter,
        "domains": domains, "domain_confirmed_count": dc,
        "contradictions": contradictions, "cycle": cycle, "m2_lag": m2_lag,
        "net_liq": net_liq, "liq_momentum": lm, "checklist": checklist,
        "final_action": fa, "decision_log": dl,
    }
