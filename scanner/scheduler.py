"""Auto-scanner — discovers trending tokens, runs Quality Gate,
   collects snapshots, scores through engines, manages regime,
   runs exit checks, nightly report, performance tracking.

   Includes three-track discovery:
   - Momentum: DexScreener trending (every 30min)
   - Adoption: DeFiLlama + watchlist (daily 05:30 UTC)
   - Infrastructure: CoinGecko watchlist (daily 05:00 UTC)
   - Lifecycle checks: daily
   - Watch recheck: every 30min
"""

import time
import threading
import schedule

from config import SCAN_INTERVAL_MINUTES, get_logger
from scanner.discover import discover_new_tokens
from quality_gate.gate import run_gate
from telegram_bot.alerts import send_gate_result, send_scored_alert, send_message, send_daily_summary
from health import set_last_gate_run
from collectors.snapshots import collect_all_snapshots, collect_momentum_snapshots
from collectors.cluster import run_cluster_detection_all
from engines.composite import score_all_tokens
from engines.convergence import scan_all_convergences, send_convergence_alerts
from regime.multiplier import calculate_regime
from risk.exits import check_all_exits
from reports.nightly import generate_nightly_report
from reports.performance import update_performance_prices
from monitoring.degraded import is_degraded, record_run_completion, check_silence_failures

log = get_logger("scanner")

# Expected task intervals for silence-is-failure detection
EXPECTED_TASKS = {
    "scan_cycle": SCAN_INTERVAL_MINUTES,
    "daily_collection": 1440,   # 24 hours
    "momentum_snapshot": 240,   # 4 hours
    "regime_calculation": 1440,
    "nightly_report": 1440,
    "adoption_discovery": 1440,
    "infra_discovery": 1440,
    "lifecycle_check": 1440,
    "watch_recheck": SCAN_INTERVAL_MINUTES,
    "smart_money_poll_high": 30,   # 30-min HIGH tier poll
    "smart_money_poll_medium": 120, # 2-hr MEDIUM tier poll
    "gmgn_scrape": 10080,          # weekly (Sunday 00:00 UTC)
    "smart_money_radar": 60,       # hourly convergence check
}


def _scan_cycle():
    """Single scan cycle: discover → gate → snapshot → score → alert."""
    log.info("=== Starting scan cycle ===")

    if is_degraded():
        log.warning("System in degraded mode — skipping new alerts")
        record_run_completion("scan_cycle")
        return

    try:
        new_mints = discover_new_tokens()
    except Exception as e:
        log.error("Discovery failed: %s", e)
        return

    if not new_mints:
        log.info("No new tokens found — skipping cycle")
        record_run_completion("scan_cycle")
        return

    passed = 0
    watching = 0
    failed = 0

    for mint in new_mints:
        try:
            log.info("Scanning %s (%d/%d)", mint, passed + watching + failed + 1, len(new_mints))
            result = run_gate(mint, category="meme")
            set_last_gate_run(result["timestamp"])

            gate_status = result.get("gate_status", "rejected")
            if gate_status == "passed":
                passed += 1
                send_gate_result(result)
            elif gate_status == "watching":
                watching += 1
                send_gate_result(result)
            else:
                failed += 1

        except Exception as e:
            log.error("Error scanning %s: %s", mint, e)
            failed += 1

    summary = (
        f"📊 <b>Scan Summary</b>\n"
        f"Scanned {len(new_mints)} tokens: "
        f"{passed} passed, {watching} watching, {failed} rejected"
    )
    log.info("Cycle done: %d scanned, %d passed, %d watching, %d rejected",
             len(new_mints), passed, watching, failed)
    send_message(summary)
    record_run_completion("scan_cycle")


def _recheck_watching():
    """Re-check tokens in WATCHING state — promote or expire."""
    try:
        from quality_gate.gate import recheck_watching_tokens
        results = recheck_watching_tokens()
        promoted = [r for r in results if r.get("gate_status") == "passed"]
        expired = [r for r in results if r.get("gate_status") == "rejected"]

        if promoted:
            for r in promoted:
                send_message(f"✅ <b>PROMOTED</b>: <code>{r.get('mint', '?')[:12]}...</code> — all checks now pass")
            log.info("Promoted %d watching tokens to PASS", len(promoted))
        if expired:
            log.info("Expired %d watching tokens (>48h)", len(expired))

    except Exception as e:
        log.error("Watch recheck failed: %s", e)
    record_run_completion("watch_recheck")


