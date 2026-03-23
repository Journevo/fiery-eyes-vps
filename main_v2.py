#!/usr/bin/env python3
"""Fiery Eyes v2 Main Loop — persistent daemon.

Runs continuously:
- Every 3min: Check Tier 1 KOL wallets
- Every 10min: Check Tier 2 KOL wallets, detect convergence
- Every 15min: Score Hatchling tokens
- Every 30min: Score Runner tokens, smart money HIGH poll
- Every 2h:  Smart money MEDIUM poll
- Every 4h:  Holdings+macro snapshot → Huoyan pulse → regime update → established scoring
- Daily 03:00: Moonbag reaper
- Daily 06:00: Chain adoption metrics (DeFiLlama)
- Sunday 08:00: Weekly portfolio review

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
INTERVAL_SMART_MONEY_HIGH = 1800    # 30min — specialized + HIGH generic
INTERVAL_SMART_MONEY_MEDIUM = 7200  # 2hr — MEDIUM generic
INTERVAL_ESTABLISHED = 14400
INTERVAL_HUOYAN = 14400
INTERVAL_REGIME = 14400
INTERVAL_HOLDINGS_MACRO = 14400     # 4h — aligned with Huoyan pulse
INTERVAL_CHAIN_METRICS = 86400      # 24h — daily

# Last run timestamps — staggered so Helius-heavy tasks cascade on startup
# instead of all firing on the first tick.
_now = time.time()
_last_run = {
    'tier1_kol': _now,            # first fire at +180s
    'tier2_kol': _now,            # first fire at +600s
    'hatchling': _now - 840,      # first fire at +60s
    'runner': _now - 1680,        # first fire at +120s
    'smart_money_high': _now,     # first fire at +1800s (no Helius)
    'smart_money_medium': _now,   # first fire at +7200s (no Helius)
    'established': _now,          # first fire at +14400s
    'huoyan': _now,
    'regime': _now,
    'holdings_macro': _now,        # first fire at +14400s (aligned with huoyan)
    'chain_metrics': _now,         # first fire at daily 06:00 UTC
    'moonbag_reaper': 0,
    'weekly_review': 0,
}

# Mutex to prevent KOL polling and health scoring from hitting Helius concurrently
_helius_busy = threading.Lock()


def _should_run(task: str, interval: int) -> bool:
    """Check if a task should run based on its interval."""
    return time.time() - _last_run.get(task, 0) >= interval


def _mark_run(task: str):
    """Mark a task as having just run."""
    _last_run[task] = time.time()


def run_tier1_kol():
    """Check Tier 1 KOL wallets only."""
    if not _helius_busy.acquire(blocking=False):
        log.debug("Skipping Tier 1 KOL — Helius busy")
        return
    try:
        from kol_tracking.monitor import check_kol_wallets
        check_kol_wallets(tier_filter=1)
    except Exception as e:
        log.error("Tier 1 KOL check failed: %s", e)
    finally:
        _helius_busy.release()


def run_tier2_kol():
    """Check Tier 2 KOL wallets + convergence detection."""
    if not _helius_busy.acquire(blocking=False):
        log.debug("Skipping Tier 2 KOL — Helius busy")
        return
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
    finally:
        _helius_busy.release()


V5_WATCHLIST = {'JUP', 'HYPE', 'RENDER', 'BONK', 'SOL', 'PUMP', 'PENGU', 'FARTCOIN', 'USELESS'}


def run_health_scoring(tier: str):
    """Score tokens of a given tier — watchlist tokens first."""
    if not _helius_busy.acquire(blocking=False):
        log.debug("Skipping health scoring (%s) — Helius busy", tier)
        return
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
               ORDER BY
                 CASE WHEN upper(symbol) = ANY(%s) THEN 0 ELSE 1 END ASC,
                 updated_at ASC
               LIMIT 20""",
            (tier_filter, list(V5_WATCHLIST)),
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
    finally:
        _helius_busy.release()


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


def run_smart_money_poll_high():
    """Poll smart money HIGH tier X accounts via Grok API."""
    try:
        from social.grok_poller import run_smart_money_poll_high
        result = run_smart_money_poll_high()
        total = result.get("total_signals", 0)
        if total > 0:
            log.info("Smart money HIGH tier poll: %d new signals", total)
    except Exception as e:
        log.error("Smart money HIGH tier poll failed: %s", e)


