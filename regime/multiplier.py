"""Regime Multiplier — macro environment scoring for position sizing.

Four components averaged into a single multiplier (0.4–1.2):
  1. BTC Trend:            20d EMA vs 50d EMA, BTC vs 200d SMA
  2. Stablecoin Supply:    30d change in USDT+USDC+DAI market cap
  3. Liquidity Proxy:      BTC dominance + total crypto mcap trend
  4. Risk Appetite:        Crypto Fear & Greed Index

Regime-adjusted allocation rules:
  >0.8  + Score >85  → full allocation
  0.6–0.8 + Score >85 → half allocation
  <0.6               → only Tier 1-2
  <0.5               → cash mode, active risk reduction
"""

import math
from datetime import date, datetime, timezone
from config import COINGECKO_API_KEY, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json

log = get_logger("regime.multiplier")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/"


# ---------------------------------------------------------------------------
# Data fetchers
# ---------------------------------------------------------------------------

def _cg_headers() -> dict:
    h = {}
    if COINGECKO_API_KEY:
        h["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return h


def _fetch_btc_prices(days: int = 200) -> list[float]:
    """Fetch BTC daily close prices from CoinGecko (free, up to 365d)."""
    try:
        data = get_json(
            f"{COINGECKO_BASE}/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": str(days), "interval": "daily"},
            headers=_cg_headers(),
        )
        prices = [p[1] for p in data.get("prices", [])]
        return prices
    except Exception as e:
        log.error("Failed to fetch BTC prices: %s", e)
        return []


def _fetch_stablecoin_mcaps() -> dict:
    """Fetch current market caps for USDT, USDC, DAI from CoinGecko."""
    ids = "tether,usd-coin,dai"
    try:
        data = get_json(
            f"{COINGECKO_BASE}/coins/markets",
            params={"vs_currency": "usd", "ids": ids, "order": "market_cap_desc"},
            headers=_cg_headers(),
        )
        result = {}
        for coin in data:
            result[coin["id"]] = {
                "mcap": coin.get("market_cap") or 0,
                "mcap_change_30d": coin.get("market_cap_change_percentage_24h") or 0,
            }
        return result
    except Exception as e:
        log.error("Failed to fetch stablecoin mcaps: %s", e)
        return {}


def _fetch_global_crypto() -> dict:
    """Fetch global crypto market data (total mcap, BTC dominance)."""
    try:
        data = get_json(f"{COINGECKO_BASE}/global", headers=_cg_headers())
        gd = data.get("data", {})
        return {
            "total_mcap": gd.get("total_market_cap", {}).get("usd", 0),
            "btc_dominance": gd.get("market_cap_percentage", {}).get("btc", 0),
            "mcap_change_24h": gd.get("market_cap_change_percentage_24h_usd", 0),
        }
    except Exception as e:
        log.error("Failed to fetch global crypto data: %s", e)
        return {}


def _fetch_fear_greed() -> dict:
    """Fetch Crypto Fear & Greed Index (free, no key)."""
    try:
        data = get_json(f"{FEAR_GREED_URL}?limit=7")
        entries = data.get("data", [])
        if not entries:
            return {"value": 50, "classification": "Neutral", "trend": 0}

        current = int(entries[0].get("value", 50))
        classification = entries[0].get("value_classification", "Neutral")

        # Calculate 7-day trend
        trend = 0
        if len(entries) >= 7:
            recent_avg = sum(int(e["value"]) for e in entries[:3]) / 3
            older_avg = sum(int(e["value"]) for e in entries[4:7]) / 3
            trend = recent_avg - older_avg  # positive = rising sentiment

        return {"value": current, "classification": classification, "trend": trend}
    except Exception as e:
        log.error("Failed to fetch Fear & Greed: %s", e)
        return {"value": 50, "classification": "Neutral", "trend": 0}


# ---------------------------------------------------------------------------
# EMA / SMA helpers
# ---------------------------------------------------------------------------

