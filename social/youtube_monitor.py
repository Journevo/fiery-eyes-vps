"""YouTube Monitor — STUB for YouTube Data API."""

from config import get_logger

log = get_logger("social.youtube")


def get_video_count(keyword: str, hours: int = 48) -> dict:
    """Get recent video count for keyword. STUB."""
    log.warning("STUB: YouTube monitor not implemented for '%s'", keyword)
    return {"count": 0, "total_views": 0, "source": "stub"}


def get_trending_videos(keyword: str, limit: int = 5) -> list[dict]:
    """Get trending YouTube videos. STUB."""
    log.warning("STUB: YouTube trending not implemented for '%s'", keyword)
    return []


def get_channel_mentions(keyword: str) -> dict:
    """Get channel mentions for keyword. STUB."""
    log.warning("STUB: YouTube channel mentions not implemented for '%s'", keyword)
    return {"channels_mentioning": 0, "total_subscribers": 0, "source": "stub"}
