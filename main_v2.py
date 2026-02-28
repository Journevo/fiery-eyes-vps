#!/usr/bin/env python3
"""Fiery Eyes v2 Main Loop — persistent daemon.

Runs continuously:
- Every 3min: Check Tier 1 KOL wallets
- Every 10min: Check Tier 2 KOL wallets, detect convergence
- Every 15min: Score Hatchling tokens
- Every 30min: Score Runner tokens, check DexScreener trending
- Every 4h:  Score Established tokens, generate Huoyan pulse, update regime
- Daily 03:00: Moonbag reaper
- Daily 06:00: Morning briefing (extended Huoyan)

KK Telegram listener runs in separate service (fiery-eyes-kk).
"""

import time
import threading
from datetime import datetime, timezone
from config import get_logger

log = get_logger("main_v2")

# Schedule intervals in seconds
INTERVAL_TIER1_KOL = 180
INTERVAL_TIER2_KOL = 600
INTERVAL_HATCHLING = 900
INTERVAL_RUNNER = 1800
INTERVAL_SMART_MONEY = 1800   # 30min — same as runner
INTERVAL_ESTABLISHED = 14400
INTERVAL_HUOYAN = 14400
INTERVAL_REGIME = 14400

# Last run timestamps
_last_run = {
    'tier1_kol': 0,
    'tier2_kol': 0,
    'hatchling': 0,
    'runner': 0,
    'smart_money': 0,
    'established': 0,
    'huoyan': 0,
    'regime': 0,
    'moonbag_reaper': 0,
}


def _should_run(task: str, interval: int) -> bool:
    """Check if a task should run based on its interval."""
    return time.time() - _last_run.get(task, 0) >= interval


def _mark_run(task: str):
    """Mark a task as having just run."""
    _last_run[task] = time.time()


def run_tier1_kol():
    """Check Tier 1 KOL wallets only."""
    try:
        from kol_tracking.monitor import check_kol_wallets
        check_kol_wallets(tier_filter=1)
    except Exception as e:
        log.error("Tier 1 KOL check failed: %s", e)


def run_tier2_kol():
    """Check Tier 2 KOL wallets + convergence detection."""
    try:
        from kol_tracking.monitor import check_kol_wallets, detect_convergence
        check_kol_wallets(tier_filter=2)
        convergences = detect_convergence()
        if convergences:
            from telegram_bot.severity import route_alert
            for c in convergences:
                msg = (f"🟡 <b>KOL CONVERGENCE</b>\n"
                       f"🪙 ${c['token_symbol'] or c['token_address'][:12]}\n"
                       f"👤 {c['wallet_count']} wallets in 30min")
                route_alert(2, msg)
    except Exception as e:
        log.error("Tier 2 KOL check failed: %s", e)


def run_health_scoring(tier: str):
    """Score tokens of a given tier."""
    try:
        from db.connection import execute
        from health_score.engine import score_token

        tier_filter = {'hatchling': 'hatchling', 'runner': 'runner',
                       'established': 'established'}.get(tier)
        if not tier_filter:
            return

        rows = execute(
            """SELECT contract_address, symbol FROM tokens
               WHERE quality_gate_pass = TRUE
                 AND (token_tier = %s OR token_tier IS NULL)
               ORDER BY updated_at ASC LIMIT 20""",
            (tier_filter,),
            fetch=True,
        )

        scored = 0
        for addr, symbol in (rows or []):
            try:
                score_token(addr, symbol)
                scored += 1
            except Exception as e:
                log.error("Health score failed for %s: %s", symbol or addr[:12], e)

        if scored:
            log.info("Scored %d %s tokens", scored, tier)
    except Exception as e:
        log.error("Health scoring (%s) failed: %s", tier, e)


def run_huoyan():
    """Generate and send Huoyan pulse."""
    try:
        from telegram_bot.huoyan_pulse import generate_pulse
        generate_pulse()
    except Exception as e:
        log.error("Huoyan pulse failed: %s", e)


def run_regime():
    """Update regime state."""
    try:
        from regime.multiplier import calculate_regime
        result = calculate_regime()
        log.info("Regime updated: %.3f (%s)",
                 result['regime_multiplier'], result['allocation_guidance'])
    except Exception as e:
        log.error("Regime update failed: %s", e)


