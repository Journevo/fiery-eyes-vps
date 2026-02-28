"""Social Pulse — unified 0-100 social intelligence score.

Platform weights:
  X/Twitter:     30%
  Google Trends: 20%
  Reddit:        15%
  TikTok:        15%
  YouTube:       10%
  Discord:       10%

Cross-platform 3+ = high conviction signal.
"""

from config import get_logger

log = get_logger("social.pulse")

WEIGHTS = {
    "x": 0.30,
    "google_trends": 0.20,
    "reddit": 0.15,
    "tiktok": 0.15,
    "youtube": 0.10,
    "discord": 0.10,
}


def _score_x(keyword: str, mint: str | None) -> float:
    """Get X/Twitter score (0-100).

    Blends DexScreener presence (0-40 range) with x_intelligence
    smart money signals (0-60 range) for a more complete picture.
    """
    # DexScreener proxy score (0-40 range)
    dex_score = 0.0
    try:
        from social.x_monitor import get_dexscreener_social_proxy, get_mention_count
        if mint:
            proxy = get_dexscreener_social_proxy(mint)
            dex_score = proxy.get("score", 0) * 0.4
        else:
            mentions = get_mention_count(keyword)
            dex_score = min(40, mentions.get("mentions", 0) * 0.4)
    except Exception:
        pass

    # X intelligence signals (0-60 range)
    x_intel_score = 0.0
    try:
        from social.grok_poller import get_x_intelligence_summary
        summary = get_x_intelligence_summary(mint)
        signal_count = summary.get("signal_count", 0)
        strong_signals = summary.get("strong_signals", 0)
        if signal_count > 0:
            x_intel_score = min(45, signal_count * 15) + min(15, strong_signals * 5)
    except Exception:
        pass

    return min(100, dex_score + x_intel_score)


def _score_trends(keyword: str) -> float:
    """Get Google Trends score (0-100)."""
    try:
        from social.google_trends import get_trend_score
        result = get_trend_score(keyword)
        score = result.get("interest_score", 0)
        if result.get("spike_detected"):
            score = min(100, score * 1.5)
        return score
    except Exception:
        return 0


def _score_reddit(keyword: str) -> float:
    """Get Reddit score (0-100)."""
    try:
        from social.reddit_monitor import get_mention_count
        result = get_mention_count(keyword)
        mentions = result.get("total_mentions", 0)
        if mentions >= 50:
            return 100
        if mentions >= 20:
            return 75
        if mentions >= 5:
            return 50
        if mentions >= 1:
            return 25
        return 0
    except Exception:
        return 0


def _score_tiktok(keyword: str) -> float:
    """Get TikTok score (0-100)."""
    try:
        from social.tiktok_monitor import get_view_count
        result = get_view_count(keyword)
        views = result.get("total_views", 0)
        if views >= 1_000_000:
            return 100
        if views >= 100_000:
            return 70
        if views >= 10_000:
            return 40
        return 0
    except Exception:
        return 0


def _score_youtube(keyword: str) -> float:
    """Get YouTube score (0-100)."""
    try:
        from social.youtube_monitor import get_video_count
        result = get_video_count(keyword)
        count = result.get("count", 0)
        if count >= 10:
            return 100
        if count >= 5:
            return 70
        if count >= 1:
            return 40
        return 0
    except Exception:
        return 0


def _score_discord(keyword: str, mint: str | None) -> float:
    """Get Discord score (0-100)."""
    try:
        from social.discord_monitor import check_discord_presence
        if mint:
            result = check_discord_presence(mint)
            return result.get("score", 0)
        return 0
    except Exception:
        return 0


def calculate_pulse(keyword: str, mint: str | None = None) -> dict:
    """Calculate unified social pulse score.

    Returns:
        {
            "pulse_score": float (0-100),
            "platform_scores": {platform: score},
            "cross_platform_count": int,
            "high_conviction": bool,
            "dominant_platform": str,
            "trend_direction": str,
        }
    """
    platform_scores = {
        "x": _score_x(keyword, mint),
        "google_trends": _score_trends(keyword),
        "reddit": _score_reddit(keyword),
        "tiktok": _score_tiktok(keyword),
        "youtube": _score_youtube(keyword),
        "discord": _score_discord(keyword, mint),
    }

    # Weighted pulse score
    pulse_score = sum(
        platform_scores[p] * WEIGHTS[p]
        for p in WEIGHTS
    )

    # Cross-platform count (platforms with meaningful signal)
    cross_platform_count = sum(1 for s in platform_scores.values() if s > 30)

    # High conviction = 3+ platforms active
    high_conviction = cross_platform_count >= 3

    # Dominant platform
    dominant = max(platform_scores, key=platform_scores.get)

    # Trend direction from Google Trends
    try:
        from social.google_trends import get_trend_score
        trends = get_trend_score(keyword)
        trend_direction = trends.get("trend_direction", "stable")
    except Exception:
        trend_direction = "stable"

    result = {
        "pulse_score": round(pulse_score, 1),
        "platform_scores": {k: round(v, 1) for k, v in platform_scores.items()},
        "cross_platform_count": cross_platform_count,
        "high_conviction": high_conviction,
        "dominant_platform": dominant,
        "trend_direction": trend_direction,
    }

    log.info("Pulse for '%s': %.0f/100 (%d platforms, conviction=%s)",
             keyword, pulse_score, cross_platform_count, high_conviction)

    return result


def get_platform_breakdown(keyword: str, mint: str | None = None) -> dict:
    """Get detailed per-platform social data.

    Returns dict with detailed data from each platform.
    """
    breakdown = {}

    try:
        from social.x_monitor import get_dexscreener_social_proxy
        if mint:
            breakdown["x"] = get_dexscreener_social_proxy(mint)
    except Exception:
        breakdown["x"] = {}

    try:
        from social.google_trends import get_trend_score
        breakdown["google_trends"] = get_trend_score(keyword)
    except Exception:
        breakdown["google_trends"] = {}

    try:
        from social.reddit_monitor import get_mention_count
        breakdown["reddit"] = get_mention_count(keyword)
    except Exception:
        breakdown["reddit"] = {}

    try:
        from social.tiktok_monitor import get_mention_count as tiktok_mentions
        breakdown["tiktok"] = tiktok_mentions(keyword)
    except Exception:
        breakdown["tiktok"] = {}

    try:
        from social.youtube_monitor import get_video_count
        breakdown["youtube"] = get_video_count(keyword)
    except Exception:
        breakdown["youtube"] = {}

    try:
        from social.discord_monitor import check_discord_presence
        if mint:
            breakdown["discord"] = check_discord_presence(mint)
    except Exception:
        breakdown["discord"] = {}

    return breakdown
