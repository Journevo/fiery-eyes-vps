"""Enhanced Macro Regime — BTC dominance, SOL/BTC ratio, stablecoin totals, funding.

Collects every 4 hours and classifies regime as RISK_ON / NEUTRAL / RISK_OFF.
Stores snapshots in macro_regime_v2 table.
"""

from config import COINGECKO_API_KEY, COINGLASS_API_KEY, get_logger
from db.connection import execute, execute_one
from quality_gate.helpers import get_json
from monitoring.degraded import record_api_call

log = get_logger("chain_metrics.macro")

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


def _cg_headers() -> dict:
    h = {}
    if COINGECKO_API_KEY:
        h["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return h


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def _fetch_global_and_prices() -> tuple[float, float, float, float]:
    """Fetch BTC price, SOL price, BTC dominance from CoinGecko in one call.

    Uses /global for dominance, then reads recent holdings_health for prices
    to avoid extra API calls. Falls back to /simple/price if no holdings data.

    Returns (btc_price, sol_price, btc_dominance_pct, total_mcap).
    """
    btc_dom = 0
    total_mcap = 0
    btc_price = 0
    sol_price = 0

    # 1. Get dominance from /global (one CoinGecko call)
    try:
        data = get_json(f"{COINGECKO_BASE}/global", headers=_cg_headers())
        record_api_call("coingecko_global", True)
        gd = data.get("data", {})
        btc_dom = gd.get("market_cap_percentage", {}).get("btc", 0)
        total_mcap = gd.get("total_market_cap", {}).get("usd", 0)
    except Exception as e:
        log.error("CoinGecko /global fetch failed: %s", e)
        record_api_call("coingecko_global", False)

    # 2. Try to get prices from holdings_health (already collected this cycle)
    try:
        sol_row = execute_one(
            """SELECT price_usd FROM holdings_health
               WHERE token = 'SOL' AND timestamp > NOW() - INTERVAL '1 hour'
               ORDER BY timestamp DESC LIMIT 1""",
        )
        if sol_row and sol_row[0]:
            sol_price = float(sol_row[0])
    except Exception:
        pass

    # BTC price: derive from total_mcap and dominance, or fall back to /simple/price
    if total_mcap and btc_dom:
        btc_price = total_mcap * btc_dom / 100 / 19.8e6  # approx BTC supply ~19.8M
    if not btc_price or not sol_price:
        try:
            price_data = get_json(
                f"{COINGECKO_BASE}/simple/price",
                params={"ids": "bitcoin,solana", "vs_currency": "usd"},
                headers=_cg_headers(),
            )
            if not btc_price:
                btc_price = price_data.get("bitcoin", {}).get("usd", 0)
            if not sol_price:
                sol_price = price_data.get("solana", {}).get("usd", 0)
        except Exception as e:
            log.warning("CoinGecko /simple/price fallback failed: %s", e)

    return btc_price, sol_price, btc_dom, total_mcap


def _fetch_stablecoin_total() -> float:
    """DeFiLlama total stablecoin supply across all chains."""
    try:
        data = get_json("https://stablecoins.llama.fi/stablecoins?includePrices=false")
        record_api_call("defillama_stablecoins_total", True)
        total = 0
        for stable in data.get("peggedAssets", []):
            circulating = stable.get("circulating", {})
            if isinstance(circulating, dict):
                total += float(circulating.get("peggedUSD", 0) or 0)
        return total
    except Exception as e:
        log.error("DeFiLlama stablecoin total fetch failed: %s", e)
        record_api_call("defillama_stablecoins_total", False)
        return 0


def _fetch_funding_avg() -> float | None:
    """Average SOL funding rate from CoinGlass (if API key set)."""
    if not COINGLASS_API_KEY:
        return None
    try:
        from market_intel.coinglass import get_funding_rates
        funding = get_funding_rates("SOL")
        return funding.get("current_rate")
    except Exception as e:
        log.error("CoinGlass SOL funding fetch failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

def _classify_regime(btc_dominance: float, sol_btc_ratio: float,
                     funding: float | None, stablecoin_total: float) -> str:
    """Classify macro regime based on multiple signals.

    RISK_ON:  BTC dom falling + SOL/BTC rising + stablecoin inflow
    RISK_OFF: BTC dom rising sharply + SOL/BTC falling + extreme funding
    NEUTRAL:  mixed signals
    """
    # Get previous snapshot for trend comparison
    prev = execute_one(
        """SELECT btc_dominance, sol_btc_ratio, stablecoin_total
           FROM macro_regime_v2
           ORDER BY timestamp DESC LIMIT 1""",
    )

    signals = 0  # positive = risk-on, negative = risk-off

    if prev:
        prev_dom, prev_ratio, prev_stable = prev
        # BTC dominance trend (falling = alt-season = risk-on for SOL)
        if prev_dom and btc_dominance:
            dom_change = btc_dominance - float(prev_dom)
            if dom_change < -0.5:
                signals += 1
            elif dom_change > 0.5:
                signals -= 1

        # SOL/BTC ratio trend (rising = SOL outperforming)
        if prev_ratio and sol_btc_ratio:
            ratio_change = (sol_btc_ratio - float(prev_ratio)) / float(prev_ratio) * 100
            if ratio_change > 2:
                signals += 1
            elif ratio_change < -2:
                signals -= 1

        # Stablecoin supply trend (growing = inflow = bullish)
        if prev_stable and stablecoin_total:
            stable_change = (stablecoin_total - float(prev_stable)) / float(prev_stable) * 100
            if stable_change > 0.5:
                signals += 1
            elif stable_change < -0.5:
                signals -= 1

    # Funding rate signal
    if funding is not None:
        if abs(funding) > 0.05:
            signals -= 1  # extreme funding = overleveraged
        elif abs(funding) < 0.01:
            signals += 1  # neutral funding = healthy

    if signals >= 2:
        return "RISK_ON"
    if signals <= -2:
        return "RISK_OFF"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_macro_snapshot():
    """Every-4h entry point: fetch and store enhanced macro data."""
    log.info("Collecting macro snapshot...")

    btc_price, sol_price, btc_dominance, total_mcap = _fetch_global_and_prices()
    sol_btc_ratio = sol_price / btc_price if btc_price else 0
    stablecoin_total = _fetch_stablecoin_total()
    funding_avg = _fetch_funding_avg()

    regime_signal = _classify_regime(btc_dominance, sol_btc_ratio,
                                     funding_avg, stablecoin_total)

    execute(
        """INSERT INTO macro_regime_v2
           (btc_price, btc_dominance, sol_btc_ratio, funding_avg,
            stablecoin_total, regime_signal)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (btc_price, btc_dominance, sol_btc_ratio, funding_avg,
         stablecoin_total, regime_signal),
    )

    log.info("Macro snapshot: BTC $%.0f | Dom %.1f%% | SOL/BTC %.6f | "
             "Stables $%.0fB | Regime %s",
             btc_price, btc_dominance, sol_btc_ratio,
             stablecoin_total / 1e9 if stablecoin_total else 0, regime_signal)

    result = {
        "btc_price": btc_price,
        "btc_dominance": btc_dominance,
        "sol_btc_ratio": sol_btc_ratio,
        "funding_avg": funding_avg,
        "stablecoin_total": stablecoin_total,
        "regime_signal": regime_signal,
    }

    # Check for macro alert conditions
    try:
        _check_macro_alerts(result)
    except Exception as e:
        log.error("Macro alert check failed: %s", e)

    return result


def get_macro_summary() -> dict:
    """Query latest macro snapshot + previous for trends.

    Returns:
        {
            "btc_price": float,
            "btc_dominance": float,
            "sol_btc_ratio": float,
            "funding_avg": float|None,
            "stablecoin_total": float,
            "regime_signal": str,
            "sol_btc_trend": str,  # up/down/flat
            "dom_trend": str,      # rising/falling/flat
        }
    """
    row = execute_one(
        """SELECT btc_price, btc_dominance, sol_btc_ratio, funding_avg,
                  stablecoin_total, regime_signal
           FROM macro_regime_v2
           ORDER BY timestamp DESC LIMIT 1""",
    )
    if not row:
        return {}

    btc_price, btc_dom, sol_btc, funding, stable_total, regime = row

    # Previous snapshot for trend
    prev = execute_one(
        """SELECT btc_dominance, sol_btc_ratio
           FROM macro_regime_v2
           ORDER BY timestamp DESC LIMIT 1 OFFSET 1""",
    )

    sol_btc_trend = "flat"
    dom_trend = "flat"
    if prev:
        prev_dom, prev_ratio = prev
        if prev_ratio and sol_btc:
            ratio_pct = (float(sol_btc) - float(prev_ratio)) / float(prev_ratio) * 100
            if ratio_pct > 1:
                sol_btc_trend = "up"
            elif ratio_pct < -1:
                sol_btc_trend = "down"
        if prev_dom and btc_dom:
            dom_diff = float(btc_dom) - float(prev_dom)
            if dom_diff > 0.3:
                dom_trend = "rising"
            elif dom_diff < -0.3:
                dom_trend = "falling"

    return {
        "btc_price": float(btc_price or 0),
        "btc_dominance": float(btc_dom or 0),
        "sol_btc_ratio": float(sol_btc or 0),
        "funding_avg": float(funding) if funding is not None else None,
        "stablecoin_total": float(stable_total or 0),
        "regime_signal": regime or "NEUTRAL",
        "sol_btc_trend": sol_btc_trend,
        "dom_trend": dom_trend,
    }


# ---------------------------------------------------------------------------
# Macro shift alerts — max 2/day to H-Fire, 24h dedup
# ---------------------------------------------------------------------------

_alert_table_ensured = False


def _ensure_alert_table():
    """Create macro_alert_log table if it doesn't exist."""
    global _alert_table_ensured
    if _alert_table_ensured:
        return
    execute(
        """CREATE TABLE IF NOT EXISTS macro_alert_log (
            id          SERIAL PRIMARY KEY,
            alert_type  VARCHAR(50) NOT NULL,
            message     TEXT,
            timestamp   TIMESTAMP DEFAULT NOW()
        )""",
    )
    _alert_table_ensured = True


def _alerts_sent_today() -> int:
    """Count macro alerts sent in the last 24 hours."""
    row = execute_one(
        "SELECT COUNT(*) FROM macro_alert_log WHERE timestamp > NOW() - INTERVAL '24 hours'",
    )
    return row[0] if row else 0


def _alert_already_sent(alert_type: str) -> bool:
    """Check if this alert type was already sent in the last 24h."""
    row = execute_one(
        "SELECT id FROM macro_alert_log WHERE alert_type = %s AND timestamp > NOW() - INTERVAL '24 hours'",
        (alert_type,),
    )
    return bool(row)


def _send_macro_alert(alert_type: str, tier: int, message: str):
    """Send a macro alert to H-Fire and log it."""
    from telegram_bot.severity import route_alert
    route_alert(tier, message)
    execute(
        "INSERT INTO macro_alert_log (alert_type, message) VALUES (%s, %s)",
        (alert_type, message),
    )
    log.info("Macro alert sent: %s", alert_type)


def _get_sol_btc_7d_change() -> float | None:
    """Calculate SOL/BTC ratio % change over ~7 days from macro_regime_v2."""
    current = execute_one(
        "SELECT sol_btc_ratio FROM macro_regime_v2 ORDER BY timestamp DESC LIMIT 1",
    )
    prev = execute_one(
        """SELECT sol_btc_ratio FROM macro_regime_v2
           WHERE timestamp < NOW() - INTERVAL '6 days'
           ORDER BY timestamp DESC LIMIT 1""",
    )
    if current and prev and current[0] and prev[0]:
        return (float(current[0]) - float(prev[0])) / float(prev[0]) * 100
    return None


def _get_previous_regime() -> str | None:
    """Get the regime signal from the snapshot before the current one."""
    row = execute_one(
        """SELECT regime_signal FROM macro_regime_v2
           ORDER BY timestamp DESC LIMIT 1 OFFSET 1""",
    )
    return row[0] if row else None


def _check_sol_dex_share_loss() -> str | None:
    """Check if Solana DEX volume dropped below ETH for 3 consecutive days."""
    try:
        rows = execute(
            """SELECT date,
                      SUM(CASE WHEN chain = 'Solana' THEN value ELSE 0 END) as sol_vol,
                      SUM(CASE WHEN chain = 'Ethereum' THEN value ELSE 0 END) as eth_vol
               FROM chain_metrics
               WHERE metric_name = 'dex_volume'
                 AND chain IN ('Solana', 'Ethereum')
                 AND date >= CURRENT_DATE - INTERVAL '3 days'
               GROUP BY date
               ORDER BY date DESC
               LIMIT 3""",
            fetch=True,
        )
        if not rows or len(rows) < 3:
            return None
        all_below = all(r[1] < r[2] for r in rows if r[1] and r[2])
        if all_below:
            sol_vol = rows[0][1]
            eth_vol = rows[0][2]
            return (
                f"⚠️ <b>SOLANA DEX SHARE LOSS</b>\n"
                f"SOL DEX vol below ETH for 3 consecutive days\n"
                f"SOL: ${sol_vol / 1e9:.1f}B vs ETH: ${eth_vol / 1e9:.1f}B"
            )
    except Exception as e:
        log.debug("DEX share check failed: %s", e)
    return None


def _check_macro_alerts(snapshot: dict):
    """Check macro conditions and fire alerts to H-Fire. Max 2/day, 24h dedup."""
    _ensure_alert_table()

    sent_today = _alerts_sent_today()
    if sent_today >= 2:
        return

    remaining = 2 - sent_today
    alerts = []

    # 1. SOL/BTC ratio weakening (7d)
    sol_btc_7d = _get_sol_btc_7d_change()
    if sol_btc_7d is not None:
        ratio = snapshot.get("sol_btc_ratio", 0)
        if sol_btc_7d < -10:
            alerts.append((
                "sol_btc_critical", 1,
                f"🔴 <b>SOL/BTC CRITICAL</b>\n"
                f"SOL/BTC: {ratio:.6f} ({sol_btc_7d:+.1f}% in 7d)\n"
                f"SOL significantly underperforming BTC",
            ))
        elif sol_btc_7d < -5:
            alerts.append((
                "sol_btc_weak", 2,
                f"🟡 <b>SOL/BTC WEAKENING</b>\n"
                f"SOL/BTC: {ratio:.6f} ({sol_btc_7d:+.1f}% in 7d)\n"
                f"Monitor for further deterioration",
            ))

    # 2. BTC dominance above 60%
    btc_dom = snapshot.get("btc_dominance", 0)
    if btc_dom > 60:
        alerts.append((
            "btc_dom_high", 2,
            f"⚠️ <b>BTC DOMINANCE HIGH</b>\n"
            f"BTC dominance: {btc_dom:.1f}%\n"
            f"Alt rotation unlikely — capital concentrating in BTC",
        ))

    # 3. Regime change
    prev_regime = _get_previous_regime()
    current_regime = snapshot.get("regime_signal", "NEUTRAL")
    if prev_regime and prev_regime != current_regime:
        emoji = {"RISK_ON": "🟢", "NEUTRAL": "🟡", "RISK_OFF": "🔴"}.get(current_regime, "⚪")
        alerts.append((
            "regime_change", 2,
            f"{emoji} <b>REGIME SHIFT</b>\n"
            f"{prev_regime} → {current_regime}\n"
            f"BTC ${snapshot.get('btc_price', 0):,.0f} | "
            f"Dom {btc_dom:.1f}% | SOL/BTC {snapshot.get('sol_btc_ratio', 0):.6f}",
        ))

    # 4. Solana DEX share loss (3 consecutive days below ETH)
    dex_msg = _check_sol_dex_share_loss()
    if dex_msg:
        alerts.append(("sol_dex_share_loss", 2, dex_msg))

    # Send alerts, respecting daily cap and dedup
    for alert_type, tier, msg in alerts:
        if remaining <= 0:
            break
        if _alert_already_sent(alert_type):
            continue
        _send_macro_alert(alert_type, tier, msg)
        remaining -= 1