def run_moonbag_reaper():
    """Run moonbag reaper (daily at 03:00 UTC)."""
    try:
        from shadow.moonbag_reaper import run_moonbag_reaper
        run_moonbag_reaper()
    except Exception as e:
        log.error("Moonbag reaper failed: %s", e)


def run_smart_money_poll():
    """Poll smart money X accounts via Grok API."""
    try:
        from social.grok_poller import run_smart_money_poll
        result = run_smart_money_poll()
        total = result.get("total_signals", 0)
        if total > 0:
            log.info("Smart money poll: %d new signals", total)
    except Exception as e:
        log.error("Smart money poll failed: %s", e)


def run_shadow_update():
    """Update open shadow trades."""
    try:
        from shadow.tracker import update_shadow_trades
        update_shadow_trades()
    except Exception as e:
        log.error("Shadow update failed: %s", e)


def main_loop():
    """Main daemon loop."""
    log.info("Fiery Eyes v2 main loop starting")

    try:
        from telegram_bot.alerts import send_message
        send_message("🔥 <b>Fiery Eyes v2</b> daemon started")
    except Exception:
        pass

    # Start health server in background
    try:
        from health import run_health_server
        health_thread = threading.Thread(target=run_health_server, daemon=True)
        health_thread.start()
    except Exception as e:
        log.error("Health server failed to start: %s", e)

    # Initial regime calculation
    run_regime()
    _mark_run('regime')

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Tier 1 KOL wallets — every 60s
            if _should_run('tier1_kol', INTERVAL_TIER1_KOL):
                threading.Thread(target=run_tier1_kol, daemon=True).start()
                _mark_run('tier1_kol')

            # Tier 2 KOL wallets — every 5min
            if _should_run('tier2_kol', INTERVAL_TIER2_KOL):
                threading.Thread(target=run_tier2_kol, daemon=True).start()
                _mark_run('tier2_kol')

            # Hatchling scoring — every 15min
            if _should_run('hatchling', INTERVAL_HATCHLING):
                threading.Thread(
                    target=run_health_scoring, args=('hatchling',), daemon=True
                ).start()
                _mark_run('hatchling')

            # Runner scoring — every 30min
            if _should_run('runner', INTERVAL_RUNNER):
                threading.Thread(
                    target=run_health_scoring, args=('runner',), daemon=True
                ).start()
                # Also update shadow trades
                threading.Thread(target=run_shadow_update, daemon=True).start()
                _mark_run('runner')

            # Smart money X poll — every 30min
            if _should_run('smart_money', INTERVAL_SMART_MONEY):
                threading.Thread(target=run_smart_money_poll, daemon=True).start()
                _mark_run('smart_money')

            # Established scoring + Huoyan + Regime — every 4h
            if _should_run('established', INTERVAL_ESTABLISHED):
                threading.Thread(
                    target=run_health_scoring, args=('established',), daemon=True
                ).start()
                _mark_run('established')

            if _should_run('huoyan', INTERVAL_HUOYAN):
                threading.Thread(target=run_huoyan, daemon=True).start()
                _mark_run('huoyan')

            if _should_run('regime', INTERVAL_REGIME):
                threading.Thread(target=run_regime, daemon=True).start()
                _mark_run('regime')

            # Daily moonbag reaper at 03:00 UTC
            if now.hour == 3 and now.minute < 2:
                if _should_run('moonbag_reaper', 82800):  # ~23h cooldown
                    threading.Thread(target=run_moonbag_reaper, daemon=True).start()
                    _mark_run('moonbag_reaper')

            # Record monitoring heartbeat
            try:
                from monitoring.degraded import record_run_completion
                record_run_completion("main_v2_loop")
            except Exception:
                pass

            time.sleep(30)  # Main loop tick

        except KeyboardInterrupt:
            log.info("Shutting down Fiery Eyes v2")
            break
        except Exception as e:
            log.error("Main loop error: %s", e)
            time.sleep(10)


if __name__ == "__main__":
    main_loop()
