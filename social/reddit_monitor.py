"""Reddit Monitor — STUB with PRAW interface.

Monitors r/cryptocurrency, r/solana for token mentions.

To implement:
    import praw
    reddit = praw.Reddit(
        client_id=REDDIT_CLIENT_ID,
        client_secret=REDDIT_CLIENT_SECRET,
        user_agent=REDDIT_USER_AGENT,
    )
"""

from config import get_logger

log = get_logger("social.reddit")

DEFAULT_SUBREDDITS = ["cryptocurrency", "solana", "CryptoMoonShots"]


def get_mention_count(keyword: str, subreddits: list[str] | None = None,
                      hours: int = 24) -> dict:
    """Get mention count for keyword across subreddits. STUB.

    Returns:
        {"total_mentions": int, "subreddit_breakdown": dict,
         "sentiment_score": float}
    """
    log.warning("STUB: Reddit monitor not implemented — returning defaults for '%s'", keyword)
    subs = subreddits or DEFAULT_SUBREDDITS
    return {
        "total_mentions": 0,
        "subreddit_breakdown": {s: 0 for s in subs},
        "sentiment_score": 0.0,
        "source": "stub",
    }


def get_trending_posts(subreddits: list[str] | None = None,
                       limit: int = 10) -> list[dict]:
    """Get trending crypto posts. STUB."""
    log.warning("STUB: Reddit trending posts not implemented")
    return []


def get_sentiment(keyword: str, subreddits: list[str] | None = None) -> dict:
    """Get sentiment analysis for keyword. STUB.

    Returns:
        {"positive_pct": float, "negative_pct": float,
         "neutral_pct": float, "overall_score": float}
    """
    log.warning("STUB: Reddit sentiment not implemented for '%s'", keyword)
    return {
        "positive_pct": 0.0,
        "negative_pct": 0.0,
        "neutral_pct": 100.0,
        "overall_score": 0.0,
        "source": "stub",
    }