def _daily_collection():
    """Daily: regime → snapshots → clusters → scores → exits → convergence → summary."""
    log.info("=== Starting daily collection & scoring ===")

    # 1. Calculate regime first (scores depend on it)
    try:
        regime = calculate_regime()
        log.info("Regime multiplier: %.3f", regime["regime_multiplier"])
    except Exception as e:
        log.error("Regime calculation failed: %s", e)
        regime = {"regime_multiplier": 1.0}
    record_run_completion("regime_calculation")

    # 2. Collect snapshots
    try:
        collect_all_snapshots()
    except Exception as e:
        log.error("Snapshot collection failed: %s", e)

    # 3. Cluster detection
    try:
        run_cluster_detection_all()
    except Exception as e:
        log.error("Cluster detection failed: %s", e)

    # 4. Score all tokens
    try:
        results = score_all_tokens()
        send_daily_summary(results)
    except Exception as e:
        log.error("Scoring failed: %s", e)
        results = []

    # 5. Check exit triggers
    try:
        exits = check_all_exits(regime["regime_multiplier"])
        if exits:
            log.info("Exit triggers found for %d tokens", len(exits))
    except Exception as e:
        log.error("Exit check failed: %s", e)

    # 6. Convergence scan
    try:
        convergences = scan_all_convergences()
        if convergences:
            send_convergence_alerts(convergences)
    except Exception as e:
        log.error("Convergence scan failed: %s", e)

    # 7. Update performance tracking
    try:
        update_performance_prices()
    except Exception as e:
        log.error("Performance update failed: %s", e)

    record_run_completion("daily_collection")
    log.info("=== Daily collection & scoring complete ===")


def _momentum_snapshot():
    """4-hourly: collect snapshots for momentum candidates."""
    try:
        collect_momentum_snapshots()
    except Exception as e:
        log.error("Momentum snapshot failed: %s", e)
    record_run_completion("momentum_snapshot")


def _nightly_report():
    """Daily 6AM: generate and send nightly strategist report."""
    try:
        generate_nightly_report()
    except Exception as e:
        log.error("Nightly report failed: %s", e)
    record_run_completion("nightly_report")


def _adoption_discovery():
    """Daily 05:30: run adoption track discovery."""
    log.info("=== Running adoption track discovery ===")
    try:
        from scanner.adoption_discover import run_adoption_discovery
        run_adoption_discovery()
    except Exception as e:
        log.error("Adoption discovery failed: %s", e)
    record_run_completion("adoption_discovery")


def _infra_discovery():
    """Daily 05:00: run infrastructure track discovery."""
    log.info("=== Running infrastructure track discovery ===")
    try:
        from scanner.infra_discover import run_infrastructure_discovery
        run_infrastructure_discovery()
    except Exception as e:
        log.error("Infrastructure discovery failed: %s", e)
    record_run_completion("infra_discovery")


def _lifecycle_check():
    """Daily: check lifecycle stages and promotion candidates."""
    log.info("=== Running lifecycle stage checks ===")
    try:
        from engines.lifecycle import check_promotion_candidates
        candidates = check_promotion_candidates()
        if candidates:
            for c in candidates:
                send_message(
                    f"🎓 <b>PROMOTION CANDIDATE</b>\n"
                    f"<code>{c.get('symbol', '?')}</code>: "
                    f"ready for Stage {c.get('new_stage', '?')}\n"
                    f"Reason: {c.get('reason', 'N/A')}"
                )
            log.info("Found %d promotion candidates", len(candidates))
    except Exception as e:
        log.error("Lifecycle check failed: %s", e)
    record_run_completion("lifecycle_check")


