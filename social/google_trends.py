"""Google Trends monitoring — spike detection using pytrends."""

from config import get_logger

log = get_logger("social.google_trends")


def get_trend_score(keyword: str, timeframe: str = "now 7-d") -> dict:
    """Get Google Trends interest score for a keyword.

    Returns:
        {"interest_score": int, "spike_detected": bool,
         "spike_pct": float, "trend_direction": str}
    """
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pytrends.build_payload([keyword], cat=0, timeframe=timeframe)
        data = pytrends.interest_over_time()

        if data.empty:
            return {"interest_score": 0, "spike_detected": False,
                    "spike_pct": 0, "trend_direction": "stable"}

        values = data[keyword].tolist()
        current = values[-1] if values else 0
        avg = sum(values) / len(values) if values else 0

        spike_pct = ((current - avg) / avg * 100) if avg > 0 else 0
        spike_detected = spike_pct > 200

        if len(values) >= 2:
            if values[-1] > values[-2] * 1.1:
                direction = "rising"
            elif values[-1] < values[-2] * 0.9:
                direction = "falling"
            else:
                direction = "stable"
        else:
            direction = "stable"

        return {
            "interest_score": current,
            "spike_detected": spike_detected,
            "spike_pct": round(spike_pct, 1),
            "trend_direction": direction,
        }
    except ImportError:
        log.warning("pytrends not installed — Google Trends unavailable")
        return {"interest_score": 0, "spike_detected": False,
                "spike_pct": 0, "trend_direction": "stable"}
    except Exception as e:
        log.error("Google Trends fetch failed for '%s': %s", keyword, e)
        return {"interest_score": 0, "spike_detected": False,
                "spike_pct": 0, "trend_direction": "stable"}


def detect_spike(keyword: str, threshold_pct: float = 200) -> bool:
    """Check if keyword has spiked >threshold_pct in 24h."""
    result = get_trend_score(keyword, timeframe="now 1-d")
    return result["spike_pct"] > threshold_pct


def get_related_queries(keyword: str) -> list[str]:
    """Get related search queries for a keyword."""
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=360, timeout=(10, 25))
        pytrends.build_payload([keyword], timeframe="now 7-d")
        related = pytrends.related_queries()
        top = related.get(keyword, {}).get("top")
        if top is not None and not top.empty:
            return top["query"].tolist()[:10]
        return []
    except ImportError:
        log.warning("pytrends not installed")
        return []
    except Exception as e:
        log.error("Related queries failed for '%s': %s", keyword, e)
        return []
