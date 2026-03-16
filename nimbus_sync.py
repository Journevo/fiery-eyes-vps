"""Nimbus Sync — Pull macro data from Jingubang VPS

SSHs into 134.209.176.180 daily (05:45 UTC, after autopull at 05:30).
Reads nimbus_data.py, parses the DATA dict, stores in Fiery Eyes DB.
Also computes FRED regime using SAME thresholds as Jingubang/TradingView Wukong.

This is the SINGLE SOURCE OF TRUTH for macro data in Fiery Eyes.
Nimbus is updated once on Jingubang → Fiery Eyes auto-pulls it.
"""

import ast
import json
import subprocess
from datetime import datetime, date, timezone
from config import FRED_API_KEY, get_logger
from db.connection import execute, execute_one

log = get_logger("nimbus_sync")

JINGUBANG_HOST = "root@134.209.176.180"
NIMBUS_DATA_PATH = "/home/jingubang/jingubang-bot/nimbus_data.py"
SSH_TIMEOUT = 15

# FRED regime thresholds — MUST match Jingubang app.py compute_fred_regime()
# and TradingView Wukong indicators exactly
REGIME_SLOPE_EXPANDING = 0.05   # slope_pct > 0.05 → EXPANDING
REGIME_SLOPE_CONTRACTING = -0.05  # slope_pct < -0.05 → CONTRACTING
# Between -0.05 and 0.05 → STALL


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
def ensure_tables():
    """Create nimbus sync tables if they don't exist."""
    execute("""
        CREATE TABLE IF NOT EXISTS nimbus_sync (
            id SERIAL PRIMARY KEY,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            as_of_date TEXT NOT NULL,
            data_json JSONB NOT NULL,
            fred_regime TEXT,
            fred_slope REAL,
            global_regime TEXT,
            global_slope REAL,
            m2_regime TEXT,
            m2_slope REAL,
            sync_status TEXT DEFAULT 'ok'
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS nimbus_macro (
            key TEXT PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def store_nimbus(data: dict, regimes: dict):
    """Store full Nimbus snapshot and regime data."""
    now = datetime.now(timezone.utc)
    as_of = data.get("meta", {}).get("as_of_date", "unknown")

    execute("""
        INSERT INTO nimbus_sync (synced_at, as_of_date, data_json,
            fred_regime, fred_slope, global_regime, global_slope,
            m2_regime, m2_slope, sync_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        now, as_of, json.dumps(data),
        regimes.get("us_regime"), regimes.get("us_slope"),
        regimes.get("global_regime"), regimes.get("global_slope"),
        regimes.get("m2_regime"), regimes.get("m2_slope"),
        "ok",
    ))

    # Store individual sections for easy access
    sections = [
        "meta", "cycle", "pmi", "cpi", "truflation", "gdp", "rates",
        "unemployment", "us_labour", "yields", "dxy", "financial_conditions",
        "liquidity", "real_rates", "commodities", "indices", "stocks",
        "uk_housing", "crypto", "geopolitics", "key_dates", "what_changed",
    ]
    for key in sections:
        if key in data:
            execute("""
                INSERT INTO nimbus_macro (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at
            """, (key, json.dumps(data[key]), now))

    # Store regime as separate key for fast access
    execute("""
        INSERT INTO nimbus_macro (key, value, updated_at)
        VALUES ('regimes', %s, %s)
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = EXCLUDED.updated_at
    """, (json.dumps(regimes), now))

    log.info("Stored Nimbus data (as_of: %s) with regimes: US=%s Global=%s M2=%s",
             as_of, regimes.get("us_regime"), regimes.get("global_regime"), regimes.get("m2_regime"))


