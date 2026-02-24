"""X/Twitter Monitor — STUB for Grok API, DexScreener proxy implemented.

Full X monitoring requires Grok API access (GROK_API_KEY).
DexScreener social proxy is functional now.
"""

from config import get_logger
from quality_gate.helpers import get_json

log = get_logger("social.x_monitor")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


def get_mention_count(keyword: str, hours: int = 24) -> dict:
    """Get X/Twitter mention count. STUB."""
    log.warning("STUB: X monitor not implemented — returning defaults for '%s'", keyword)
    return {"mentions": 0, "unique_authors": 0, "impressions": 0, "source": "stub"}


def get_sentiment(keyword: str) -> dict:
    """Get X sentiment for keyword. STUB."""
    log.warning("STUB: X sentiment not implemented for '%s'", keyword)
    return {"positive_pct": 0, "negative_pct": 0, "neutral_pct": 100,
            "overall_score": 0.0, "source": "stub"}


def get_influencer_mentions(keyword: str, min_followers: int = 10000) -> list[dict]:
    """Get influencer mentions. STUB."""
    log.warning("STUB: X influencer mentions not implemented for '%s'", keyword)
    return []


def get_dexscreener_social_proxy(mint: str) -> dict:
    """Get social presence data from DexScreener as proxy for X activity.

    Returns:
        {"has_twitter": bool, "twitter_url": str|None,
         "social_count": int, "socials": list,
         "score": int (0-100)}
    """
    try:
        data = get_json(f"{DEXSCREENER_TOKEN_URL}/{mint}")
        pairs = data.get("pairs") or []
        if not pairs:
            return {"has_twitter": False, "twitter_url": None,
                    "social_count": 0, "socials": [], "score": 0}

        best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
        info = best.get("info", {}) or {}
        socials = info.get("socials", [])

        twitter_url = None
        has_twitter = False
        for s in socials:
            if s.get("type") in ("twitter", "x"):
                has_twitter = True
                twitter_url = s.get("url")
                break

        social_count = len(socials)
        # Score: more socials = higher score, twitter presence is key
        score = 0
        if has_twitter:
            score += 40
        score += min(60, social_count * 15)

        return {
            "has_twitter": has_twitter,
            "twitter_url": twitter_url,
            "social_count": social_count,
            "socials": socials,
            "score": min(100, score),
        }
    except Exception as e:
        log.error("DexScreener social proxy failed for %s: %s", mint, e)
        return {"has_twitter": False, "twitter_url": None,
                "social_count": 0, "socials": [], "score": 0}
