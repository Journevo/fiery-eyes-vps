"""Degraded Mode Handler v2 — tracks API success/failure rates.

Key behavior changes from v1:
  - Only alerts if degraded for >60 minutes continuously (not on first trigger)
  - NEVER sends "DEGRADED MODE CLEARED" — just silently resumes
  - Grok exponential backoff: 5min → 10min → 30min on consecutive failures
"""

import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
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
_degraded_since: datetime | None = None  # When degraded mode started
_degraded_alert_sent = False  # Only alert once per degraded episode
_last_run_times: dict[str, datetime] = {}
_lock = threading.Lock()

# Grok backoff state
_grok_consecutive_failures = 0
_grok_backoff_until: float = 0  # timestamp until which to wait

# Thresholds
CONSECUTIVE_FAILURES_REQUIRED = 3
FAILURE_WINDOW_SECONDS = 600  # 10 minutes
SILENCE_TIMEOUT_MINUTES = 15
DEGRADED_ALERT_DELAY_MINUTES = 60  # Only alert after 60min of continuous degraded


# ---------------------------------------------------------------------------
# Grok exponential backoff
# ---------------------------------------------------------------------------

def get_grok_backoff() -> int:
    """Return seconds to wait before next Grok call. 0 = proceed immediately."""
    now = time.time()
    if now < _grok_backoff_until:
        return int(_grok_backoff_until - now)
    return 0


def record_grok_success():
    """Reset Grok backoff on successful call."""
    global _grok_consecutive_failures, _grok_backoff_until
    with _lock:
        _grok_consecutive_failures = 0
        _grok_backoff_until = 0


def record_grok_failure():
    """Increment Grok backoff on failure."""
    global _grok_consecutive_failures, _grok_backoff_until
    with _lock:
        _grok_consecutive_failures += 1
        n = _grok_consecutive_failures

        if n <= 2:
            backoff = 0  # No backoff for first 2 failures
        elif n <= 5:
            backoff = 300  # 5 minutes
        elif n <= 10:
            backoff = 600  # 10 minutes
        else:
            backoff = 1800  # 30 minutes

        if backoff > 0:
            _grok_backoff_until = time.time() + backoff
            log.warning("Grok backoff: %d consecutive failures, waiting %ds",
                        n, backoff)


def reset_grok_backoff():
    """Explicit reset (e.g., on startup)."""
    record_grok_success()


# ---------------------------------------------------------------------------
# API call tracking
# ---------------------------------------------------------------------------