# ---------------------------------------------------------------------------
# SSH fetch
# ---------------------------------------------------------------------------
def fetch_nimbus_via_ssh() -> dict | None:
    """SSH into Jingubang and read nimbus_data.py, parse DATA dict."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
             JINGUBANG_HOST, f"cat {NIMBUS_DATA_PATH}"],
            capture_output=True, text=True, timeout=SSH_TIMEOUT,
        )
        if result.returncode != 0:
            log.error("SSH failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return None

        content = result.stdout
        if "DATA = {" not in content and "DATA = " not in content:
            log.error("nimbus_data.py doesn't contain DATA dict")
            return None

        # Extract the dict assignment
        # Find "DATA = " and parse everything after it
        idx = content.index("DATA = ")
        dict_str = content[idx + len("DATA = "):]

        # Use ast.literal_eval for safe parsing
        data = ast.literal_eval(dict_str)
        log.info("Parsed Nimbus DATA with %d top-level keys", len(data))
        return data

    except subprocess.TimeoutExpired:
        log.error("SSH timed out after %ds", SSH_TIMEOUT)
    except (ValueError, SyntaxError) as e:
        log.error("Failed to parse nimbus_data.py: %s", e)
    except Exception as e:
        log.error("Nimbus fetch failed: %s", e)
    return None


def fetch_tv_regime_via_ssh() -> dict | None:
    """Fetch TradingView regime from Jingubang state.db.
    This is the LIVE regime from TradingView webhooks, which may differ
    from FRED calculations due to different data sources."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", JINGUBANG_HOST,
             "python3 /home/jingubang/jingubang-bot/get_tv_regime.py"],
            capture_output=True, text=True, timeout=SSH_TIMEOUT,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            log.info("TV regime from Jingubang: %s", data)
            return data
    except Exception as e:
        log.error("TV regime fetch failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# FRED regime computation — matches Jingubang exactly
# ---------------------------------------------------------------------------
def _fred_fetch(series_id: str, days_ago: int = 0) -> float | None:
    """Fetch FRED series value, optionally from N days ago."""
    import requests
    if not FRED_API_KEY:
        return None
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,
        }
        if days_ago > 0:
            from datetime import timedelta
            target = datetime.now(timezone.utc) - timedelta(days=days_ago)
            params["observation_start"] = (target - timedelta(days=10)).strftime("%Y-%m-%d")
            params["observation_end"] = target.strftime("%Y-%m-%d")

        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        for o in resp.json().get("observations", []):
            if o["value"] != ".":
                return float(o["value"])
    except Exception as e:
        log.error("FRED %s error: %s", series_id, e)
    return None


def compute_regime_from_slope(net_now: float, net_30d: float) -> tuple[str, float]:
    """Compute regime from 30-day slope. EXACT same logic as Jingubang."""
    if abs(net_30d) < 0.001:
        return "UNKNOWN", 0.0
    slope_pct = ((net_now - net_30d) / abs(net_30d)) * 100
    if slope_pct > REGIME_SLOPE_EXPANDING:
        regime = "EXPANDING"
    elif slope_pct < REGIME_SLOPE_CONTRACTING:
        regime = "CONTRACTING"
    else:
        regime = "STALL"
    return regime, round(slope_pct, 3)


def compute_us_regime() -> tuple[str, float, float | None]:
    """Compute US Net Liquidity regime from FRED data.
    Returns (regime, slope_pct, current_net_liq_T)."""
    fed_now = _fred_fetch("WALCL")
    tga_now = _fred_fetch("WTREGEN")
    rrp_now = _fred_fetch("RRPONTSYD")
    fed_30d = _fred_fetch("WALCL", 30)
    tga_30d = _fred_fetch("WTREGEN", 30)
    rrp_30d = _fred_fetch("RRPONTSYD", 30)

    if None in (fed_now, tga_now, rrp_now, fed_30d, tga_30d, rrp_30d):
        return "UNKNOWN", 0.0, None

    # EXACT same formula as Jingubang app.py line 363-373
    net_now = (fed_now / 1e6) - (tga_now / 1e6) - (rrp_now / 1e3)
    net_30d = (fed_30d / 1e6) - (tga_30d / 1e6) - (rrp_30d / 1e3)

    regime, slope = compute_regime_from_slope(net_now, net_30d)
    log.info("US regime: %s (slope=%.3f%%, now=%.2fT, 30d=%.2fT)", regime, slope, net_now, net_30d)
    return regime, slope, round(net_now, 2)


