"""Data Health Monitor — tracks which APIs are live/stale/down.

Feeds into Data Confidence Score.

Tracks:
- DexScreener: last successful call, avg latency, error rate
- Helius: last successful call, credits remaining
- Grok: last successful call (when connected)
- CoinGecko: last successful call

Rules:
- >3 of 5 signals must be available for auto-execution
- <3 signals -> alert-only mode
- Display: "Health: 72/100 (social stale, 4/5 signals, 80% conf)"
"""

import time
import threading
from collections import defaultdict
from datetime import datetime, timezone
from config import get_logger
from monitoring.degraded import record_api_call, get_health_summary as get_degraded_summary

log = get_logger("monitoring.data_health")

# API tracking
_api_latency: dict[str, list[float]] = defaultdict(list)
_api_last_call: dict[str, float] = {}
_lock = threading.Lock()

# Max latency samples to keep per API
MAX_LATENCY_SAMPLES = 100

# API sources we track
TRACKED_APIS = ['dexscreener', 'helius', 'coingecko', 'grok', 'coinglass']

# Signal-to-API mapping
SIGNAL_API_MAP = {
    'volume': 'dexscreener',
    'price': 'dexscreener',
    'kol': 'helius',
    'social': 'grok',
    'holders': 'helius',
}


def record_api_latency(source: str, latency_ms: float):
    """Record API call latency for a source."""
    with _lock:
        samples = _api_latency[source]
        samples.append(latency_ms)
        if len(samples) > MAX_LATENCY_SAMPLES:
            _api_latency[source] = samples[-MAX_LATENCY_SAMPLES:]
        _api_last_call[source] = time.time()


def get_api_status() -> dict:
    """Get comprehensive API health status.

    Returns:
        {
            'apis': {
                'name': {
                    'status': 'live'|'stale'|'down',
                    'avg_latency_ms': float,
                    'last_call_ago_sec': float,
                    'error_rate_pct': float,
                }
            },
            'signals_available': int,
            'total_signals': int,
            'auto_execution_allowed': bool,
            'mode': 'auto'|'alert_only'|'degraded',
        }
    """
    degraded = get_degraded_summary()
    degraded_sources = degraded.get('sources', {})

    apis = {}
    signals_available = 0

    with _lock:
        for api_name in TRACKED_APIS:
            latencies = _api_latency.get(api_name, [])
            last_call = _api_last_call.get(api_name)
            degraded_info = degraded_sources.get(api_name, {})

            avg_latency = sum(latencies) / len(latencies) if latencies else 0
            last_call_ago = (time.time() - last_call) if last_call else float('inf')

            error_rate = degraded_info.get('failure_rate', 0)
            degraded_status = degraded_info.get('status', 'healthy')

            # Determine status
            if degraded_status == 'down' or error_rate > 50:
                status = 'down'
            elif last_call_ago > 1800:  # >30min since last call
                status = 'stale'
            elif degraded_status == 'degraded' or error_rate > 20:
                status = 'stale'
            else:
                status = 'live'

            apis[api_name] = {
                'status': status,
                'avg_latency_ms': round(avg_latency, 1),
                'last_call_ago_sec': round(last_call_ago, 0) if last_call else None,
                'error_rate_pct': round(error_rate, 1),
            }

    # Count available signals
    for signal, api in SIGNAL_API_MAP.items():
        api_info = apis.get(api, {})
        if api_info.get('status') in ('live', 'stale'):
            signals_available += 1

    total_signals = len(SIGNAL_API_MAP)
    auto_allowed = signals_available >= 3

    if degraded.get('degraded_mode'):
        mode = 'degraded'
    elif auto_allowed:
        mode = 'auto'
    else:
        mode = 'alert_only'

    return {
        'apis': apis,
        'signals_available': signals_available,
        'total_signals': total_signals,
        'auto_execution_allowed': auto_allowed,
        'mode': mode,
    }


def get_data_health_display() -> str:
    """Get a one-line data health display string.

    Example: "4/5 signals | DexScreener: live | Helius: live | Grok: stale"
    """
    status = get_api_status()
    parts = [f"{status['signals_available']}/{status['total_signals']} signals"]

    for api_name, info in status['apis'].items():
        icon = {'live': '🟢', 'stale': '🟡', 'down': '🔴'}.get(info['status'], '⚪')
        parts.append(f"{api_name}: {icon}")

    return " | ".join(parts)


def check_auto_execution_safety() -> tuple[bool, str]:
    """Check if auto-execution is safe based on data health.

    Returns: (is_safe, reason)
    """
    status = get_api_status()

    if status['mode'] == 'degraded':
        return False, "System in degraded mode"

    if not status['auto_execution_allowed']:
        return False, f"Only {status['signals_available']}/{status['total_signals']} signals available (need 3+)"

    # Check if critical APIs are down
    dex_status = status['apis'].get('dexscreener', {}).get('status')
    helius_status = status['apis'].get('helius', {}).get('status')

    if dex_status == 'down':
        return False, "DexScreener is down — no price/volume data"

    if helius_status == 'down':
        return False, "Helius is down — no wallet tracking"

    return True, "All systems operational"
