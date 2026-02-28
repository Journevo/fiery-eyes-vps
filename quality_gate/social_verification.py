"""Check 0: Social Verification — reject tokens with dead X/website links.

Fetches social links from DexScreener, then HEAD/GET checks each URL.
Pass criteria: at least one (twitter OR website) must be alive (HTTP 200-399).
No socials at all = fail.
"""

import requests
from config import get_logger
from quality_gate.helpers import get_json

log = get_logger("gate.social_verification")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
URL_CHECK_TIMEOUT = 5


def _url_alive(url: str) -> bool:
    """Check if a URL is reachable (HTTP 200-399). HEAD first, GET fallback."""
    try:
        resp = requests.head(url, timeout=URL_CHECK_TIMEOUT, allow_redirects=True)
        if 200 <= resp.status_code < 400:
            return True
    except requests.RequestException:
        pass

    # GET fallback for servers that reject HEAD
    try:
        resp = requests.get(url, timeout=URL_CHECK_TIMEOUT, allow_redirects=True,
                            stream=True)
        alive = 200 <= resp.status_code < 400
        resp.close()
        return alive
    except requests.RequestException:
        return False


def check(mint: str) -> dict:
    """Social verification gate check.

    Returns:
        {
            "pass": bool,
            "has_twitter": bool,
            "has_website": bool,
            "twitter_alive": bool,
            "website_alive": bool,
            "twitter_url": str | None,
            "website_url": str | None,
            "reason": str | None,
        }
    """
    result = {
        "pass": False,
        "has_twitter": False,
        "has_website": False,
        "twitter_alive": False,
        "website_alive": False,
        "twitter_url": None,
        "website_url": None,
        "reason": None,
    }

    try:
        data = get_json(DEXSCREENER_TOKEN_URL.format(mint=mint))
        pairs = data.get("pairs") or []
        if not pairs:
            result["reason"] = "No pairs found on DexScreener"
            log.info("Social verification FAIL for %s: %s", mint, result["reason"])
            return result

        # Use highest-volume pair
        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        info = best.get("info", {}) or {}
        socials = info.get("socials", [])
        websites = info.get("websites", [])

        # Extract twitter/x URL
        for s in socials:
            if s.get("type") in ("twitter", "x"):
                result["has_twitter"] = True
                result["twitter_url"] = s.get("url")
                break

        # Extract website URL
        if websites:
            for w in websites:
                url = w.get("url") if isinstance(w, dict) else w
                if url:
                    result["has_website"] = True
                    result["website_url"] = url
                    break

        # No socials at all = fail
        if not result["has_twitter"] and not result["has_website"]:
            result["reason"] = "No twitter or website links found"
            log.info("Social verification FAIL for %s: %s", mint, result["reason"])
            return result

        # Check liveness
        if result["twitter_url"]:
            result["twitter_alive"] = _url_alive(result["twitter_url"])

        if result["website_url"]:
            result["website_alive"] = _url_alive(result["website_url"])

        # Pass if at least one is alive
        if result["twitter_alive"] or result["website_alive"]:
            result["pass"] = True
            log.info("Social verification PASS for %s (twitter=%s, website=%s)",
                     mint, result["twitter_alive"], result["website_alive"])
        else:
            result["reason"] = "All social links are dead"
            log.info("Social verification FAIL for %s: %s", mint, result["reason"])

    except Exception as e:
        log.error("Social verification error for %s: %s", mint, e)
        result["reason"] = f"Social verification error: {e}"

    return result


# --- Self-test ---

if __name__ == "__main__":
    import sys

    mint = sys.argv[1] if len(sys.argv) > 1 else "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
    print(f"Checking social verification for {mint}...")
    r = check(mint)
    for k, v in r.items():
        print(f"  {k}: {v}")
    print(f"\nResult: {'PASS' if r['pass'] else 'FAIL'}")