def compute_global_regime() -> tuple[str, float, float | None]:
    """Compute Global Net Liquidity regime (Fed+ECB+BOJ)."""
    fed_now = _fred_fetch("WALCL")
    tga_now = _fred_fetch("WTREGEN")
    rrp_now = _fred_fetch("RRPONTSYD")
    ecb_now = _fred_fetch("ECBASSETSW")
    boj_now = _fred_fetch("JPNASSETS")

    fed_30d = _fred_fetch("WALCL", 30)
    tga_30d = _fred_fetch("WTREGEN", 30)
    rrp_30d = _fred_fetch("RRPONTSYD", 30)
    ecb_30d = _fred_fetch("ECBASSETSW", 30)
    boj_30d = _fred_fetch("JPNASSETS", 30)

    if None in (fed_now, tga_now, rrp_now, fed_30d, tga_30d, rrp_30d):
        return "UNKNOWN", 0.0, None

    us_now = (fed_now / 1e6) - (tga_now / 1e6) - (rrp_now / 1e3)
    us_30d = (fed_30d / 1e6) - (tga_30d / 1e6) - (rrp_30d / 1e3)

    ecb_usd_now = (ecb_now * 1.08 / 1e6) if ecb_now else 0
    ecb_usd_30d = (ecb_30d * 1.08 / 1e6) if ecb_30d else 0
    boj_usd_now = (boj_now * 0.0067 / 1e6) if boj_now else 0
    boj_usd_30d = (boj_30d * 0.0067 / 1e6) if boj_30d else 0

    global_now = us_now + ecb_usd_now + boj_usd_now
    global_30d = us_30d + ecb_usd_30d + boj_usd_30d

    regime, slope = compute_regime_from_slope(global_now, global_30d)
    log.info("Global regime: %s (slope=%.3f%%, now=%.2fT, 30d=%.2fT)", regime, slope, global_now, global_30d)
    return regime, slope, round(global_now, 2)


def compute_m2_regime() -> tuple[str, float, float | None]:
    """Compute Global M2 regime from FRED US M2."""
    m2_now = _fred_fetch("M2SL")
    m2_30d = _fred_fetch("M2SL", 30)

    if m2_now is None or m2_30d is None:
        return "UNKNOWN", 0.0, None

    now_t = m2_now / 1e3
    ago_t = m2_30d / 1e3

    regime, slope = compute_regime_from_slope(now_t, ago_t)
    log.info("M2 regime: %s (slope=%.3f%%, now=%.2fT, 30d=%.2fT)", regime, slope, now_t, ago_t)
    return regime, slope, round(now_t, 2)


# ---------------------------------------------------------------------------
# Data accessors for other modules
# ---------------------------------------------------------------------------
def get_nimbus_data() -> dict | None:
    """Get the latest Nimbus data from DB."""
    row = execute_one("""
        SELECT data_json FROM nimbus_sync
        ORDER BY synced_at DESC LIMIT 1
    """)
    if row:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return None


def get_nimbus_section(key: str) -> dict | None:
    """Get a specific Nimbus section (e.g., 'pmi', 'cpi', 'rates')."""
    row = execute_one("SELECT value FROM nimbus_macro WHERE key = %s", (key,))
    if row:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return None


def get_regimes() -> dict:
    """Get current liquidity regimes."""
    row = execute_one("SELECT value FROM nimbus_macro WHERE key = 'regimes'")
    if row:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return {}


def get_staleness() -> dict:
    """Check how stale the Nimbus data is."""
    row = execute_one("""
        SELECT as_of_date, synced_at FROM nimbus_sync
        ORDER BY synced_at DESC LIMIT 1
    """)
    if not row:
        return {"is_stale": True, "days_stale": 999, "as_of_date": "never", "synced_at": None}

    as_of_str, synced_at = row
    try:
        as_of = datetime.strptime(as_of_str, "%Y-%m-%d").date()
        days = (date.today() - as_of).days
    except Exception:
        days = 999

    return {
        "as_of_date": as_of_str,
        "synced_at": synced_at.isoformat() if synced_at else None,
        "days_stale": days,
        "is_stale": days > 30,
    }


