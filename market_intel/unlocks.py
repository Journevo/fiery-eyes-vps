"""Tokenomist.ai API — cliff unlocks, linear emissions, buyback/burn data.

Unlock-to-volume ratio:
  <0.5x = green (healthy)
  0.5-3x = yellow (caution)
  >3x = red (danger)

Net dilution = unlocks - buybacks - burns.
7-day exit warning before cliff events.
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import TOKENOMIST_API_KEY, get_logger
from quality_gate.helpers import get_json

log = get_logger("market_intel.unlocks")

TOKENOMIST_BASE = "https://api.tokenomist.ai/v1"
FALLBACK_FILE = Path(__file__).parent / "unlock_data.json"


def _headers() -> dict:
    if TOKENOMIST_API_KEY:
        return {"Authorization": f"Bearer {TOKENOMIST_API_KEY}"}
    return {}


def _load_fallback(token_id: str) -> dict | None:
    """Load fallback unlock data from local JSON."""
    try:
        data = json.loads(FALLBACK_FILE.read_text())
        return data.get("tokens", {}).get(token_id)
    except Exception:
        return None


def get_upcoming_unlocks(token_id_or_symbol: str) -> list[dict]:
    """Get upcoming token unlock events.

    Returns:
        list of {date, amount_tokens, amount_usd, type, pct_of_supply}
    """
    if TOKENOMIST_API_KEY:
        try:
            data = get_json(
                f"{TOKENOMIST_BASE}/unlocks/{token_id_or_symbol}",
                headers=_headers(),
            )
            events = data.get("events", data.get("unlocks", []))
            return [
                {
                    "date": e.get("date"),
                    "amount_tokens": e.get("amount_tokens", 0),
                    "amount_usd": e.get("amount_usd", 0),
                    "type": e.get("type", "linear"),
                    "pct_of_supply": e.get("pct_of_supply", 0),
                }
                for e in events
            ]
        except Exception as e:
            log.debug("Tokenomist unlock fetch failed for %s: %s", token_id_or_symbol, e)

    # Fallback
    fallback = _load_fallback(token_id_or_symbol)
    if fallback:
        return fallback.get("unlocks", [])

    return []


def get_emission_schedule(token_id_or_symbol: str) -> dict:
    """Get token emission schedule.

    Returns:
        {"daily_emission": float|None, "monthly_emission": float|None,
         "total_locked": float|None, "unlock_complete_date": str|None}
    """
    if TOKENOMIST_API_KEY:
        try:
            data = get_json(
                f"{TOKENOMIST_BASE}/emissions/{token_id_or_symbol}",
                headers=_headers(),
            )
            return {
                "daily_emission": data.get("daily_emission"),
                "monthly_emission": data.get("monthly_emission"),
                "total_locked": data.get("total_locked"),
                "unlock_complete_date": data.get("unlock_complete_date"),
            }
        except Exception as e:
            log.debug("Tokenomist emission fetch failed for %s: %s", token_id_or_symbol, e)

    fallback = _load_fallback(token_id_or_symbol)
    if fallback:
        return fallback.get("emission_schedule", {})

    return {"daily_emission": None, "monthly_emission": None,
            "total_locked": None, "unlock_complete_date": None}


def get_buyback_burn_data(token_id_or_symbol: str) -> dict:
    """Get buyback and burn data.

    Returns:
        {"buyback_30d_usd": float, "burn_30d_tokens": float,
         "net_emission": float}
    """
    if TOKENOMIST_API_KEY:
        try:
            data = get_json(
                f"{TOKENOMIST_BASE}/buybacks/{token_id_or_symbol}",
                headers=_headers(),
            )
            buyback = float(data.get("buyback_30d_usd", 0) or 0)
            burn = float(data.get("burn_30d_tokens", 0) or 0)
            emission = get_emission_schedule(token_id_or_symbol)
            monthly = emission.get("monthly_emission") or 0
            net = monthly - buyback - burn
            return {
                "buyback_30d_usd": buyback,
                "burn_30d_tokens": burn,
                "net_emission": net,
            }
        except Exception as e:
            log.debug("Tokenomist buyback fetch failed for %s: %s", token_id_or_symbol, e)

    fallback = _load_fallback(token_id_or_symbol)
    if fallback and "buybacks" in fallback:
        return fallback["buybacks"]

    return {"buyback_30d_usd": 0, "burn_30d_tokens": 0, "net_emission": 0}


def calculate_unlock_risk(token_id_or_symbol: str, daily_volume: float) -> dict:
    """Calculate unlock risk based on upcoming unlocks vs daily volume.

    Returns:
        {"unlock_to_volume_ratio": float, "risk_level": str,
         "net_dilution": float, "days_to_next_cliff": int|None,
         "warning": str|None}
    """
    unlocks = get_upcoming_unlocks(token_id_or_symbol)
    buybacks = get_buyback_burn_data(token_id_or_symbol)

    # Calculate total upcoming unlock value (next 30 days)
    now = datetime.now(timezone.utc)
    upcoming_value = 0
    days_to_cliff = None

    for u in unlocks:
        try:
            unlock_date = u.get("date", "")
            if isinstance(unlock_date, str) and unlock_date:
                dt = datetime.fromisoformat(unlock_date.replace("Z", "+00:00"))
            else:
                continue

            days_until = (dt - now).days
            if 0 <= days_until <= 30:
                upcoming_value += float(u.get("amount_usd", 0) or 0)

            if u.get("type") == "cliff" and days_until >= 0:
                if days_to_cliff is None or days_until < days_to_cliff:
                    days_to_cliff = days_until
        except Exception:
            continue

    # Calculate ratio
    if daily_volume > 0:
        ratio = upcoming_value / (daily_volume * 30) if daily_volume > 0 else 0
    else:
        ratio = 0 if upcoming_value == 0 else 999

    # Risk level
    if ratio < 0.5:
        risk_level = "green"
    elif ratio < 3.0:
        risk_level = "yellow"
    else:
        risk_level = "red"

    # Net dilution
    net_dilution = upcoming_value - buybacks.get("buyback_30d_usd", 0) - buybacks.get("burn_30d_tokens", 0)

    # Warning for cliff within 7 days
    warning = None
    if days_to_cliff is not None and days_to_cliff <= 7:
        warning = f"CLIFF UNLOCK in {days_to_cliff} days"

    return {
        "unlock_to_volume_ratio": round(ratio, 2),
        "risk_level": risk_level,
        "net_dilution": net_dilution,
        "days_to_next_cliff": days_to_cliff,
        "warning": warning,
    }


def get_7day_cliff_warnings() -> list[dict]:
    """Get all tokens with cliff unlocks in the next 7 days.

    Returns:
        list of {symbol, days_until, pct_of_supply, amount_usd, risk_level}
    """
    warnings = []

    # Check tracked tokens from DB
    try:
        from db.connection import execute
        rows = execute(
            "SELECT symbol, contract_address FROM tokens WHERE quality_gate_pass = TRUE",
            fetch=True,
        )
        if not rows:
            return warnings

        now = datetime.now(timezone.utc)
        for symbol, mint in rows:
            unlocks = get_upcoming_unlocks(symbol)
            for u in unlocks:
                try:
                    date_str = u.get("date", "")
                    if not date_str:
                        continue
                    dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
                    days_until = (dt - now).days
                    if 0 <= days_until <= 7 and u.get("type") == "cliff":
                        risk = calculate_unlock_risk(symbol, 0)
                        warnings.append({
                            "symbol": symbol,
                            "days_until": days_until,
                            "pct_of_supply": u.get("pct_of_supply", 0),
                            "amount_usd": u.get("amount_usd", 0),
                            "risk_level": risk.get("risk_level", "unknown"),
                        })
                except Exception:
                    continue
    except Exception as e:
        log.error("7-day cliff warning scan failed: %s", e)

    warnings.sort(key=lambda w: w.get("days_until", 999))
    return warnings
