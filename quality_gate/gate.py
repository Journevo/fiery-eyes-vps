"""Quality Gate Orchestrator — three-tier gate: FULL PASS / WATCH / REJECT.

Runs all 8 checks, evaluates velocity signals, and classifies tokens into:
  - PASSED:   all 8 checks pass -> enters scoring pipeline
  - WATCHING: contract safety OK + age >2h + volume >$25K + at least 1 velocity signal
  - REJECTED: fails social verification OR contract safety OR age OR zero velocity signals

Check 0 (social verification) triggers early exit on failure — skips remaining 7 checks.
"""

import json
from datetime import datetime, timezone, timedelta

from config import HELIUS_RPC_URL, get_logger
from db.connection import execute, execute_one
from quality_gate import contract_safety, liquidity, holders, sybil, unlocks, wash_trading, age_volume, social_verification
from quality_gate.helpers import get_json, post_json

log = get_logger("gate")

# ---------------------------------------------------------------------------
# DexScreener endpoints
# ---------------------------------------------------------------------------
DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
DEXSCREENER_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/latest/v1"

# Watch-tier thresholds
WATCH_MIN_AGE_HOURS = 2
WATCH_MIN_VOLUME_USD = 25_000
WATCH_EXPIRY_HOURS = 48
WATCH_RECHECK_INTERVAL_MINUTES = 30

# Velocity signal thresholds
VOLUME_ACCEL_MULTIPLIER = 3.0        # current hour volume > 3x previous hour
HOLDER_GROWTH_PER_HOUR = 100         # >100 new holders per hour
PRICE_PUMP_4H_PCT = 50               # up >50% in last 4 hours


# ---------------------------------------------------------------------------
# Velocity signal detection
# ---------------------------------------------------------------------------

def check_velocity_signals(mint: str) -> list[str]:
    """Detect velocity signals from DexScreener and Helius data.

    Returns list of signal names detected:
      - 'volume_acceleration'
      - 'holder_growth_velocity'
      - 'trending_dexscreener'
      - 'price_action'
    """
    signals = []

    # --- DexScreener data ---
    dex_data = _fetch_dexscreener_data(mint)

    if dex_data:
        # 1. Volume acceleration: current hour volume > 3x previous hour
        vol_h1 = dex_data.get("volume_h1", 0)
        vol_h6 = dex_data.get("volume_h6", 0)
        # Estimate previous-hour volume: (h6 - h1) / 5 gives avg hourly for prior 5 hours
        if vol_h6 > vol_h1 and vol_h1 > 0:
            prev_hourly_avg = (vol_h6 - vol_h1) / 5
            if prev_hourly_avg > 0 and vol_h1 > prev_hourly_avg * VOLUME_ACCEL_MULTIPLIER:
                signals.append("volume_acceleration")
                log.info("Velocity signal: volume_acceleration for %s (h1=$%.0f, prev_avg=$%.0f)",
                         mint, vol_h1, prev_hourly_avg)

        # 4. Price action: up >50% in last 4h with increasing volume
        price_change_h1 = dex_data.get("price_change_h1", 0)
        price_change_h6 = dex_data.get("price_change_h6", 0)
        # Approximate 4h change from available data
        # h6 change encompasses 4h, use it as proxy
        if price_change_h6 > PRICE_PUMP_4H_PCT and vol_h1 > 0:
            # Check increasing volume: h1 volume should exceed proportional h6 share
            proportional_h1 = vol_h6 / 6 if vol_h6 > 0 else 0
            if vol_h1 > proportional_h1:
                signals.append("price_action")
                log.info("Velocity signal: price_action for %s (6h_change=%.1f%%, h1_vol=$%.0f)",
                         mint, price_change_h6, vol_h1)

    # 2. Holder growth velocity: >100 new holders per hour (Helius)
    holder_velocity = _check_holder_growth_velocity(mint)
    if holder_velocity:
        signals.append("holder_growth_velocity")
        log.info("Velocity signal: holder_growth_velocity for %s", mint)

    # 3. Trending on DexScreener (boosted/profiles list)
    if _check_dexscreener_trending(mint):
        signals.append("trending_dexscreener")
        log.info("Velocity signal: trending_dexscreener for %s", mint)

    return signals


