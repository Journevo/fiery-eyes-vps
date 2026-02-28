"""X/Twitter Monitor — smart money signals via Grok + DexScreener proxy.

Queries x_intelligence table (populated by grok_poller) for real signal data.
DexScreener social proxy remains as a complementary check for token-level social presence.
"""

from config import get_logger
from db.connection import execute
from quality_gate.helpers import get_json

log = get_logger("social.x_monitor")

DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/latest/dex/tokens"


def get_mention_count(keyword: str, hours: int = 24) -> dict:
    """Get X smart money mention count for a keyword from x_intelligence table."""
    try:
        rows = execute(
            """SELECT COUNT(*) as mentions,
                      COUNT(DISTINCT source_handle) as unique_authors
               FROM x_intelligence
               WHERE (tweet_text ILIKE %s OR token_symbol ILIKE %s)
                 AND detected_at > NOW() - INTERVAL '%s hours'""",
            (f"%{keyword}%", f"%{keyword}%", hours),
            fetch=True,
        )
        if rows and rows[0]:
            return {
                "mentions": rows[0][0] or 0,
                "unique_authors": rows[0][1] or 0,
                "impressions": 0,
                "source": "x_intelligence",
            }
    except Exception as e:
        log.debug("get_mention_count query failed for '%s': %s", keyword, e)

    return {"mentions": 0, "unique_authors": 0, "impressions": 0, "source": "x_intelligence"}


def get_sentiment(keyword: str) -> dict:
    """Derive X sentiment from signal strength distribution."""
    try:
        rows = execute(
            """SELECT signal_strength, COUNT(*) as cnt
               FROM x_intelligence
               WHERE (tweet_text ILIKE %s OR token_symbol ILIKE %s)
                 AND detected_at > NOW() - INTERVAL '24 hours'
               GROUP BY signal_strength""",
            (f"%{keyword}%", f"%{keyword}%"),
            fetch=True,
        )
        if rows:
            counts = {r[0]: r[1] for r in rows}
            total = sum(counts.values())
            strong = counts.get("strong", 0)
            medium = counts.get("medium", 0)
            weak = counts.get("weak", 0)

            positive_pct = round((strong + medium * 0.5) / total * 100) if total else 0
            negative_pct = 0
            neutral_pct = 100 - positive_pct

            return {
                "positive_pct": positive_pct,
                "negative_pct": negative_pct,
                "neutral_pct": neutral_pct,
                "overall_score": positive_pct / 100,
                "source": "x_intelligence",
            }
    except Exception as e:
        log.debug("get_sentiment query failed for '%s': %s", keyword, e)

    return {"positive_pct": 0, "negative_pct": 0, "neutral_pct": 100,
            "overall_score": 0.0, "source": "x_intelligence"}


def get_influencer_mentions(keyword: str, min_followers: int = 10000) -> list[dict]:
    """Get smart money account mentions for a keyword."""
    try:
        rows = execute(
            """SELECT source_handle, tweet_text, detected_at, signal_strength
               FROM x_intelligence
               WHERE (tweet_text ILIKE %s OR token_symbol ILIKE %s)
                 AND detected_at > NOW() - INTERVAL '24 hours'
               ORDER BY detected_at DESC LIMIT 10""",
            (f"%{keyword}%", f"%{keyword}%"),
            fetch=True,
        )
        return [
            {
                "handle": r[0],
                "tweet_text": r[1][:200] if r[1] else "",
                "detected_at": str(r[2]),
                "signal_strength": r[3],
            }
            for r in (rows or [])
        ]
    except Exception as e:
        log.debug("get_influencer_mentions failed for '%s': %s", keyword, e)
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
