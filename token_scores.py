"""Token Score Auto-Update — Task 18 of Fiery Eyes v5.1

Split static (40%) / dynamic (60%). Dynamic auto-recalculates daily from:
- DeFiLlama revenue trend (15%)
- Price momentum vs ATH (15%)
- Smart money activity (15%)
- Supply health / distribution penalty (15%)

Static scores are manually assessed and updated infrequently.
"""

import json
import requests
from datetime import datetime, timezone
from config import COINGECKO_API_KEY, get_logger
from db.connection import execute

log = get_logger("token_scores")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["📊 Intel", "🐋 Signals", "💼 Portfolio", "📈 Market", "🔧 Tools"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# ---------------------------------------------------------------------------
# Static scores (40% weight) — manually assessed, change slowly
# ---------------------------------------------------------------------------
STATIC_SCORES = {
    # {token: {team, tokenomics_structure, narrative_fit, ecosystem_position}} each 1-10
    "JUP": {"team": 8.5, "tokenomics_structure": 9, "narrative_fit": 7, "ecosystem_position": 9},   # 8.4 avg
    "HYPE": {"team": 10, "tokenomics_structure": 5, "narrative_fit": 8, "ecosystem_position": 9},    # 8.0 avg
    "RENDER": {"team": 9, "tokenomics_structure": 7, "narrative_fit": 9, "ecosystem_position": 7},   # 8.0 avg
    "BONK": {"team": 6, "tokenomics_structure": 8, "narrative_fit": 6, "ecosystem_position": 7},     # 6.8 avg
    "PUMP": {"team": 5, "tokenomics_structure": 4, "narrative_fit": 7, "ecosystem_position": 6},     # 5.5 avg
    "PENGU": {"team": 8, "tokenomics_structure": 5, "narrative_fit": 6, "ecosystem_position": 5},    # 6.0 avg
    "FARTCOIN": {"team": 2, "tokenomics_structure": 9, "narrative_fit": 4, "ecosystem_position": 3}, # 4.5 avg
}

# ATH values for momentum calculation
ATH_PRICES = {
    "JUP": 2.00, "HYPE": 59, "RENDER": 13.59, "BONK": 0.000059,
    "PUMP": 0.025, "PENGU": 0.05, "FARTCOIN": 2.64,
}

# Revenue protocol slugs on DeFiLlama
REVENUE_SLUGS = {
    "HYPE": "hyperliquid",
    "JUP": "jupiter",
    "PUMP": "pump.fun",
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS token_scores (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            token TEXT NOT NULL,
            static_score REAL,
            dynamic_score REAL,
            total_score REAL,
            revenue_component REAL,
            momentum_component REAL,
            smart_money_component REAL,
            supply_component REAL,
            details JSONB,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (date, token)
        )
    """)


# ---------------------------------------------------------------------------
# Dynamic component calculators
# ---------------------------------------------------------------------------
def calc_revenue_score(token: str) -> float:
    """Revenue/utility score from DeFiLlama (0-10)."""
    slug = REVENUE_SLUGS.get(token)
    if not slug:
        # No revenue tokens: BONK=3, PENGU=1, FARTCOIN=1
        return {"BONK": 3, "PENGU": 1, "FARTCOIN": 1, "RENDER": 5}.get(token, 2)

    try:
        resp = requests.get(
            f"https://api.llama.fi/summary/fees/{slug}?dataType=dailyRevenue",
            timeout=10)
        resp.raise_for_status()
        d = resp.json()
        rev_24h = d.get("total24h", 0) or 0
        rev_30d = d.get("total30d", 0) or 0
        annualised = rev_30d * 12

        # Score based on annualised revenue
        if annualised >= 500_000_000: return 10    # >$500M/yr
        if annualised >= 200_000_000: return 9     # >$200M/yr
        if annualised >= 100_000_000: return 8     # >$100M/yr
        if annualised >= 50_000_000: return 7      # >$50M/yr
        if annualised >= 20_000_000: return 6
        if annualised >= 5_000_000: return 5
        if annualised >= 1_000_000: return 4
        return 3
    except Exception as e:
        log.warning("Revenue score for %s failed: %s", token, e)
        return 3


def calc_momentum_score(token: str) -> float:
    """Price momentum score — how far from ATH and recent trend (0-10).
    
    Deep value = higher score (contrarian: beaten down = opportunity).
    """
    ath = ATH_PRICES.get(token)
    if not ath:
        return 5

    try:
        cg_ids = {
            "JUP": "jupiter-exchange-solana", "HYPE": "hyperliquid",
            "RENDER": "render-token", "BONK": "bonk",
            "PUMP": "pump-fun", "PENGU": "pudgy-penguins", "FARTCOIN": "fartcoin",
        }
        cg_id = cg_ids.get(token)
        if not cg_id:
            return 5

        headers = {"x-cg-demo-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
        resp = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd", "include_24hr_change": "true"},
            headers=headers, timeout=10)
        data = resp.json().get(cg_id, {})
        price = data.get("usd")
        if not price:
            return 5

        pct_from_ath = (1 - price / ath) * 100

        # Contrarian scoring: deeper value = higher score
        if pct_from_ath >= 90: return 9    # >90% down = extreme deep value
        if pct_from_ath >= 80: return 8
        if pct_from_ath >= 70: return 7
        if pct_from_ath >= 60: return 6
        if pct_from_ath >= 40: return 5
        if pct_from_ath >= 20: return 4
        return 3  # Near ATH = lower momentum score (less upside)
    except Exception as e:
        log.warning("Momentum score for %s failed: %s", token, e)
        return 5


def calc_smart_money_score(token: str) -> float:
    """Smart money activity score from X signals (0-10)."""
    try:
        rows = execute("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE signal_strength = 'strong'),
                   COUNT(DISTINCT source_handle)
            FROM x_intelligence
            WHERE token_symbol = %s
              AND detected_at > NOW() - INTERVAL '7 days'
        """, (token,), fetch=True)

        if not rows or not rows[0]:
            return 2

        total, strong, sources = rows[0]
        total = total or 0
        strong = strong or 0
        sources = sources or 0

        # Score based on signal volume and quality
        if strong >= 5 and sources >= 3: return 10
        if strong >= 3 or (total >= 10 and sources >= 3): return 8
        if strong >= 1 or total >= 5: return 6
        if total >= 2: return 4
        if total >= 1: return 3
        return 2
    except Exception as e:
        log.warning("Smart money score for %s failed: %s", token, e)
        return 2


def calc_supply_score(token: str) -> float:
    """Supply health score (0-10). Includes distribution penalty."""
    from supply_flow import SUPPLY_DATA, get_distribution_penalties

    data = SUPPLY_DATA.get(token, {})
    circ_pct = data.get("circulating_pct", 50)
    emissions = data.get("daily_emissions", {}).get("total", 0)

    # Base score from circulating %
    if circ_pct >= 90: base = 9
    elif circ_pct >= 70: base = 7
    elif circ_pct >= 50: base = 5
    elif circ_pct >= 30: base = 4
    else: base = 3

    # Bonus for zero emissions
    if emissions == 0:
        base = min(10, base + 1)

    # Penalty for buyback source
    buyback = data.get("buyback_source", "")
    if "buyback" in buyback.lower() or "burn" in buyback.lower():
        base = min(10, base + 0.5)

    # Distribution penalty
    penalties = get_distribution_penalties()
    if token in penalties:
        penalty_pct = penalties[token]["penalty_pct"]
        base = max(1, base - (penalty_pct / 10))
        log.info("Supply penalty for %s: -%d%% (%.1f → %.1f)", token, penalty_pct, base + penalty_pct/10, base)

    return round(base, 1)


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------
def calculate_token_score(token: str) -> dict:
    """Calculate full score for a token: 40% static + 60% dynamic."""
    # Static (40%)
    static = STATIC_SCORES.get(token, {})
    static_avg = sum(static.values()) / len(static) if static else 5
    static_component = static_avg * 0.4

    # Dynamic components (60% total, 15% each)
    revenue = calc_revenue_score(token)
    momentum = calc_momentum_score(token)
    smart_money = calc_smart_money_score(token)
    supply = calc_supply_score(token)

    dynamic_avg = (revenue + momentum + smart_money + supply) / 4
    dynamic_component = dynamic_avg * 0.6

    total = round(static_component + dynamic_component, 1)
    # Scale to /60 for compatibility with spec's X/60 format
    total_60 = round(total * 6, 0)

    return {
        "token": token,
        "static_avg": round(static_avg, 1),
        "static_component": round(static_component, 1),
        "revenue": revenue,
        "momentum": momentum,
        "smart_money": smart_money,
        "supply": supply,
        "dynamic_avg": round(dynamic_avg, 1),
        "dynamic_component": round(dynamic_component, 1),
        "total_10": total,
        "total_60": int(total_60),
        "details": static,
    }


def calculate_all_scores() -> list:
    """Calculate scores for all watchlist tokens."""
    ensure_table()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []

    for token in STATIC_SCORES:
        score = calculate_token_score(token)
        results.append(score)

        # Store
        execute("""
            INSERT INTO token_scores (date, token, static_score, dynamic_score, total_score,
                revenue_component, momentum_component, smart_money_component, supply_component, details)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date, token) DO UPDATE SET
                static_score = EXCLUDED.static_score, dynamic_score = EXCLUDED.dynamic_score,
                total_score = EXCLUDED.total_score, revenue_component = EXCLUDED.revenue_component,
                momentum_component = EXCLUDED.momentum_component,
                smart_money_component = EXCLUDED.smart_money_component,
                supply_component = EXCLUDED.supply_component, details = EXCLUDED.details
        """, (today, token, score["static_avg"], score["dynamic_avg"], score["total_10"],
              score["revenue"], score["momentum"], score["smart_money"], score["supply"],
              json.dumps(score["details"])))

    results.sort(key=lambda s: s["total_60"], reverse=True)
    log.info("Calculated scores for %d tokens", len(results))
    return results


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def format_scores_telegram(scores: list) -> str:
    lines = ["📊 <b>TOKEN SCORES</b> (40% static + 60% dynamic)", ""]
    lines.append("<pre>")
    lines.append(f"{'Token':<9s} {'Score':>5s} {'Rev':>4s} {'Mom':>4s} {'SM':>4s} {'Sup':>4s} {'Stat':>4s}")

    for s in scores:
        lines.append(
            f"{s['token']:<9s} {s['total_60']:>3d}/60 "
            f"{s['revenue']:>4.0f} {s['momentum']:>4.0f} {s['smart_money']:>4.0f} "
            f"{s['supply']:>4.0f} {s['static_avg']:>4.1f}"
        )

    lines.append("</pre>")
    lines.append("Rev=Revenue Mom=Momentum SM=SmartMoney Sup=Supply Stat=Static")
    return "\n".join(lines)


def send_telegram(text: str):
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
    except Exception as e:
        log.error("Telegram error: %s", e)


def run_score_update(send_to_telegram: bool = False) -> list:
    scores = calculate_all_scores()
    msg = format_scores_telegram(scores)
    log.info("Scores:\n%s", msg)
    if send_to_telegram:
        send_telegram(msg)
    return scores


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    scores = run_score_update(send_to_telegram=send_tg)
    print(format_scores_telegram(scores))
