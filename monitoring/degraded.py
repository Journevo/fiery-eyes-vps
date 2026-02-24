"""Degraded Mode Handler — tracks API success/failure rates,
   enters Degraded Mode when >30% of data sources failing,
   silence-is-failure alerting if scheduled runs don't complete.

Degraded Mode:
  - No new alerts issued
  - Existing positions monitored only
  - Operator alerted immediately
"""

import time
import threading
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from config import get_logger
from telegram_bot.alerts import send_message

log = get_logger("monitoring.degraded")

# ---------------------------------------------------------------------------
# State tracking (in-process)
# ---------------------------------------------------------------------------

_api_stats: dict[str, dict] = defaultdict(lambda: {
    "success": 0, "failure": 0, "last_success": None, "last_failure": None,
})
_degraded_mode = False
_last_run_times: dict[str, datetime] = {}
_lock = threading.Lock()

# Thresholds
FAILURE_THRESHOLD_PCT = 30  # enter degraded if >30% failure rate
MIN_CALLS_FOR_EVAL = 10     # need at least this many calls to evaluate
SILENCE_TIMEOUT_MINUTES = 15  # alert if scheduled run misses by this much


# ---------------------------------------------------------------------------
# API call tracking
# ---------------------------------------------------------------------------

def record_api_call(source: str, success: bool):
    """Record an API call result for a data source.

    Args:
        source: name of the API (e.g., "dexscreener", "helius", "coingecko", "defillama")
        success: whether the call succeeded
    """
    with _lock:
        if success:
            _api_stats[source]["success"] += 1
            _api_stats[source]["last_success"] = datetime.now(timezone.utc)
        else:
            _api_stats[source]["failure"] += 1
            _api_stats[source]["last_failure"] = datetime.now(timezone.utc)

    # Check if we should enter/exit degraded mode
    _evaluate_degraded_mode()


def record_run_completion(task_name: str):
    """Record that a scheduled task completed successfully."""
    with _lock:
        _last_run_times[task_name] = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Degraded mode evaluation
# ---------------------------------------------------------------------------

def _evaluate_degraded_mode():
    """Evaluate whether to enter or exit degraded mode."""
    global _degraded_mode

    with _lock:
        failing_sources = 0
        total_sources = 0

        for source, stats in _api_stats.items():
            total_calls = stats["success"] + stats["failure"]
            if total_calls < MIN_CALLS_FOR_EVAL:
                continue

            total_sources += 1
            failure_rate = stats["failure"] / total_calls * 100

            if failure_rate > FAILURE_THRESHOLD_PCT:
                failing_sources += 1

        if total_sources == 0:
            return

        overall_failure_pct = (failing_sources / total_sources) * 100

    was_degraded = _degraded_mode

    if overall_failure_pct > FAILURE_THRESHOLD_PCT:
        if not _degraded_mode:
            _degraded_mode = True
            log.critical("ENTERING DEGRADED MODE — %.0f%% of sources failing", overall_failure_pct)
            send_message(
                "🔴 <b>DEGRADED MODE ACTIVATED</b>\n\n"
                f"⚠️ {failing_sources}/{total_sources} data sources failing "
                f"({overall_failure_pct:.0f}%)\n"
                "• No new alerts will be issued\n"
                "• Existing positions monitored only\n"
                "• Check API keys and service status"
            )
    elif _degraded_mode and overall_failure_pct < FAILURE_THRESHOLD_PCT / 2:
        # Exit degraded mode when failure rate drops below half the threshold
        _degraded_mode = False
        log.info("EXITING DEGRADED MODE — sources recovering")
        send_message(
            "🟢 <b>DEGRADED MODE CLEARED</b>\n\n"
            "Data sources recovered. Normal operations resumed."
        )


def is_degraded() -> bool:
    """Check if system is in degraded mode."""
    return _degraded_mode


# ---------------------------------------------------------------------------
# Silence-is-failure detection
# ---------------------------------------------------------------------------

def check_silence_failures(expected_tasks: dict[str, int]):
    """Check if any scheduled tasks have missed their expected run time.

    Args:
        expected_tasks: {task_name: expected_interval_minutes}
    """
    now = datetime.now(timezone.utc)
    alerts = []

    with _lock:
        for task_name, interval_min in expected_tasks.items():
            last_run = _last_run_times.get(task_name)

            if last_run is None:
                # Task has never run — only alert if we've been up long enough
                continue

            elapsed = (now - last_run).total_seconds() / 60
            max_allowed = interval_min + SILENCE_TIMEOUT_MINUTES

            if elapsed > max_allowed:
                alerts.append({
                    "task": task_name,
                    "last_run": last_run.isoformat(),
                    "minutes_overdue": int(elapsed - interval_min),
                })

    if alerts:
        lines = ["⚠️ <b>SILENCE-IS-FAILURE ALERT</b>", ""]
        for alert in alerts:
            lines.append(
                f"🔴 <b>{alert['task']}</b>: {alert['minutes_overdue']}min overdue"
            )
            lines.append(f"   Last run: {alert['last_run']}")

        log.error("Silence-is-failure: %d tasks overdue", len(alerts))
        send_message("\n".join(lines))

    return alerts


# ---------------------------------------------------------------------------
# Health summary
# ---------------------------------------------------------------------------

def get_health_summary() -> dict:
    """Get complete health summary for all tracked APIs.

    Returns:
        {
            "degraded_mode": bool,
            "sources": {
                "name": {
                    "success": int,
                    "failure": int,
                    "failure_rate": float,
                    "last_success": str|None,
                    "last_failure": str|None,
                    "status": "healthy"|"degraded"|"down",
                }
            },
            "last_runs": {task_name: str},
        }
    """
    with _lock:
        sources = {}
        for name, stats in _api_stats.items():
            total = stats["success"] + stats["failure"]
            failure_rate = (stats["failure"] / total * 100) if total > 0 else 0

            if failure_rate > 50:
                status = "down"
            elif failure_rate > FAILURE_THRESHOLD_PCT:
                status = "degraded"
            else:
                status = "healthy"

            sources[name] = {
                "success": stats["success"],
                "failure": stats["failure"],
                "failure_rate": round(failure_rate, 1),
                "last_success": stats["last_success"].isoformat() if stats["last_success"] else None,
                "last_failure": stats["last_failure"].isoformat() if stats["last_failure"] else None,
                "status": status,
            }

        last_runs = {
            name: ts.isoformat()
            for name, ts in _last_run_times.items()
        }

    return {
        "degraded_mode": _degraded_mode,
        "sources": sources,
        "last_runs": last_runs,
    }


def reset_stats():
    """Reset all API stats (e.g., daily reset to prevent stale counters)."""
    global _api_stats
    with _lock:
        _api_stats = defaultdict(lambda: {
            "success": 0, "failure": 0, "last_success": None, "last_failure": None,
        })
    log.info("API stats reset")