def _ema(prices: list[float], period: int) -> float:
    """Calculate Exponential Moving Average for the last value."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    k = 2 / (period + 1)
    ema_val = sum(prices[:period]) / period  # seed with SMA
    for price in prices[period:]:
        ema_val = price * k + ema_val * (1 - k)
    return ema_val


def _sma(prices: list[float], period: int) -> float:
    """Calculate Simple Moving Average of last N values."""
    if len(prices) < period:
        return sum(prices) / len(prices) if prices else 0
    return sum(prices[-period:]) / period


# ---------------------------------------------------------------------------
# Component scorers (each returns 0.4–1.2)
# ---------------------------------------------------------------------------

def _score_btc_trend(prices: list[float]) -> float:
    """BTC trend score based on EMA crossover and 200d SMA position.
    Above 200d + 20>50 = 1.0, Below 200d + 20<50 = 0.4, Mixed = 0.6–0.8"""
    if len(prices) < 50:
        log.warning("Insufficient BTC price data (%d points), returning neutral", len(prices))
        return 0.7

    current = prices[-1]
    ema_20 = _ema(prices, 20)
    ema_50 = _ema(prices, 50)

    above_200 = True
    if len(prices) >= 200:
        sma_200 = _sma(prices, 200)
        above_200 = current > sma_200
    # If <200 days data, assume above 200d SMA

    ema_bullish = ema_20 > ema_50

    if above_200 and ema_bullish:
        return 1.0   # full bull
    if above_200 and not ema_bullish:
        return 0.8   # above support but weakening
    if not above_200 and ema_bullish:
        return 0.6   # below support but recovering
    return 0.4       # below support + bearish cross


def _score_stablecoin_supply(stables: dict) -> float:
    """Stablecoin supply direction: growing >2% = 1.0, flat = 0.7, shrinking = 0.4"""
    if not stables:
        return 0.7

    # Aggregate mcap changes (using 24h change as proxy, scaled to 30d)
    total_change = 0
    count = 0
    for coin_data in stables.values():
        change = coin_data.get("mcap_change_30d", 0) or 0
        total_change += change
        count += 1

    avg_change = (total_change / count) if count else 0

    # Scale 24h change to approximate 30d direction
    # Positive 24h change sustained = growing supply
    if avg_change > 2:
        return 1.0
    if avg_change > 0.5:
        return 0.85
    if avg_change > -0.5:
        return 0.7
    if avg_change > -2:
        return 0.55
    return 0.4


def _score_liquidity_proxy(global_data: dict) -> float:
    """Liquidity proxy using BTC dominance + total mcap trend.
    Expanding = 1.0, Flat = 0.7, Contracting = 0.5"""
    if not global_data:
        return 0.7

    mcap_change = global_data.get("mcap_change_24h", 0) or 0
    btc_dom = global_data.get("btc_dominance", 50) or 50

    # BTC dominance dropping + mcap rising = liquidity expanding to alts (bullish)
    # BTC dominance rising + mcap falling = flight to safety (bearish)
    if mcap_change > 2 and btc_dom < 55:
        return 1.0   # expanding, alts getting flow
    if mcap_change > 0:
        return 0.85  # modest expansion
    if mcap_change > -2:
        return 0.7   # flat
    if mcap_change > -5:
        return 0.55  # contracting
    return 0.5       # sharp contraction


def _score_oi_leverage() -> float | None:
    """Optional OI-based leverage assessment for regime.
    High OI + high funding = overleveraged = risk of cascade.
    Returns 0.4-1.0 or None if data unavailable."""
    try:
        from market_intel.oi_analyzer import analyze_oi_regime
        from market_intel.coinglass import get_funding_rates
        analysis = analyze_oi_regime("BTC", 0, 0)
        funding = get_funding_rates("BTC")
        leverage_risk = analysis.get("leverage_risk", 50)
        funding_rate = funding.get("current_rate", 0)

        # High leverage risk = lower regime score
        if leverage_risk >= 80 or abs(funding_rate) > 0.1:
            return 0.4   # extreme leverage
        if leverage_risk >= 60 or abs(funding_rate) > 0.05:
            return 0.6   # elevated leverage
        if leverage_risk >= 40:
            return 0.75  # moderate
        return 0.9       # healthy leverage levels
    except Exception:
        return None  # skip OI component if unavailable


def _score_risk_appetite(fng: dict) -> float:
    """Risk appetite from Fear & Greed Index.
    Greed + rising = 1.0, Neutral = 0.7, Fear = 0.4"""
    value = fng.get("value", 50)
    trend = fng.get("trend", 0)

    if value >= 70 and trend > 0:
        return 1.0   # extreme greed + rising
    if value >= 60:
        return 0.9   # greed
    if value >= 45:
        return 0.7   # neutral
    if value >= 30:
        return 0.55  # fear
    if value >= 20:
        return 0.4   # extreme fear
    return 0.4


# ---------------------------------------------------------------------------
# Main scoring
# ---------------------------------------------------------------------------

def calculate_regime() -> dict:
    """Calculate current regime multiplier and all components.

    Returns:
        {
            "regime_multiplier": float (0.4–1.2),
            "components": {
                "btc_trend": float,
                "stablecoin_supply": float,
                "liquidity_proxy": float,
                "risk_appetite": float,
            },
            "raw_data": {
                "btc_price": float,
                "btc_ema20": float,
                "btc_ema50": float,
                "btc_sma200": float | None,
                "fear_greed_value": int,
                "fear_greed_classification": str,
                "total_crypto_mcap": float,
                "btc_dominance": float,
            },
            "allocation_guidance": str,
        }
    """
    log.info("Calculating regime multiplier...")

    # Fetch all data
    btc_prices = _fetch_btc_prices(200)
    stables = _fetch_stablecoin_mcaps()
    global_data = _fetch_global_crypto()
    fng = _fetch_fear_greed()

    # Score components
    components = {
        "btc_trend": _score_btc_trend(btc_prices),
        "stablecoin_supply": _score_stablecoin_supply(stables),
        "liquidity_proxy": _score_liquidity_proxy(global_data),
        "risk_appetite": _score_risk_appetite(fng),
    }

    # Optional: OI-based leverage assessment
    oi_score = _score_oi_leverage()
    if oi_score is not None:
        components["oi_leverage"] = oi_score

    # Regime multiplier = average of components (range 0.4–1.2)
    # Cap at 1.2 to allow slight boost in extreme bull conditions
    raw_avg = sum(components.values()) / len(components)
    regime_multiplier = round(min(1.2, max(0.4, raw_avg)), 3)

    # Build raw data summary
    raw_data = {
        "btc_price": btc_prices[-1] if btc_prices else 0,
        "btc_ema20": round(_ema(btc_prices, 20), 2) if len(btc_prices) >= 20 else None,
        "btc_ema50": round(_ema(btc_prices, 50), 2) if len(btc_prices) >= 50 else None,
        "btc_sma200": round(_sma(btc_prices, 200), 2) if len(btc_prices) >= 200 else None,
        "fear_greed_value": fng.get("value", 50),
        "fear_greed_classification": fng.get("classification", "Neutral"),
        "total_crypto_mcap": global_data.get("total_mcap", 0),
        "btc_dominance": global_data.get("btc_dominance", 0),
    }

    # Allocation guidance
    if regime_multiplier >= 0.8:
        guidance = "full_allocation"
    elif regime_multiplier >= 0.6:
        guidance = "half_allocation"
    elif regime_multiplier >= 0.5:
        guidance = "tier_1_2_only"
    else:
        guidance = "cash_mode"

    result = {
        "regime_multiplier": regime_multiplier,
        "components": {k: round(v, 3) for k, v in components.items()},
        "raw_data": raw_data,
        "allocation_guidance": guidance,
    }

    log.info("Regime multiplier: %.3f (%s) — BTC=%.3f, Stable=%.3f, Liq=%.3f, FnG=%.3f",
             regime_multiplier, guidance,
             components["btc_trend"], components["stablecoin_supply"],
             components["liquidity_proxy"], components["risk_appetite"])

    # Persist to regime_snapshots
    _save_regime(result)

    return result


def _save_regime(result: dict):
    """Store regime snapshot in DB."""
    today = date.today()
    comp = result["components"]
    try:
        execute(
            """INSERT INTO regime_snapshots
               (date, btc_trend_score, stablecoin_supply_delta,
                liquidity_proxy, risk_appetite, regime_multiplier)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (date) DO UPDATE SET
                 btc_trend_score = EXCLUDED.btc_trend_score,
                 stablecoin_supply_delta = EXCLUDED.stablecoin_supply_delta,
                 liquidity_proxy = EXCLUDED.liquidity_proxy,
                 risk_appetite = EXCLUDED.risk_appetite,
                 regime_multiplier = EXCLUDED.regime_multiplier""",
            (today, comp["btc_trend"], comp["stablecoin_supply"],
             comp["liquidity_proxy"], comp["risk_appetite"],
             result["regime_multiplier"]),
        )
        log.info("Regime snapshot saved for %s", today)
    except Exception as e:
        log.error("Failed to save regime snapshot: %s", e)


def get_current_regime() -> dict | None:
    """Get today's regime from DB, or calculate if not present."""
    try:
        row = execute_one(
            """SELECT regime_multiplier, btc_trend_score, stablecoin_supply_delta,
                      liquidity_proxy, risk_appetite
               FROM regime_snapshots WHERE date = CURRENT_DATE""",
        )
        if row:
            mult = row[0]
            if mult >= 0.8:
                guidance = "full_allocation"
            elif mult >= 0.6:
                guidance = "half_allocation"
            elif mult >= 0.5:
                guidance = "tier_1_2_only"
            else:
                guidance = "cash_mode"
            return {
                "regime_multiplier": mult,
                "components": {
                    "btc_trend": row[1],
                    "stablecoin_supply": row[2],
                    "liquidity_proxy": row[3],
                    "risk_appetite": row[4],
                },
                "allocation_guidance": guidance,
            }
    except Exception as e:
        log.error("Failed to fetch regime from DB: %s", e)

    # Calculate fresh
    return calculate_regime()


