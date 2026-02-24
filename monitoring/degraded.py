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
    "consecutive_failures": 0, "failure_timestamps": [],
})
_degraded_mode = False
_last_run_times: dict[str, datetime] = {}
_lock = threading.Lock()

# Thresholds
CONSECUTIVE_FAILURES_REQUIRED = 3   # 3 consecutive failures from same source
FAILURE_WINDOW_SECONDS = 600        # ... within 10 minutes
SILENCE_TIMEOUT_MINUTES = 15        # alert if scheduled run misses by this much


# ---------------------------------------------------------------------------
# API call tracking
# ---------------------------------------------------------------------------

def record_api_call(source: str, success: bool):
    """Record an API call result for a data source.

    Args:
        source: name of the API (e.g., "dexscreener", "helius", "coingecko", "defillama")
        success: whether the call succeeded
    """
    now = datetime.now(timezone.utc)
    now_ts = time.time()

    with _lock:
        stats = _api_stats[source]
        if success:
            stats["success"] += 1
            stats["last_success"] = now
            # Reset consecutive failure counter on success
            stats["consecutive_failures"] = 0
            stats["failure_timestamps"] = []
        else:
            stats["failure"] += 1
            stats["last_failure"] = now
            stats["consecutive_failures"] += 1
            # Track failure timestamps, prune old ones outside window
            stats["failure_timestamps"].append(now_ts)
            cutoff = now_ts - FAILURE_WINDOW_SECONDS
            stats["failure_timestamps"] = [
                t for t in stats["failure_timestamps"] if t > cutoff
            ]

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
    """Evaluate whether to enter or exit degraded mode.

    Requires 3 consecutive failures from the same source within 10 minutes
    before flagging degraded.  A single transient 429 or timeout won't trigger it.
    """
    global _degraded_mode

    now_ts = time.time()
    cutoff = now_ts - FAILURE_WINDOW_SECONDS

    with _lock:
        degraded_sources = []

        for source, stats in _api_stats.items():
            # Count consecutive failures that occurred within the window
            recent_consecutive = stats["consecutive_failures"]
            recent_in_window = [t for t in stats["failure_timestamps"] if t > cutoff]

            if (recent_consecutive >= CONSECUTIVE_FAILURES_REQUIRED
                    and len(recent_in_window) >= CONSECUTIVE_FAILURES_REQUIRED):
                degraded_sources.append(source)

    if degraded_sources:
        if not _degraded_mode:
            _degraded_mode = True
            src_list = ", ".join(degraded_sources)
            log.critical("ENTERING DEGRADED MODE — sources with %d+ consecutive failures: %s",
                         CONSECUTIVE_FAILURES_REQUIRED, src_list)
            send_message(
                "🔴 <b>DEGRADED MODE ACTIVATED</b>\n\n"
                f"⚠️ {len(degraded_sources)} source(s) with {CONSECUTIVE_FAILURES_REQUIRED}+ "
                f"consecutive failures in {FAILURE_WINDOW_SECONDS // 60}min: {src_list}\n"
                "• No new alerts will be issued\n"
                "• Existing positions monitored only\n"
                "• Check API keys and service status"
            )
    elif _degraded_mode:
        # Exit degraded mode when no source has consecutive failures
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

            consecutive = stats.get("consecutive_failures", 0)
            if consecutive >= CONSECUTIVE_FAILURES_REQUIRED:
                status = "down"
            elif consecutive > 0:
                status = "degraded"
            else:
                status = "healthy"

            sources[name] = {
                "success": stats["success"],
                "failure": stats["failure"],
                "failure_rate": round(failure_rate, 1),
                "consecutive_failures": consecutive,
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
            "consecutive_failures": 0, "failure_timestamps": [],
        })
    log.info("API stats reset")
