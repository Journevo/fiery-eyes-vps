"""YouTube Intelligence — Task 11 of Fiery Eyes v5.1

Enhanced extraction from existing YouTube pipeline:
- Price targets, reasoning, conditions, personal action
- Channel weighting by subscriber count
- Recency decay: >48h = stale
- Health check: alert if no transcripts for 24h
- Only surface in report when watchlist token mentioned >7/10 conviction
- Multi-channel convergence detection
"""

import json
import requests
from datetime import datetime, timedelta, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.connection import execute

log = get_logger("youtube_intel")

# Persistent keyboard for Telegram messages
_KEYBOARD_JSON = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True,
    "is_persistent": True,
}


# v5.1 watchlist tokens
WATCHLIST = {"BTC", "SOL", "JUP", "HYPE", "RENDER", "BONK", "PUMP", "PENGU", "FARTCOIN", "MSTR", "COIN"}

# Minimum conviction to surface in report
MIN_CONVICTION_REPORT = 7

# Channel weights (approximate subscriber tiers)
CHANNEL_WEIGHTS = {
    "Benjamin Cowen": 3,    # 800K+ subs, rigorous analysis
    "Coin Bureau": 3,       # 2M+ subs
    "Raoul Pal": 3,         # 1M+ subs, macro authority
    "InvestAnswers": 2,     # 450K, data-driven
    "Bankless": 2,          # 800K, DeFi focus
    "Lyn Alden": 3,         # macro authority
    "Altcoin Daily": 2,     # 1.4M subs
    "Miles Deutscher": 2,   # Solana focused
    "Real Vision": 3,       # institutional macro
    "Lark Davis": 1,        # frequent but lower signal
    "Crypto Banter": 1,     # high frequency, lower signal
    "Ivan on Tech": 1,
    "The Breakdown": 2,
    "Patrick Boyle": 2,     # traditional finance perspective
    "Wolf of All Streets": 2,
    "Anthony Pompliano": 2,
}


def get_recent_youtube_intel(hours: int = 48) -> dict:
    """Query recent YouTube analyses for watchlist tokens.

    Returns structured intelligence summary.
    """
    rows = execute("""
        SELECT channel_name, title, tokens_mentioned, analysis_json,
               relevance_score, processed_at, video_id
        FROM youtube_videos
        WHERE processed_at > NOW() - INTERVAL '%s hours'
          AND tokens_mentioned IS NOT NULL
          AND jsonb_array_length(tokens_mentioned) > 0
        ORDER BY processed_at DESC
    """ % int(hours), fetch=True)

    if not rows:
        return {"videos": 0, "watchlist_mentions": [], "convergence": [], "stale": hours > 48}

    # Aggregate mentions by token
    token_mentions = {}  # {symbol: [{channel, sentiment, conviction, price_target, video_title}]}
    all_videos = 0

    for channel, title, tokens_json, analysis_json, score, processed_at, video_id in rows:
        all_videos += 1
        tokens = tokens_json if isinstance(tokens_json, list) else json.loads(tokens_json) if tokens_json else []

        for token in tokens:
            symbol = token.get("symbol", "").upper()
            if not symbol:
                continue

            conviction = token.get("conviction", 0)
            sentiment = token.get("sentiment", "neutral")
            price_target = token.get("price_target")

            if symbol not in token_mentions:
                token_mentions[symbol] = []

            channel_weight = CHANNEL_WEIGHTS.get(channel, 1)

            token_mentions[symbol].append({
                "channel": channel,
                "sentiment": sentiment,
                "conviction": conviction,
                "weighted_conviction": (conviction or 0) * channel_weight,
                "price_target": price_target,
                "video_title": title,
                "video_id": video_id,
                "hours_ago": round((datetime.now() - processed_at).total_seconds() / 3600, 1) if processed_at else 0,
                "channel_weight": channel_weight,
            })

    # Filter for watchlist with high conviction
    watchlist_mentions = []
    for symbol in WATCHLIST:
        if symbol not in token_mentions:
            continue
        mentions = token_mentions[symbol]

        # Calculate aggregate metrics
        total_mentions = len(mentions)
        bullish = sum(1 for m in mentions if m.get("sentiment", "neutral") == "bullish")
        bearish = sum(1 for m in mentions if m.get("sentiment", "neutral") == "bearish")
        avg_conviction = sum((m.get("conviction") or 0) for m in mentions) / total_mentions if total_mentions else 0
        weighted_avg = sum((m.get("weighted_conviction") or 0) for m in mentions) / sum((m.get("channel_weight") or 1) for m in mentions) if mentions else 0
        price_targets = [m.get("price_target") for m in mentions if m.get("price_target")]
        unique_channels = list(set(m.get("channel", "?") for m in mentions))

        watchlist_mentions.append({
            "symbol": symbol,
            "total_mentions": total_mentions,
            "bullish": bullish,
            "bearish": bearish,
            "neutral": total_mentions - bullish - bearish,
            "avg_conviction": round(avg_conviction, 1),
            "weighted_conviction": round(weighted_avg, 1),
            "price_targets": price_targets,
            "channels": unique_channels,
            "top_mentions": sorted(mentions, key=lambda m: (m.get("weighted_conviction") or 0), reverse=True)[:3],
        })

    # Sort by weighted conviction
    watchlist_mentions.sort(key=lambda x: (x.get("weighted_conviction") or 0), reverse=True)

    # Detect convergence: 2+ channels bullish on same token in 24h
    convergence = []
    for wm in watchlist_mentions:
        recent_bullish = [m for m in token_mentions.get(wm["symbol"], [])
                         if m.get("sentiment", "neutral") == "bullish" and m.get("hours_ago", 999) <= 24]
        unique_bullish_channels = set(m.get("channel", "?") for m in recent_bullish)
        if len(unique_bullish_channels) >= 2:
            convergence.append({
                "symbol": wm["symbol"],
                "channels": list(unique_bullish_channels),
                "count": len(unique_bullish_channels),
            })

    return {
        "videos": all_videos,
        "watchlist_mentions": watchlist_mentions,
        "convergence": convergence,
        "all_token_mentions": {k: len(v) for k, v in token_mentions.items()},
    }


