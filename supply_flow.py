"""Supply Flow Monitor — Task 12 of Fiery Eyes v5.1

Tracks token-specific supply dynamics:
- HYPE: DeFiLlama revenue → buyback estimate vs known emissions (27K staking + 40K team/day)
- JUP: governance watch. Alert on emission proposals.
- PUMP: countdown to Jul 12 cliff. Alert at 90/60/30/14/7/3/1 days.
- RENDER: monthly burn rate tracking.

Distribution penalty: if unlock <45 days away → auto score penalty.
"""

import requests
from datetime import datetime, date, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("supply_flow")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# ---------------------------------------------------------------------------
# Supply constants (updated manually when data changes)
# ---------------------------------------------------------------------------
SUPPLY_DATA = {
    "HYPE": {
        "circulating_pct": 24,
        "daily_emissions": {
            "staking_rewards": 27_000,  # HYPE/day
            "team_vesting": 40_000,     # HYPE/day
            "total": 67_000,            # HYPE/day
        },
        "buyback_source": "97% of protocol revenue",
        "fdv_risk": "4x FDV/MCap — watch for dilution acceleration",
        "next_cliff": None,
    },
    "JUP": {
        "circulating_pct": 100,  # Zero emissions — all circulating
        "daily_emissions": {"total": 0},
        "buyback_source": "fee revenue buyback program",
        "fdv_risk": "None — zero emissions, fully circulating",
        "next_cliff": None,
        "governance_url": "https://discuss.jup.ag",
        "watch": "Governance proposals for new emissions",
    },
    "PUMP": {
        "circulating_pct": 59,
        "daily_emissions": {"total": 0},  # Linear until cliff
        "buyback_source": "$45M/mo revenue buyback",
        "fdv_risk": "Jul 12 2026 CLIFF — massive unlock",
        "next_cliff": {
            "date": "2026-07-12",
            "description": "Major token unlock cliff",
            "pct_of_supply": 41,  # Remaining 41% unlocks
        },
    },
    "RENDER": {
        "circulating_pct": 78,
        "daily_emissions": {"total": 0},  # Burn-Mint Equilibrium
        "buyback_source": "Burn-Mint Equilibrium — tokens burned on GPU job payment",
        "fdv_risk": "Moderate — BME keeps inflation in check",
        "next_cliff": None,
        "watch": "Monthly burn rate from GPU rendering jobs",
    },
    "BONK": {
        "circulating_pct": 94,
        "daily_emissions": {"total": 0},
        "buyback_source": "LetsBonk fees → burns",
        "fdv_risk": "Low — 94% circulating, burn-only",
        "next_cliff": None,
    },
}

# Alert thresholds for PUMP cliff countdown
PUMP_CLIFF_DATE = date(2026, 7, 12)
CLIFF_ALERT_DAYS = [90, 60, 45, 30, 14, 7, 3, 1]