def get_regime_state() -> dict:
    """Get current regime as discrete state for meme trading.

    States:
        RISK-ON:   BTC >+3% 24h, F&G >60     -> normal rules
        NEUTRAL:   BTC +/-3%                   -> normal rules
        UNCERTAIN: BTC -3% to -8%              -> no new entries, widen stops
        RISK-OFF:  BTC <-8% 24h, F&G <25      -> PAUSE auto-exits, only exit on KOL sell

    Returns:
        {
            'state': str,
            'confidence': float (0-100),
            'btc_24h_pct': float,
            'fear_greed': int,
            'details': str,
        }
    """
    # Fetch BTC 24h change
    btc_24h_pct = 0
    fear_greed = 50
    confidence = 100

    try:
        btc_prices = _fetch_btc_prices(2)
        if len(btc_prices) >= 2:
            btc_24h_pct = ((btc_prices[-1] - btc_prices[-2]) / btc_prices[-2]) * 100
        else:
            confidence -= 30  # Reduced confidence without BTC data
    except Exception as e:
        log.error("Failed to fetch BTC data for regime state: %s", e)
        confidence -= 30

    try:
        fng = _fetch_fear_greed()
        fear_greed = fng.get("value", 50)
    except Exception as e:
        log.error("Failed to fetch F&G for regime state: %s", e)
        confidence -= 20

    # Determine state
    if btc_24h_pct > 3 and fear_greed > 60:
        state = 'risk_on'
        details = f"BTC {btc_24h_pct:+.1f}%, F&G {fear_greed}"
    elif btc_24h_pct < -8 or fear_greed < 25:
        state = 'risk_off'
        details = f"BTC {btc_24h_pct:+.1f}%, F&G {fear_greed} — PAUSE auto-exits"
    elif btc_24h_pct < -3:
        state = 'uncertain'
        details = f"BTC {btc_24h_pct:+.1f}% — no new entries, widen stops"
    else:
        state = 'neutral'
        details = f"BTC {btc_24h_pct:+.1f}%, F&G {fear_greed}"

    # Reduced confidence widens thresholds
    confidence = max(0, min(100, confidence))

    return {
        'state': state,
        'confidence': confidence,
        'btc_24h_pct': round(btc_24h_pct, 2),
        'fear_greed': fear_greed,
        'details': details,
    }


def get_allocation_guidance(regime_multiplier: float, composite_score: float) -> dict:
    """Determine allocation guidance based on regime + token score.

    Returns:
        {
            "action": str,
            "max_tier": int,
            "size_modifier": float,  # multiplier for position size
            "reason": str,
        }
    """
    if regime_multiplier >= 0.8 and composite_score >= 85:
        return {
            "action": "full_allocation",
            "max_tier": 5,
            "size_modifier": 1.0,
            "reason": "Strong regime + high score",
        }
    if regime_multiplier >= 0.6 and composite_score >= 85:
        return {
            "action": "half_allocation",
            "max_tier": 4,
            "size_modifier": 0.5,
            "reason": "Mixed regime, high score — half size",
        }
    if regime_multiplier >= 0.5:
        return {
            "action": "tier_1_2_only",
            "max_tier": 2,
            "size_modifier": 0.3,
            "reason": "Weak regime — foundations only",
        }
    return {
        "action": "cash_mode",
        "max_tier": 1,
        "size_modifier": 0.0,
        "reason": "Risk-off — cash mode, active risk reduction",
    }