def check_youtube_health() -> dict:
    """Check if YouTube pipeline is healthy."""
    row = execute("""
        SELECT COUNT(*), MAX(processed_at)
        FROM youtube_videos
        WHERE processed_at > NOW() - INTERVAL '24 hours'
    """, fetch=True)

    count = row[0][0] if row and row[0] else 0
    latest = row[0][1] if row and row[0] else None

    hours_since = None
    if latest:
        hours_since = round((datetime.now() - latest).total_seconds() / 3600, 1)

    healthy = count > 0 and (hours_since is not None and hours_since < 24)

    return {
        "healthy": healthy,
        "videos_24h": count,
        "hours_since_last": hours_since,
    }


# ---------------------------------------------------------------------------
# Telegram output
# ---------------------------------------------------------------------------
def format_youtube_telegram(intel: dict) -> str:
    """Format YouTube intelligence for Telegram."""
    watchlist = intel.get("watchlist_mentions", [])
    convergence = intel.get("convergence", [])

    if not watchlist:
        return "📺 No watchlist token mentions in YouTube (48h)"

    lines = [f"📺 <b>YOUTUBE INTEL</b> ({intel['videos']} videos / 48h)", ""]

    # Only show tokens with conviction >= threshold
    high_conviction = [w for w in watchlist if (w.get("weighted_conviction") or 0) >= MIN_CONVICTION_REPORT]
    if not high_conviction:
        # Show top 3 even if below threshold
        high_conviction = watchlist[:3]

    for wm in high_conviction:
        sentiment_bar = f"🟢{wm['bullish']}" if wm["bullish"] > wm["bearish"] else f"🔴{wm['bearish']}"
        if wm["bullish"] == wm["bearish"]:
            sentiment_bar = f"⚪{wm['neutral']}"

        targets_str = ""
        if wm["price_targets"]:
            targets_str = f" | Targets: {', '.join(str(t) for t in wm['price_targets'][:3])}"

        channels_str = ", ".join(wm["channels"][:3])
        if len(wm["channels"]) > 3:
            channels_str += f" +{len(wm['channels']) - 3}"

        lines.append(
            f"<b>${wm['symbol']}</b>: {wm['total_mentions']} mentions ({sentiment_bar}) "
            f"conv: {(wm.get('weighted_conviction') or 0):.0f}/10{targets_str}\n"
            f"  📢 {channels_str}"
        )

    # Convergence alerts
    if convergence:
        lines.append("")
        for c in convergence:
            lines.append(f"🔀 <b>${c['symbol']} CONVERGENCE</b>: {c['count']} channels bullish ({', '.join(c['channels'][:3])})")

    return "\n".join(lines)


def format_youtube_for_report(intel: dict) -> str | None:
    """Format YouTube section for daily report. Returns None if nothing to show."""
    watchlist = [w for w in intel.get("watchlist_mentions", [])
                 if w["weighted_conviction"] >= 3 or w["total_mentions"] >= 2]

    if not watchlist:
        return None

    lines = []
    for wm in watchlist[:4]:
        sentiment = "🟢" if wm["bullish"] > wm["bearish"] else ("🔴" if wm["bearish"] > wm["bullish"] else "⚪")
        channels = ", ".join(wm["channels"][:2])
        lines.append(f"  {sentiment} ${wm['symbol']}: {wm['total_mentions']} mentions, conv {(wm.get("weighted_conviction") or 0):.0f}/10 ({channels})")

    convergence = intel.get("convergence", [])
    for c in convergence:
        lines.append(f"  🔀 ${c['symbol']}: {c['count']} channels bullish")

    return "\n".join(lines)


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": _KEYBOARD_JSON,
        }, timeout=15)
        if resp.status_code != 200:
            log.error("Telegram failed: %s", resp.text)
    except Exception as e:
        log.error("Telegram error: %s", e)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_youtube_intel(send_to_telegram: bool = False) -> dict:
    """Collect and format YouTube intelligence."""
    intel = get_recent_youtube_intel(hours=48)

    # Health check
    health = check_youtube_health()
    if not health["healthy"]:
        log.warning("YouTube pipeline unhealthy: %d videos in 24h, last %.1fh ago",
                    health["videos_24h"], health.get("hours_since_last") or 999)
        if send_to_telegram:
            send_telegram(f"⚠️ YouTube pipeline: {health['videos_24h']} videos in 24h (unhealthy)")

    msg = format_youtube_telegram(intel)
    log.info("YouTube intel:\n%s", msg)

    if send_to_telegram:
        send_telegram(msg)

    return intel


if __name__ == "__main__":
    import sys
    send_tg = "--telegram" in sys.argv
    result = run_youtube_intel(send_to_telegram=send_tg)
    print(format_youtube_telegram(result))
