"""Health & metrics endpoints — Flask app on port 8080.

Endpoints:
  GET /health  — DB status, last scan, API status, regime, degraded mode, uptime
  GET /metrics — total tokens, pass rate, alerts today, scan cycle time
  GET /        — app metadata
"""

import os
import time
from datetime import datetime, timezone
from flask import Flask, jsonify
from db.connection import is_healthy, execute_one, execute
from config import get_logger

log = get_logger("health")
app = Flask(__name__)

# Track state
_last_gate_run: str | None = None
_start_time = time.time()


def set_last_gate_run(ts: str):
    global _last_gate_run
    _last_gate_run = ts


@app.route("/health")
def health():
    db_ok = is_healthy()

    # Last gate run
    last_run = _last_gate_run
    if not last_run:
        try:
            row = execute_one(
                "SELECT MAX(timestamp) FROM alerts WHERE type IN ('gate_pass', 'gate_fail')"
            )
            if row and row[0]:
                last_run = row[0].isoformat()
        except Exception:
            pass

    # Regime multiplier
    regime = None
    try:
        row = execute_one(
            "SELECT regime_multiplier FROM regime_snapshots WHERE date = CURRENT_DATE"
        )
        if row:
            regime = row[0]
    except Exception:
        pass

    # Degraded mode
    degraded = False
    api_status = {}
    try:
        from monitoring.degraded import is_degraded, get_health_summary
        degraded = is_degraded()
        summary = get_health_summary()
        api_status = {name: info["status"]
                      for name, info in summary.get("sources", {}).items()}
    except Exception:
        pass

    status = "degraded" if (not db_ok or degraded) else "healthy"
    uptime_seconds = int(time.time() - _start_time)

    return jsonify({
        "status": status,
        "database": "connected" if db_ok else "disconnected",
        "degraded_mode": degraded,
        "last_gate_run": last_run,
        "regime_multiplier": regime,
        "api_status": api_status,
        "uptime_seconds": uptime_seconds,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }), 200 if status == "healthy" else 503


@app.route("/metrics")
def metrics():
    """Operational metrics endpoint."""
    data = {
        "tokens_tracked": 0,
        "tokens_gate_pass": 0,
        "pass_rate": 0,
        "alerts_today": 0,
        "alerts_today_pass": 0,
        "alerts_today_fail": 0,
        "open_positions": 0,
        "scan_cycle_count": 0,
    }

    try:
        # Token counts
        row = execute_one("SELECT COUNT(*) FROM tokens")
        if row:
            data["tokens_tracked"] = row[0]

        row = execute_one("SELECT COUNT(*) FROM tokens WHERE quality_gate_pass = TRUE")
        if row:
            data["tokens_gate_pass"] = row[0]

        if data["tokens_tracked"] > 0:
            data["pass_rate"] = round(
                data["tokens_gate_pass"] / data["tokens_tracked"] * 100, 1
            )

        # Alerts today
        row = execute_one(
            """SELECT
                 COUNT(*) as total,
                 COUNT(*) FILTER (WHERE type = 'gate_pass') as passes,
                 COUNT(*) FILTER (WHERE type = 'gate_fail') as fails
               FROM alerts WHERE timestamp >= CURRENT_DATE"""
        )
        if row:
            data["alerts_today"] = row[0]
            data["alerts_today_pass"] = row[1]
            data["alerts_today_fail"] = row[2]

        # Open positions
        row = execute_one(
            "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        )
        if row:
            data["open_positions"] = row[0]

        # Scores today
        row = execute_one(
            "SELECT COUNT(*) FROM scores_daily WHERE date = CURRENT_DATE"
        )
        if row:
            data["scan_cycle_count"] = row[0]

    except Exception as e:
        log.error("Metrics query error: %s", e)

    # API health details
    try:
        from monitoring.degraded import get_health_summary
        summary = get_health_summary()
        data["api_health"] = summary.get("sources", {})
        data["last_runs"] = summary.get("last_runs", {})
    except Exception:
        data["api_health"] = {}
        data["last_runs"] = {}

    return jsonify(data)


@app.route("/")
def root():
    return jsonify({
        "name": "Fiery Eyes",
        "version": "2.0.0",
        "docs": "/health",
        "metrics": "/metrics",
    })


def run_health_server():
    port = int(os.getenv("HEALTH_PORT", "8080"))
    log.info("Starting health server on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=False)