# Distribution penalty threshold
UNLOCK_PENALTY_DAYS = 45


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_table():
    execute("""
        CREATE TABLE IF NOT EXISTS supply_flow (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            token TEXT NOT NULL,
            net_flow_usd REAL,
            daily_emission_tokens REAL,
            daily_buyback_usd REAL,
            net_dilution_usd REAL,
            cliff_days_remaining INTEGER,
            notes TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


# ---------------------------------------------------------------------------
# HYPE: revenue → buyback vs emissions
# ---------------------------------------------------------------------------
def calc_hype_supply_flow(hype_price: float | None = None) -> dict:
    """Calculate HYPE net supply flow from revenue vs emissions."""
    # Get HYPE revenue from DeFiLlama
    try:
        resp = requests.get(
            "https://api.llama.fi/summary/fees/hyperliquid?dataType=dailyRevenue",
            timeout=15)
        resp.raise_for_status()
        rev_24h = resp.json().get("total24h", 0)
    except Exception:
        rev_24h = 0

    # 97% goes to buyback
    daily_buyback_usd = rev_24h * 0.97

    # Get HYPE price for emission USD value
    if hype_price is None:
        try:
            resp = requests.get(
                "https://api.coingecko.com/api/v3/simple/price?ids=hyperliquid&vs_currencies=usd",
                timeout=10)
            hype_price = resp.json().get("hyperliquid", {}).get("usd", 37)
        except Exception:
            hype_price = 37

    emissions = SUPPLY_DATA["HYPE"]["daily_emissions"]
    daily_emission_usd = emissions["total"] * hype_price

    net_flow = daily_buyback_usd - daily_emission_usd

    return {
        "token": "HYPE",
        "daily_revenue_usd": rev_24h,
        "daily_buyback_usd": daily_buyback_usd,
        "daily_emission_tokens": emissions["total"],
        "daily_emission_usd": daily_emission_usd,
        "net_flow_usd": net_flow,
        "net_positive": net_flow > 0,
        "hype_price": hype_price,
    }


# ---------------------------------------------------------------------------
# PUMP: cliff countdown
# ---------------------------------------------------------------------------
def calc_pump_cliff() -> dict:
    """Calculate PUMP cliff countdown and penalty status."""
    today = date.today()
    days_remaining = (PUMP_CLIFF_DATE - today).days

    # Check if we should alert
    should_alert = days_remaining in CLIFF_ALERT_DAYS
    penalty_active = days_remaining <= UNLOCK_PENALTY_DAYS

    return {
        "token": "PUMP",
        "cliff_date": PUMP_CLIFF_DATE.isoformat(),
        "days_remaining": days_remaining,
        "should_alert": should_alert,
        "penalty_active": penalty_active,
        "penalty_reason": f"Unlock in {days_remaining}d — distribution penalty active" if penalty_active else None,
        "pct_unlocking": SUPPLY_DATA["PUMP"]["next_cliff"]["pct_of_supply"],
    }


# ---------------------------------------------------------------------------
# Distribution penalty calculator
# ---------------------------------------------------------------------------
def get_distribution_penalties() -> dict:
    """Check all tokens for upcoming unlocks and apply penalties.

    Returns {token: penalty_info} for tokens with active penalties.
    """
    penalties = {}

    for symbol, data in SUPPLY_DATA.items():
        cliff = data.get("next_cliff")
        if not cliff or not cliff.get("date"):
            continue

        cliff_date = date.fromisoformat(cliff["date"])
        days_remaining = (cliff_date - date.today()).days

        if days_remaining <= UNLOCK_PENALTY_DAYS:
            # Scale penalty: closer = higher penalty
            if days_remaining <= 7:
                penalty_pct = 30
            elif days_remaining <= 14:
                penalty_pct = 25
            elif days_remaining <= 30:
                penalty_pct = 20
            else:
                penalty_pct = 10

            penalties[symbol] = {
                "days_remaining": days_remaining,
                "penalty_pct": penalty_pct,
                "cliff_date": cliff["date"],
                "pct_unlocking": cliff.get("pct_of_supply", 0),
                "description": cliff.get("description", "Token unlock"),
            }
            log.info("Distribution penalty: %s -%d%% (cliff in %dd)", symbol, penalty_pct, days_remaining)

    return penalties


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def _fmt_usd(v: float) -> str:
    if abs(v) >= 1e6: return f"${v/1e6:.1f}M"
    if abs(v) >= 1e3: return f"${v/1e3:.0f}K"
    return f"${v:,.0f}"


def format_supply_telegram(hype_flow: dict, pump_cliff: dict, penalties: dict) -> str:
    """Format supply flow data for Telegram."""
    lines = ["🔄 <b>SUPPLY FLOW</b>", ""]

    # HYPE
    net = hype_flow.get("net_flow_usd", 0)
    arrow = "🟢 net positive" if net > 0 else "🔴 net negative"
    lines.append(
        f"<b>HYPE:</b> {arrow}\n"
        f"  Buyback: {_fmt_usd(hype_flow.get('daily_buyback_usd', 0))}/day (97% of {_fmt_usd(hype_flow.get('daily_revenue_usd', 0))} rev)\n"
        f"  Emissions: {hype_flow.get('daily_emission_tokens', 0):,.0f} HYPE/day ({_fmt_usd(hype_flow.get('daily_emission_usd', 0))})\n"
        f"  Net: {_fmt_usd(net)}/day"
    )

    # JUP
    lines.append(f"\n<b>JUP:</b> 🟢 Zero emissions, buyback from fees")

    # RENDER
    lines.append(f"<b>RENDER:</b> Burn-Mint Equilibrium — burns on GPU jobs")

    # BONK
    lines.append(f"<b>BONK:</b> 94% circulating, LetsBonk burns")

    # PUMP cliff
    days = pump_cliff.get("days_remaining", 0)
    lines.append(
        f"\n<b>PUMP CLIFF:</b> ⏰ {days}d remaining (Jul 12)\n"
        f"  {pump_cliff.get('pct_unlocking', 41)}% of supply unlocking"
    )

    # Penalties
    if penalties:
        lines.append("\n⚠️ <b>DISTRIBUTION PENALTIES:</b>")
        for symbol, p in penalties.items():
            lines.append(f"  {symbol}: -{p['penalty_pct']}% score ({p['days_remaining']}d to cliff)")

    return "\n".join(lines)


def format_supply_for_report(hype_flow: dict, pump_cliff: dict) -> str:
    """One-line per token for daily report."""
    lines = []
    net = hype_flow.get("net_flow_usd", 0)
    arrow = "🟢" if net > 0 else "🔴"
    lines.append(f"  HYPE: {arrow} net {_fmt_usd(net)}/d (buyback {_fmt_usd(hype_flow.get('daily_buyback_usd', 0))} vs emit {_fmt_usd(hype_flow.get('daily_emission_usd', 0))})")
    lines.append(f"  JUP: 🟢 zero emissions")
    lines.append(f"  PUMP: ⏰ cliff in {pump_cliff['days_remaining']}d (Jul 12)")
    lines.append(f"  RENDER: BME burns | BONK: 94% circ, burns")
    return "\n".join(lines)


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_supply_flow(send_to_telegram: bool = False) -> dict:
    """Collect supply flow data for all tracked tokens."""
    ensure_table()

    hype_flow = calc_hype_supply_flow()
    pump_cliff = calc_pump_cliff()
    penalties = get_distribution_penalties()

    # Store HYPE flow
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    execute("""
        INSERT INTO supply_flow (date, token, net_flow_usd, daily_emission_tokens,
            daily_buyback_usd, net_dilution_usd, cliff_days_remaining, notes)
        VALUES (%s, 'HYPE', %s, %s, %s, %s, NULL, %s)
    """, (today, hype_flow["net_flow_usd"], hype_flow["daily_emission_tokens"],
          hype_flow["daily_buyback_usd"], -hype_flow["net_flow_usd"],
          "net positive" if hype_flow["net_positive"] else "net negative"))

    # Store PUMP cliff
    execute("""
        INSERT INTO supply_flow (date, token, cliff_days_remaining, notes)
        VALUES (%s, 'PUMP', %s, %s)
    """, (today, pump_cliff["days_remaining"],
          f"Cliff Jul 12, {pump_cliff['pct_unlocking']}% unlocking"))

    msg = format_supply_telegram(hype_flow, pump_cliff, penalties)
    log.info("Supply flow:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)

        # Send cliff alert if threshold hit
        if pump_cliff.get("should_alert"):
            alert = (f"⏰ <b>PUMP CLIFF ALERT</b>\n"
                     f"{pump_cliff['days_remaining']} days until Jul 12 unlock\n"
                     f"{pump_cliff['pct_unlocking']}% of supply unlocking\n"
                     f"Spec says: mandatory reduce before cliff")
            send_telegram(alert)

    return {"hype": hype_flow, "pump_cliff": pump_cliff, "penalties": penalties}


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_supply_flow(send_to_telegram=send_tg)
    print(format_supply_telegram(result["hype"], result["pump_cliff"], result["penalties"]))