def run_smart_money_poll_medium():
    """Poll smart money MEDIUM tier X accounts via Grok API."""
    try:
        from social.grok_poller import run_smart_money_poll_medium
        result = run_smart_money_poll_medium()
        total = result.get("total_signals", 0)
        if total > 0:
            log.info("Smart money MEDIUM tier poll: %d new signals", total)
    except Exception as e:
        log.error("Smart money MEDIUM tier poll failed: %s", e)


def run_shadow_update():
    """Update open shadow trades."""
    try:
        from shadow.tracker import update_shadow_trades
        update_shadow_trades()
    except Exception as e:
        log.error("Shadow update failed: %s", e)


def run_holdings_macro():
    """Collect holdings health + macro regime snapshot (every 4h, before Huoyan)."""
    try:
        from chain_metrics.holdings import collect_holdings_health
        collect_holdings_health()
    except Exception as e:
        log.error("Holdings health collection failed: %s", e)
    try:
        from chain_metrics.macro import collect_macro_snapshot
        collect_macro_snapshot()
    except Exception as e:
        log.error("Macro snapshot collection failed: %s", e)


def run_chain_metrics():
    """Collect chain adoption metrics from DeFiLlama (daily)."""
    try:
        from chain_metrics.adoption import collect_chain_metrics
        result = collect_chain_metrics()
        log.info("Chain metrics: %d rows stored", result.get("rows_stored", 0))
    except Exception as e:
        log.error("Chain metrics collection failed: %s", e)


def run_weekly_review():
    """Generate and send weekly portfolio review (Sunday 08:00 UTC)."""
    try:
        from reports.weekly_review import generate_weekly_review
        generate_weekly_review()
    except Exception as e:
        log.error("Weekly review failed: %s", e)


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

            # Tier 1 KOL wallets — every 3min
            if _should_run('tier1_kol', INTERVAL_TIER1_KOL):
                threading.Thread(target=run_tier1_kol, daemon=True).start()
                _mark_run('tier1_kol')

            # Tier 2 KOL wallets — every 10min
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

            # Smart money X poll — HIGH tier every 30min
            if _should_run('smart_money_high', INTERVAL_SMART_MONEY_HIGH):
                threading.Thread(target=run_smart_money_poll_high, daemon=True).start()
                _mark_run('smart_money_high')

            # Smart money X poll — MEDIUM tier every 2hr
            if _should_run('smart_money_medium', INTERVAL_SMART_MONEY_MEDIUM):
                threading.Thread(target=run_smart_money_poll_medium, daemon=True).start()
                _mark_run('smart_money_medium')

            # Established scoring + Huoyan + Regime — every 4h
            if _should_run('established', INTERVAL_ESTABLISHED):
                threading.Thread(
                    target=run_health_scoring, args=('established',), daemon=True
                ).start()
                _mark_run('established')

            # Holdings + macro snapshot — every 4h (runs before Huoyan so pulse has fresh data)
            if _should_run('holdings_macro', INTERVAL_HOLDINGS_MACRO):
                run_holdings_macro()  # synchronous so data is ready for pulse
                _mark_run('holdings_macro')

            if _should_run('huoyan', INTERVAL_HUOYAN):
                threading.Thread(target=run_huoyan, daemon=True).start()
                _mark_run('huoyan')

            if _should_run('regime', INTERVAL_REGIME):
                threading.Thread(target=run_regime, daemon=True).start()
                _mark_run('regime')

            # Chain adoption metrics — daily at 06:00 UTC
            if now.hour == 6 and now.minute < 2:
                if _should_run('chain_metrics', INTERVAL_CHAIN_METRICS):
                    threading.Thread(target=run_chain_metrics, daemon=True).start()
                    _mark_run('chain_metrics')

            # Daily moonbag reaper at 03:00 UTC
            if now.hour == 3 and now.minute < 2:
                if _should_run('moonbag_reaper', 82800):  # ~23h cooldown
                    threading.Thread(target=run_moonbag_reaper, daemon=True).start()
                    _mark_run('moonbag_reaper')

            # Weekly portfolio review — Sunday 08:00 UTC
            if now.weekday() == 6 and now.hour == 8 and now.minute < 2:
                if _should_run('weekly_review', 604800):  # ~7d cooldown
                    threading.Thread(target=run_weekly_review, daemon=True).start()
                    _mark_run('weekly_review')

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
