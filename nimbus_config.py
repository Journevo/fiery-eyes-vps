"""
NIMBUS v2.2 — Configuration & Rubrics
Locked. No changes without version bump.
"""

VERSION = "2.2"

DOMAINS = {
    "CYCLE":      ["pmi_regime", "cpi_direction"],
    "LIQUIDITY":  ["net_liq_direction", "m2_growth", "dxy_direction"],
    "TECHNICALS": ["btc_technicals", "btc_valuation"],
    "CONDITIONS": ["yield_curve", "real_rate_room"],
}

CONFIDENCE_FACTORS = [
    "pmi_regime", "cpi_direction",
    "net_liq_direction", "m2_growth", "dxy_direction",
    "btc_technicals", "btc_valuation",
    "yield_curve", "real_rate_room",
]
FACTOR_MAX = 5
CONFIDENCE_MAX = len(CONFIDENCE_FACTORS) * FACTOR_MAX

CAP_PERCENTAGE = 0.60
CAP_CONDITIONS = ["net_liq_direction", "btc_technicals"]

RISK_BANDS = [
    (0,  25, "FEAR",      "deploy aggressive"),
    (26, 50, "CAUTIOUS",  "starter size only"),
    (51, 75, "TREND",     "full size / scale"),
    (76, 100, "EUPHORIA", "take profit / de-risk"),
]

ACTION_LADDER = {
    0: ("WAIT",       "no new risk"),
    1: ("WATCHLIST",  "prepare entries"),
    2: ("STARTER",    "25% intended size"),
    3: ("FULL SIZE",  "deploy conviction"),
    4: ("AGGRESSIVE", "trend mode, scale"),
}

CYCLE_PHASES = [
    (1,  12, "EARLY"),
    (13, 26, "MID"),
    (27, 999, "LATE"),
]

MVRV_BANDS = [
    (-999, 0,   "DEEP VALUE",    5),
    (0,    1,   "VALUE ZONE",    4),
    (1,    2,   "FAIR-LOWER",    3),
    (2,    3,   "FAIR-UPPER",    2),
    (3,    5,   "OVERHEATED",    1),
    (5,    999, "BLOW-OFF TOP",  0),
]

CHECKLIST_ITEMS = [
    "btc_golden_cross",
    "net_liq_bottom",
    "confidence_above_threshold",
    "mstr_mnav_above_1",
]
CONFIDENCE_THRESHOLD_FOR_CHECKLIST = 0.60

CONTRADICTION_MAX_ACTION = 2