def _smart_money_poll_high():
    """Poll smart money HIGH tier X accounts via Grok API."""
    log.info("=== Smart money HIGH tier X poll ===")
    try:
        from social.grok_poller import run_smart_money_poll_high
        result = run_smart_money_poll_high()
        total = result.get("total_signals", 0)
        if total > 0:
            log.info("Smart money HIGH tier poll: %d new signals", total)
        else:
            log.debug("Smart money HIGH tier poll: no new signals")
    except Exception as e:
        log.error("Smart money HIGH tier poll failed: %s", e)
    record_run_completion("smart_money_poll_high")


def _smart_money_poll_medium():
    """Poll smart money MEDIUM tier X accounts via Grok API."""
    log.info("=== Smart money MEDIUM tier X poll ===")
    try:
        from social.grok_poller import run_smart_money_poll_medium
        result = run_smart_money_poll_medium()
        total = result.get("total_signals", 0)
        if total > 0:
            log.info("Smart money MEDIUM tier poll: %d new signals", total)
        else:
            log.debug("Smart money MEDIUM tier poll: no new signals")
    except Exception as e:
        log.error("Smart money MEDIUM tier poll failed: %s", e)
    record_run_completion("smart_money_poll_medium")


def _gmgn_weekly_scrape():
    """Weekly Sunday 00:00: scrape GMGN smart money leaderboard."""
    log.info("=== GMGN Weekly Wallet Scrape ===")
    try:
        from wallets.gmgn_scraper import run_gmgn_scrape
        result = run_gmgn_scrape()
        log.info("GMGN scrape done: %d passed filter, +%d new, -%d removed",
                 result.get("passed_filter", 0),
                 result.get("new_wallets", 0),
                 result.get("removed", 0))
    except Exception as e:
        log.error("GMGN weekly scrape failed: %s", e)
    record_run_completion("gmgn_scrape")


def _smart_money_radar():
    """Hourly: check for cross-source smart money convergence."""
    try:
        from wallets.convergence_detector import run_convergence_check
        result = run_convergence_check()
        convergences = result.get("convergences", [])
        if convergences:
            log.info("Smart money radar: %d convergences detected", len(convergences))
    except Exception as e:
        log.error("Smart money radar failed: %s", e)
    record_run_completion("smart_money_radar")


def _silence_check():
    """Periodic: check for missed scheduled runs."""
    try:
        check_silence_failures(EXPECTED_TASKS)
    except Exception as e:
        log.error("Silence check failed: %s", e)


def run_scanner():
    """Start the scheduler loop — runs first cycle immediately, then on schedule."""
    log.info("Auto-scanner starting (interval: %d min)", SCAN_INTERVAL_MINUTES)
    send_message(f"🔥 <b>Fiery Eyes v2.0</b> auto-scanner started (every {SCAN_INTERVAL_MINUTES}min)")

    # Start Telegram bot command polling in background
    try:
        from telegram_bot.commands import start_bot_polling
        bot_thread = threading.Thread(target=start_bot_polling, daemon=True)
        bot_thread.start()
        log.info("Telegram bot command polling started")
    except Exception as e:
        log.error("Failed to start bot polling: %s", e)

    # Run first smart money poll immediately (both tiers)
    _smart_money_poll_high()
    _smart_money_poll_medium()

    # Run first scan cycle immediately
    _scan_cycle()

    # Run first daily collection immediately
    _daily_collection()

    # Schedule recurring runs
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(_scan_cycle)
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(_recheck_watching)
    schedule.every().day.at("05:00").do(_infra_discovery)
    schedule.every().day.at("05:00").do(_daily_collection)
    schedule.every().day.at("05:30").do(_adoption_discovery)
    schedule.every(4).hours.do(_momentum_snapshot)
    schedule.every().day.at("06:00").do(_nightly_report)
    schedule.every().day.at("06:30").do(_lifecycle_check)
    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(_smart_money_poll_high)
    schedule.every(2).hours.do(_smart_money_poll_medium)
    schedule.every().sunday.at("00:00").do(_gmgn_weekly_scrape)
    schedule.every(1).hours.do(_smart_money_radar)
    schedule.every(5).minutes.do(_silence_check)

    while True:
        schedule.run_pending()
        time.sleep(10)
