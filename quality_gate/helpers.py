"""Shared helpers: retry logic, HTTP sessions."""

import time
import requests
from config import get_logger

log = get_logger("helpers")


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
    """POST request returning parsed JSON with retry."""
    resp = retry_request("POST", url, json=payload, **kwargs)
    return resp.json()