def record_api_call(source: str, success: bool):
    """Record an API call result for a data source."""
    now = datetime.now(timezone.utc)
    now_ts = time.time()

    # Track Grok-specific backoff
    if source == "grok":
        if success:
            record_grok_success()
        else:
            record_grok_failure()

    with _lock:
        stats = _api_stats[source]
        if success:
            stats["success"] += 1
            stats["last_success"] = now
            stats["consecutive_failures"] = 0
            stats["failure_timestamps"] = []
        else:
            stats["failure"] += 1
            stats["last_failure"] = now
            stats["consecutive_failures"] += 1
            stats["failure_timestamps"].append(now_ts)
            cutoff = now_ts - FAILURE_WINDOW_SECONDS
            stats["failure_timestamps"] = [
                t for t in stats["failure_timestamps"] if t > cutoff
            ]

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

    Enter: 3+ consecutive failures from same source within 10 minutes.
    Alert: only after 60 minutes of continuous degraded mode.
    Exit: silently — no Telegram message.
    """
    global _degraded_mode, _degraded_since, _degraded_alert_sent

    now_ts = time.time()
    cutoff = now_ts - FAILURE_WINDOW_SECONDS

    with _lock:
        degraded_sources = []
        for source, stats in _api_stats.items():
            recent_consecutive = stats["consecutive_failures"]
            recent_in_window = [t for t in stats["failure_timestamps"] if t > cutoff]
            if (recent_consecutive >= CONSECUTIVE_FAILURES_REQUIRED
                    and len(recent_in_window) >= CONSECUTIVE_FAILURES_REQUIRED):
                degraded_sources.append(source)

    if degraded_sources:
        if not _degraded_mode:
            _degraded_mode = True
            _degraded_since = datetime.now(timezone.utc)
            _degraded_alert_sent = False
            src_list = ", ".join(degraded_sources)
            log.warning("ENTERING DEGRADED MODE — sources: %s (alert in %dmin if persists)",
                        src_list, DEGRADED_ALERT_DELAY_MINUTES)
    elif _degraded_mode:
        # Exit silently — no Telegram message
        _degraded_mode = False
        _degraded_since = None
        _degraded_alert_sent = False
        log.info("EXITING DEGRADED MODE — sources recovering (no alert sent)")


def check_degraded_alert():
    """Call periodically (e.g., every 5min). Sends alert only if degraded >60min."""
    global _degraded_alert_sent

    if not _degraded_mode or _degraded_alert_sent or _degraded_since is None:
        return

    now = datetime.now(timezone.utc)
    elapsed = (now - _degraded_since).total_seconds() / 60

    if elapsed >= DEGRADED_ALERT_DELAY_MINUTES:
        _degraded_alert_sent = True
        # Find which sources are degraded
        now_ts = time.time()
        cutoff = now_ts - FAILURE_WINDOW_SECONDS
        degraded_sources = []
        with _lock:
            for source, stats in _api_stats.items():
                if stats["consecutive_failures"] >= CONSECUTIVE_FAILURES_REQUIRED:
                    degraded_sources.append(source)

        src_list = ", ".join(degraded_sources) if degraded_sources else "unknown"
        log.critical("DEGRADED MODE persisted for %d minutes — alerting operator", int(elapsed))
        send_message(
            f"🔴 <b>DEGRADED MODE</b> ({int(elapsed)}min)\n\n"
            f"Sources failing: {src_list}\n"
            f"Since: {_degraded_since.strftime('%H:%M UTC')}\n"
            "Check API keys and service status."
        )


def is_degraded() -> bool:
    """Check if system is in degraded mode."""
    return _degraded_mode


# ---------------------------------------------------------------------------
# Silence-is-failure detection
# ---------------------------------------------------------------------------

def check_silence_failures(expected_tasks: dict[str, int]):
    """Check if any scheduled tasks have missed their expected run time."""
    now = datetime.now(timezone.utc)
    alerts = []

    with _lock:
        for task_name, interval_min in expected_tasks.items():
            last_run = _last_run_times.get(task_name)
            if last_run is None:
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
            lines.append(f"🔴 <b>{alert['task']}</b>: {alert['minutes_overdue']}min overdue")
            lines.append(f"   Last run: {alert['last_run']}")
        log.error("Silence-is-failure: %d tasks overdue", len(alerts))
        send_message("\n".join(lines))

    return alerts


# ---------------------------------------------------------------------------
# Health summary
# ---------------------------------------------------------------------------

def get_health_summary() -> dict:
    """Get complete health summary for all tracked APIs."""
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

        last_runs = {name: ts.isoformat() for name, ts in _last_run_times.items()}

    return {
        "degraded_mode": _degraded_mode,
        "degraded_since": _degraded_since.isoformat() if _degraded_since else None,
        "grok_backoff_seconds": get_grok_backoff(),
        "grok_consecutive_failures": _grok_consecutive_failures,
        "sources": sources,
        "last_runs": last_runs,
    }


def reset_stats():
    """Reset all API stats."""
    global _api_stats, _grok_consecutive_failures, _grok_backoff_until
    with _lock:
        _api_stats = defaultdict(lambda: {
            "success": 0, "failure": 0, "last_success": None, "last_failure": None,
            "consecutive_failures": 0, "failure_timestamps": [],
        })
        _grok_consecutive_failures = 0
        _grok_backoff_until = 0
    log.info("API stats reset")
