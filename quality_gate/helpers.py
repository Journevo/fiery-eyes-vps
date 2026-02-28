"""Shared helpers: retry logic, HTTP sessions, Helius rate limiting + cache."""

import hashlib
import json
import time
import threading
import requests
from config import get_logger

log = get_logger("helpers")

# ---------------------------------------------------------------------------
# Helius token-bucket rate limiter (5 RPS, conservative for 10 RPS limit)
# ---------------------------------------------------------------------------
_helius_max = 5
_helius_tokens = 0.0  # start empty — ramp up gradually, no burst on startup
_helius_last_refill = time.monotonic()
_helius_lock = threading.Lock()


def _helius_wait():
    """Block until a Helius rate-limit token is available."""
    global _helius_tokens, _helius_last_refill
    waited = False
    while True:
        with _helius_lock:
            now = time.monotonic()
            elapsed = now - _helius_last_refill
            _helius_tokens = min(_helius_max, _helius_tokens + elapsed * _helius_max)
            _helius_last_refill = now
            if _helius_tokens >= 1.0:
                _helius_tokens -= 1.0
                if waited:
                    log.debug("Helius rate limiter: resumed after wait")
                return
        if not waited:
            log.debug("Helius rate limiter: waiting for token")
            waited = True
        time.sleep(0.05)


def _helius_drain():
    """Drain all tokens after a 429 — force slowdown."""
    global _helius_tokens
    with _helius_lock:
        _helius_tokens = 0.0


# ---------------------------------------------------------------------------
# Helius circuit breaker — block ALL calls after 429 for a cooldown period
# ---------------------------------------------------------------------------
_helius_circuit_open_until = 0.0  # monotonic time when circuit closes
_circuit_lock = threading.Lock()
_CIRCUIT_COOLDOWN = 60  # seconds to block after a 429


def _helius_trip_circuit():
    """Open the circuit breaker — block all Helius calls for cooldown period."""
    global _helius_circuit_open_until
    with _circuit_lock:
        _helius_circuit_open_until = time.monotonic() + _CIRCUIT_COOLDOWN
        log.warning("Helius circuit breaker OPEN — blocking all calls for %ds", _CIRCUIT_COOLDOWN)


def _helius_circuit_ok() -> bool:
    """Return True if Helius calls are allowed (circuit closed)."""
    with _circuit_lock:
        return time.monotonic() >= _helius_circuit_open_until


# ---------------------------------------------------------------------------
# Helius response cache (per-method TTL)
# ---------------------------------------------------------------------------
_helius_cache: dict[str, tuple[dict, float]] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 500

_HELIUS_CACHE_TTL: dict[str, int] = {
    'getTransaction': 1800,
    'getTokenLargestAccounts': 300,
    'getTokenSupply': 300,
    'getAsset': 300,
    'getSignaturesForAddress': 60,
    'getBalance': 120,
    'getTokenAccountsByOwner': 120,
}
_HELIUS_DEFAULT_TTL = 300


def _cache_key(method: str, params) -> str:
    raw = method + json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        entry = _helius_cache.get(key)
        if entry is None:
            return None
        value, expires = entry
        if time.monotonic() > expires:
            del _helius_cache[key]
            return None
        return value


def _cache_set(key: str, value: dict, ttl: int):
    with _cache_lock:
        # Evict expired entries if at capacity
        if len(_helius_cache) >= _CACHE_MAX:
            now = time.monotonic()
            expired = [k for k, (_, exp) in _helius_cache.items() if now > exp]
            for k in expired:
                del _helius_cache[k]
        _helius_cache[key] = (value, time.monotonic() + ttl)


# ---------------------------------------------------------------------------
# Core HTTP helpers
# ---------------------------------------------------------------------------

def retry_request(method: str, url: str, max_retries=3, backoff=1.0, **kwargs) -> requests.Response:
    """HTTP request with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, timeout=15, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            wait = backoff * (2 ** attempt)
            log.warning("Request %s %s attempt %d failed: %s — retrying in %.1fs",
                        method, url[:80], attempt + 1, e, wait)
            if attempt == max_retries - 1:
                raise
            time.sleep(wait)


def get_json(url: str, **kwargs) -> dict:
    """GET request returning parsed JSON with retry."""
    resp = retry_request("GET", url, **kwargs)
    return resp.json()


def post_json(url: str, payload: dict, **kwargs) -> dict:
    """POST request returning parsed JSON with retry.

    When the target is Helius RPC, automatically applies rate limiting
    (8 RPS token bucket) and per-method response caching.
    """
    # Lazy import to avoid circular dependency with config.py
    from config import HELIUS_RPC_URL

    if not url.startswith(HELIUS_RPC_URL):
        resp = retry_request("POST", url, json=payload, **kwargs)
        return resp.json()

    # --- Helius path: circuit breaker → cache → rate limit → request → cache ---
    method = payload.get("method", "")
    params = payload.get("params", [])

    # Circuit breaker: fail fast if Helius is rate-limited globally
    if not _helius_circuit_ok():
        raise requests.RequestException(
            f"Helius circuit breaker open — skipping {method}")

    key = _cache_key(method, params)
    cached = _cache_get(key)
    if cached is not None:
        log.debug("Helius cache hit: %s", method)
        return cached

    max_attempts = 3
    for attempt in range(max_attempts):
        _helius_wait()
        try:
            resp = requests.post(url, json=payload, timeout=15, **kwargs)
            if resp.status_code == 429:
                _helius_drain()
                _helius_trip_circuit()
                raise requests.RequestException(
                    f"Helius 429 on {method} — circuit breaker tripped")
            resp.raise_for_status()
            data = resp.json()
            if "result" in data:
                ttl = _HELIUS_CACHE_TTL.get(method, _HELIUS_DEFAULT_TTL)
                _cache_set(key, data, ttl)
            return data
        except requests.ConnectionError as e:
            if attempt == max_attempts - 1:
                raise
            wait = 1.0 * (2 ** attempt)
            log.warning("Helius %s attempt %d connection error: %s — retrying in %.1fs",
                        method, attempt + 1, e, wait)
            time.sleep(wait)

    raise requests.RequestException(f"Helius {method} failed after {max_attempts} attempts")
