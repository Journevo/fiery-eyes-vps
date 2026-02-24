"""TikTok Monitor — STUB for Apify integration."""

from config import get_logger

log = get_logger("social.tiktok")


def get_mention_count(keyword: str, hours: int = 24) -> dict:
    """Get TikTok mention count. STUB."""
    log.warning("STUB: TikTok monitor not implemented for '%s'", keyword)
    return {"mentions": 0, "total_views": 0, "source": "stub"}


def get_trending_videos(keyword: str, limit: int = 10) -> list[dict]:
    """Get trending TikTok videos. STUB."""
    log.warning("STUB: TikTok trending not implemented for '%s'", keyword)
    return []


def get_view_count(keyword: str) -> dict:
    """Get view count for keyword. STUB."""
    log.warning("STUB: TikTok view count not implemented for '%s'", keyword)
    return {"total_views": 0, "avg_views_per_video": 0, "source": "stub"}