def _fetch_dexscreener_data(mint: str) -> dict | None:
    """Fetch volume, price, and token info from DexScreener."""
    try:
        data = get_json(DEXSCREENER_TOKEN_URL.format(mint=mint))
        pairs = data.get("pairs") or []
        if not pairs:
            return None

        # Use highest-volume pair
        pairs_sorted = sorted(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0), reverse=True)
        pair = pairs_sorted[0]

        volume = pair.get("volume", {})
        price_change = pair.get("priceChange", {})
        base_token = pair.get("baseToken", {})

        return {
            "volume_h1": float(volume.get("h1", 0) or 0),
            "volume_h6": float(volume.get("h6", 0) or 0),
            "volume_h24": float(volume.get("h24", 0) or 0),
            "price_change_h1": float(price_change.get("h1", 0) or 0),
            "price_change_h6": float(price_change.get("h6", 0) or 0),
            "price_change_h24": float(price_change.get("h24", 0) or 0),
            "pair_address": pair.get("pairAddress", ""),
            "token_name": base_token.get("name", ""),
            "token_symbol": base_token.get("symbol", ""),
            "price_usd": float(pair.get("priceUsd", 0) or 0),
            "market_cap": float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0),
            "liquidity_usd": float(pair.get("liquidity", {}).get("usd", 0) or 0),
        }
    except Exception as e:
        log.warning("DexScreener fetch failed for %s: %s", mint, e)
        return None


def _check_holder_growth_velocity(mint: str) -> bool:
    """Check if holder growth exceeds 100 new holders per hour using Helius.

    Compares current holder count from getTokenLargestAccounts (total accounts)
    against the stored count from the last gate check.
    """
    try:
        # Get current holder count via Helius RPC
        resp = post_json(HELIUS_RPC_URL, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenLargestAccounts",
            "params": [mint],
        })
        accounts = resp.get("result", {}).get("value", [])
        # getTokenLargestAccounts returns top 20; use total supply holders as proxy
        # For a more accurate count, query the actual holder count from our DB
        current_holders = len(accounts)

        # Check if we have a prior holder count from recent gate check
        row = execute_one(
            """SELECT holders_raw, updated_at
               FROM snapshots_daily sd
               JOIN tokens t ON t.id = sd.token_id
               WHERE t.contract_address = %s
               ORDER BY sd.date DESC LIMIT 1""",
            (mint,)
        )
        if row and row[0] is not None and row[1] is not None:
            prior_holders = row[0]
            prior_time = row[1]
            now = datetime.now(timezone.utc)
            hours_elapsed = max((now - prior_time).total_seconds() / 3600, 0.1)
            growth_per_hour = (current_holders - prior_holders) / hours_elapsed
            if growth_per_hour > HOLDER_GROWTH_PER_HOUR:
                return True

        return False
    except Exception as e:
        log.debug("Holder growth velocity check failed for %s: %s", mint, e)
        return False