# ---------------------------------------------------------------------------
# Formatters for synthesis engine and daily report
# ---------------------------------------------------------------------------
def format_macro_for_synthesis() -> str:
    """Format full macro context for synthesis engine prompt."""
    data = get_nimbus_data()
    regimes = get_regimes()
    staleness = get_staleness()

    if not data:
        return "MACRO: No Nimbus data available (never synced)."

    parts = []
    parts.append(f"MACRO DATA (as of {staleness['as_of_date']}, {staleness['days_stale']}d ago):")

    # PMI
    pmi = data.get("pmi", {})
    if pmi:
        months = pmi.get("months", [])
        us = pmi.get("us", [])
        global_w = pmi.get("global_weighted", [])
        parts.append(f"PMI: US Mfg {us[-1] if us else '?'}, Global {global_w[-1] if global_w else '?'} "
                     f"({'above 50 = expansion' if (global_w and global_w[-1] and global_w[-1] > 50) else 'below 50 = contraction'})")

    # CPI
    cpi = data.get("cpi", {})
    truf = data.get("truflation", {})
    if cpi:
        us_cpi = cpi.get("us", [])
        parts.append(f"CPI: US {us_cpi[-1] if us_cpi else '?'}%, Truflation {truf.get('current', '?')}% "
                     f"(gap {round((us_cpi[-1] or 0) - (truf.get('current') or 0), 2)}%)")

    # Rates
    rates = data.get("rates", {})
    if rates:
        fed = rates.get("fed", {})
        parts.append(f"Rates: Fed {fed.get('rate', '?')}% (next: {fed.get('next', '?')})")

    # Liquidity regimes — THE CRITICAL SECTION
    if regimes:
        parts.append(
            f"LIQUIDITY REGIME: "
            f"US Net Liq {regimes.get('us_regime', '?')} (slope {regimes.get('us_slope', 0):+.3f}%) | "
            f"Global {regimes.get('global_regime', '?')} (slope {regimes.get('global_slope', 0):+.3f}%) | "
            f"M2 {regimes.get('m2_regime', '?')} (slope {regimes.get('m2_slope', 0):+.3f}%)"
        )
        parts.append(f"US Net Liq: ${regimes.get('us_net_liq_T', '?')}T | "
                     f"Global: ${regimes.get('global_net_liq_T', '?')}T | "
                     f"M2: ${regimes.get('m2_T', '?')}T")

    # DXY
    dxy = data.get("dxy", {})
    if dxy:
        vals = dxy.get("values", [])
        if len(vals) >= 2:
            pct = ((vals[-1] - vals[0]) / vals[0]) * 100
            parts.append(f"DXY: {vals[-1]} ({pct:+.1f}% trend)")

    # Geopolitics
    geo = data.get("geopolitics", {})
    if geo:
        parts.append(f"Geopolitics: Iran war {geo.get('iran_war', 'unknown')}")

    # Yields
    y = data.get("yields", {})
    if y:
        us10 = y.get("us_10y", [])
        spread = y.get("spread_10_2", [])
        parts.append(f"Yields: 10Y {us10[-1] if us10 else '?'}%, 10-2 spread {spread[-1] if spread else '?'}%")

    # Commodities
    com = data.get("commodities", {})
    if com:
        gold = com.get("gold", {}).get("price", "?")
        oil = com.get("wti_oil", {}).get("price", "?")
        parts.append(f"Gold: ${gold} | Oil: ${oil}")

    # Stocks proxies
    stk = data.get("stocks", {})
    if stk:
        mstr = stk.get("mstr", {})
        coin = stk.get("coin", {})
        parts.append(f"Proxies: MSTR ${mstr.get('price', '?')} (mNAV {mstr.get('mnav', '?')}) | "
                     f"COIN ${coin.get('price', '?')}")

    if staleness["is_stale"]:
        parts.append(f"⚠️ STALE DATA: Nimbus {staleness['days_stale']} days old!")

    return "\n".join(parts)


def format_regime_for_report() -> str:
    """Format regime display for daily report (matches TradingView Wukong)."""
    regimes = get_regimes()
    if not regimes:
        return "⚠️ No FRED regime data"

    def emoji(regime):
        if regime == "EXPANDING": return "✅"
        if regime == "CONTRACTING": return "🔴"
        if regime == "STALL": return "⏳"
        return "⚪"

    us = regimes.get("us_regime", "UNKNOWN")
    gl = regimes.get("global_regime", "UNKNOWN")
    m2 = regimes.get("m2_regime", "UNKNOWN")

    tv = regimes.get("tv_regime")
    tv_line = f"\n  TV webhook: {tv}" if tv and tv != us else ""

    return (
        f"<b>WUKONG REGIME (FRED)</b>\n"
        f"  {emoji(us)} US Net Liq: {us} ({regimes.get('us_slope', 0):+.3f}%) ${regimes.get('us_net_liq_T', '?')}T\n"
        f"  {emoji(gl)} Global: {gl} ({regimes.get('global_slope', 0):+.3f}%) ${regimes.get('global_net_liq_T', '?')}T\n"
        f"  {emoji(m2)} M2: {m2} ({regimes.get('m2_slope', 0):+.3f}%) ${regimes.get('m2_T', '?')}T"
        f"{tv_line}"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def run_sync(compute_regimes: bool = True) -> dict:
    """Full sync: SSH fetch + FRED regime computation + store."""
    ensure_tables()

    # 1. SSH fetch Nimbus data
    log.info("Fetching Nimbus data from Jingubang...")
    data = fetch_nimbus_via_ssh()
    if not data:
        log.error("Nimbus sync FAILED — SSH fetch returned None")
        return {"status": "error", "reason": "ssh_fetch_failed"}

    as_of = data.get("meta", {}).get("as_of_date", "unknown")
    log.info("Got Nimbus data, as_of: %s", as_of)

    # 1b. Also fetch TradingView regime from Jingubang state.db
    tv_regime = fetch_tv_regime_via_ssh()

    # 2. Compute FRED regimes (same thresholds as Jingubang/TradingView)
    regimes = {}
    if compute_regimes:
        log.info("Computing FRED regimes...")
        us_regime, us_slope, us_net = compute_us_regime()
        gl_regime, gl_slope, gl_net = compute_global_regime()
        m2_regime, m2_slope, m2_val = compute_m2_regime()

        regimes = {
            "us_regime": us_regime,
            "us_slope": us_slope,
            "us_net_liq_T": us_net,
            "global_regime": gl_regime,
            "global_slope": gl_slope,
            "global_net_liq_T": gl_net,
            "m2_regime": m2_regime,
            "m2_slope": m2_slope,
            "m2_T": m2_val,
            "tv_regime": tv_regime.get("tv_regime") if tv_regime else None,
            "tv_fred_regime": tv_regime.get("fred_regime") if tv_regime else None,
            "tv_fred_slope": tv_regime.get("fred_slope") if tv_regime else None,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
        log.info("Regimes: US=%s Global=%s M2=%s", us_regime, gl_regime, m2_regime)

    # 3. Store everything
    store_nimbus(data, regimes)

    return {
        "status": "ok",
        "as_of_date": as_of,
        "regimes": regimes,
        "sections": list(data.keys()),
    }


if __name__ == "__main__":
    import sys
    result = run_sync()
    if result["status"] == "ok":
        print(f"✅ Nimbus sync OK (as_of: {result['as_of_date']})")
        r = result["regimes"]
        print(f"   US:     {r.get('us_regime', '?')} (slope {r.get('us_slope', 0):+.3f}%)")
        print(f"   Global: {r.get('global_regime', '?')} (slope {r.get('global_slope', 0):+.3f}%)")
        print(f"   M2:     {r.get('m2_regime', '?')} (slope {r.get('m2_slope', 0):+.3f}%)")
        print(f"\n{format_regime_for_report()}")
    else:
        print(f"❌ Sync failed: {result.get('reason')}")
        sys.exit(1)