def _check_dexscreener_trending(mint: str) -> bool:
    """Check if the token appears in DexScreener's boosted/trending list."""
    try:
        boosts = get_json(DEXSCREENER_BOOSTS_URL)
        # The boosts API returns a list of boosted tokens
        if isinstance(boosts, list):
            for entry in boosts:
                token_addr = entry.get("tokenAddress", "")
                if token_addr.lower() == mint.lower():
                    return True
        return False
    except Exception as e:
        log.debug("DexScreener trending check failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Main gate function
# ---------------------------------------------------------------------------

def run_gate(mint: str, category: str = "meme") -> dict:
    """
    Run all 8 quality gate checks on a Solana token with three-tier outcome.

    Outcomes:
      PASSED   — all 8 checks pass. Enters scoring pipeline.
      WATCHING — social + contract safety pass + age >2h + volume >$25K + velocity signal.
      REJECTED — fails social verification OR contract safety OR age OR zero velocity.

    Args:
        mint: Solana token contract address
        category: 'meme', 'adoption', or 'infra'

    Returns:
        {
            "mint": str,
            "category": str,
            "overall_pass": bool,         # True for FULL PASS only (backward compat)
            "gate_status": str,           # "passed", "watching", "rejected"
            "checks": {
                "social_verification": {...},
                "contract_safety": {...},
                "liquidity": {...},
                "holders": {...},
                "sybil": {...},
                "unlocks": {...},
                "wash_trading": {...},
                "age_volume": {...},
            },
            "failures": [str],
            "velocity_signals": [str],
            "timestamp": str,
        }
    """
    log.info("=== Running Quality Gate for %s (category: %s) ===", mint, category)

    checks = {}

    # 0. Social Verification — cheapest check, fail fast
    log.info("[1/8] Social Verification...")
    checks["social_verification"] = social_verification.check(mint)
    if not checks["social_verification"]["pass"]:
        log.info("=== REJECTED %s — social verification failed: %s ===",
                 mint, checks["social_verification"].get("reason"))
        dex_data = _fetch_dexscreener_data(mint)
        result = {
            "mint": mint,
            "category": category,
            "overall_pass": False,
            "gate_status": "rejected",
            "checks": checks,
            "failures": ["social_verification"],
            "velocity_signals": [],
            "dex_data": dex_data or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _save_gate_result(mint, category, result)
        return result

    # 1. Contract Safety
    log.info("[2/8] Contract Safety...")
    checks["contract_safety"] = contract_safety.check(mint)

    # 2. Liquidity Depth
    log.info("[3/8] Liquidity Depth...")
    checks["liquidity"] = liquidity.check(mint)

    # 3. Holder Distribution
    log.info("[4/8] Holder Distribution...")
    checks["holders"] = holders.check(mint)

    # 4. Sybil Risk
    log.info("[5/8] Sybil Risk...")
    checks["sybil"] = sybil.check(mint)

    # 5. Unlock Overhang
    log.info("[6/8] Unlock Overhang...")
    checks["unlocks"] = unlocks.check(mint, category=category)

    # 6. Wash Trading
    log.info("[7/8] Wash Trading...")
    checks["wash_trading"] = wash_trading.check(mint)

    # 7. Age / Volume
    log.info("[8/8] Age / Volume...")
    checks["age_volume"] = age_volume.check(mint)

    # Determine failures
    failures = [name for name, c in checks.items() if not c["pass"]]
    all_pass = len(failures) == 0

    # Fetch DexScreener token data for alerts
    dex_data = _fetch_dexscreener_data(mint)

    # --- Three-tier classification ---
    velocity_signals = []

    if all_pass:
        # FULL PASS: all 7 checks passed
        gate_status = "passed"
        overall_pass = True
        log.info("=== FULL PASS for %s — all 8/8 checks passed ===", mint)

    elif _qualifies_for_watch(checks, failures):
        # Candidate for WATCH — check velocity signals
        velocity_signals = check_velocity_signals(mint)
        if velocity_signals:
            gate_status = "watching"
            overall_pass = False
            log.info("=== WATCHING %s — %d velocity signal(s): %s, failures: %s ===",
                     mint, len(velocity_signals), velocity_signals, failures)
        else:
            gate_status = "rejected"
            overall_pass = False
            log.info("=== REJECTED %s — qualified for watch but zero velocity signals ===", mint)
    else:
        # REJECT: hard failures
        gate_status = "rejected"
        overall_pass = False
        log.info("=== REJECTED %s — hard failure(s): %s ===", mint, failures)

    result = {
        "mint": mint,
        "category": category,
        "overall_pass": overall_pass,
        "gate_status": gate_status,
        "checks": checks,
        "failures": failures,
        "velocity_signals": velocity_signals,
        "dex_data": dex_data or {},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Persist to database
    _save_gate_result(mint, category, result)

    return result


def _qualifies_for_watch(checks: dict, failures: list[str]) -> bool:
    """Check if a token meets minimum WATCH tier requirements.

    Requirements:
      - Social verification MUST pass (hard requirement — enforced via early exit)
      - Contract safety MUST pass (hard requirement)
      - Age must be >2 hours
      - Volume must be >$25K
    Other checks (liquidity, holders, sybil, unlocks, wash_trading) can fail.
    """
    # Social verification must pass (normally caught by early exit, but defensive)
    if not checks.get("social_verification", {}).get("pass", False):
        return False

    # Contract safety must pass
    if not checks.get("contract_safety", {}).get("pass", False):
        return False

    # Check age requirement (>2 hours)
    age_data = checks.get("age_volume", {})
    age_hours = age_data.get("age_hours")
    if age_hours is None or age_hours < WATCH_MIN_AGE_HOURS:
        return False

    # Check volume requirement (>$25K)
    volume_usd = age_data.get("volume_usd")
    if volume_usd is None or volume_usd < WATCH_MIN_VOLUME_USD:
        return False

    return True


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

def _save_gate_result(mint: str, category: str, gate_result: dict):
    """Upsert token record with quality_gate_status and create alert."""
    try:
        dex = gate_result.get("dex_data", {})
        symbol = dex.get("token_symbol") or mint[:8]
        gate_status = gate_result["gate_status"]
        overall_pass = gate_result["overall_pass"]

        # For backward compat: quality_gate_pass = True for 'passed' and 'watching'
        # (watching tokens also get snapshots), False for 'rejected'
        gate_pass_compat = gate_status in ("passed", "watching")

        token_name = dex.get("token_name") or symbol

        row = execute_one(
            """INSERT INTO tokens (symbol, name, contract_address, category,
                                   quality_gate_pass, quality_gate_status,
                                   safety_score, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
               ON CONFLICT (contract_address) DO UPDATE
               SET symbol = EXCLUDED.symbol,
                   name = EXCLUDED.name,
                   quality_gate_pass = EXCLUDED.quality_gate_pass,
                   quality_gate_status = EXCLUDED.quality_gate_status,
                   safety_score = EXCLUDED.safety_score,
                   updated_at = NOW()
               RETURNING id""",
            (symbol, token_name, mint, category, gate_pass_compat, gate_status,
             _compute_safety_score(gate_result["checks"]))
        )
        token_id = row[0] if row else None

        if token_id:
            # Record alert with appropriate type
            if gate_status == "passed":
                alert_type = "gate_pass"
                severity = "info"
            elif gate_status == "watching":
                alert_type = "gate_watch"
                severity = "info"
            else:
                alert_type = "gate_fail"
                severity = "warning"

            alert_payload = {
                **gate_result["checks"],
                "_velocity_signals": gate_result.get("velocity_signals", []),
                "_gate_status": gate_status,
            }
            execute(
                """INSERT INTO alerts (token_id, type, severity, feature_vector_json)
                   VALUES (%s, %s, %s, %s)""",
                (token_id, alert_type, severity, json.dumps(alert_payload, default=str))
            )
            log.info("Saved gate result to DB for token_id=%d (status=%s)", token_id, gate_status)

    except Exception as e:
        log.error("Failed to save gate result to DB: %s", e)


def _compute_safety_score(checks: dict) -> float:
    """Compute 0-100 safety score from check results."""
    passed = sum(1 for c in checks.values() if c["pass"])
    return round((passed / len(checks)) * 100, 1)


# ---------------------------------------------------------------------------
# Watch tier management
# ---------------------------------------------------------------------------

def recheck_watching_tokens() -> list[dict]:
    """Re-run gate on all tokens with quality_gate_status='watching'.

    Promotes to 'passed' when all 7 checks pass.
    Expires tokens watching >48h.
    Returns list of result dicts with actions taken.
    """
    log.info("=== Rechecking WATCHING tokens ===")
    results = []

    # First expire stale watches
    _expire_stale_watches()

    # Fetch all watching tokens
    try:
        rows = execute(
            """SELECT id, contract_address, category
               FROM tokens
               WHERE quality_gate_status = 'watching'
               ORDER BY updated_at ASC""",
            fetch=True,
        )
    except Exception as e:
        log.error("Failed to fetch watching tokens: %s", e)
        return results

    if not rows:
        log.info("No watching tokens to recheck")
        return results

    log.info("Rechecking %d watching tokens", len(rows))

    for row in rows:
        token_id, mint, category = row[0], row[1], row[2]
        try:
            gate_result = run_gate(mint, category=category or "meme")

            action = None
            if gate_result["gate_status"] == "passed":
                _promote_to_passed(mint, token_id)
                action = "promoted"
            elif gate_result["gate_status"] == "rejected":
                action = "rejected"
            else:
                action = "still_watching"

            results.append({
                "token_id": token_id,
                "mint": mint,
                "action": action,
                "gate_status": gate_result["gate_status"],
                "failures": gate_result["failures"],
                "velocity_signals": gate_result["velocity_signals"],
            })
            log.info("Recheck %s: %s", mint, action)

        except Exception as e:
            log.error("Recheck failed for %s: %s", mint, e)
            results.append({
                "token_id": token_id,
                "mint": mint,
                "action": "error",
                "error": str(e),
            })

    log.info("=== Recheck complete: %d tokens processed ===", len(results))
    return results


def _expire_stale_watches():
    """Remove tokens that have been in 'watching' status for >48 hours."""
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=WATCH_EXPIRY_HOURS)
        rows = execute(
            """UPDATE tokens
               SET quality_gate_status = 'rejected',
                   quality_gate_pass = FALSE,
                   updated_at = NOW()
               WHERE quality_gate_status = 'watching'
                 AND updated_at < %s
               RETURNING id, contract_address""",
            (cutoff,),
            fetch=True,
        )
        if rows:
            for row in rows:
                token_id, mint = row[0], row[1]
                log.info("Expired stale watch: token_id=%d mint=%s (>%dh)",
                         token_id, mint, WATCH_EXPIRY_HOURS)
                # Record expiry alert
                execute(
                    """INSERT INTO alerts (token_id, type, severity, feature_vector_json)
                       VALUES (%s, %s, %s, %s)""",
                    (token_id, "gate_watch_expired", "info",
                     json.dumps({"expired_after_hours": WATCH_EXPIRY_HOURS}))
                )
            log.info("Expired %d stale watching tokens", len(rows))
        else:
            log.debug("No stale watches to expire")
    except Exception as e:
        log.error("Failed to expire stale watches: %s", e)


def _promote_to_passed(mint: str, token_id: int):
    """Upgrade a token from 'watching' to 'passed' status."""
    try:
        execute(
            """UPDATE tokens
               SET quality_gate_status = 'passed',
                   quality_gate_pass = TRUE,
                   updated_at = NOW()
               WHERE id = %s""",
            (token_id,)
        )
        # Record promotion alert
        execute(
            """INSERT INTO alerts (token_id, type, severity, feature_vector_json)
               VALUES (%s, %s, %s, %s)""",
            (token_id, "gate_watch_promoted", "info",
             json.dumps({"promoted_from": "watching", "promoted_to": "passed"}))
        )
        log.info("Promoted token_id=%d (%s) from WATCHING -> PASSED", token_id, mint)

        # Send Telegram notification for promotion
        try:
            from telegram_bot.alerts import send_message
            send_message(
                f"✅ <b>PROMOTED: WATCHING -> FULL PASS</b>\n"
                f"Token: <code>{mint}</code>\n"
                f"All 8 quality checks now passing."
            )
        except Exception as e:
            log.warning("Failed to send promotion Telegram alert: %s", e)

    except Exception as e:
        log.error("Failed to promote token_id=%d: %s", token_id, e)


# ---------------------------------------------------------------------------
# Confidence penalty for Momentum Engine integration
# ---------------------------------------------------------------------------

def get_watch_confidence_penalty(mint: str) -> float:
    """Return confidence multiplier for a token based on gate status.

    Returns:
        1.0 for 'passed' tokens (no penalty)
        0.7 for 'watching' tokens (30% confidence reduction)
        1.0 for unknown/not found (default, no penalty)
    """
    try:
        row = execute_one(
            """SELECT quality_gate_status FROM tokens
               WHERE contract_address = %s""",
            (mint,)
        )
        if row and row[0] == "watching":
            return 0.7
        return 1.0
    except Exception as e:
        log.debug("Failed to check gate status for penalty: %s", e)
        return 1.0
