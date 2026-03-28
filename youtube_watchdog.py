"""YouTube Watchdog — independent monitor for the YouTube pipeline.

Runs every 4 hours. Checks if scans ran, videos found, analyses generated,
and messages sent. Alerts if anything stalled during market hours.

INDEPENDENT of the YouTube scan — if scan crashes, watchdog still fires.
"""
from datetime import datetime, timezone, timedelta
from db.connection import execute, execute_one
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
import requests

log = get_logger("youtube_watchdog")

KEYBOARD = {
    "keyboard": [["🌍 Macro", "₿ Cycle", "🪙 Tokens"], ["🧠 Intel", "📚 Learn", "💼 Command"]],
    "resize_keyboard": True, "is_persistent": True,
}

MARKET_HOURS_START = 6   # 06:00 UTC
MARKET_HOURS_END = 22    # 22:00 UTC


def _send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "disable_web_page_preview": True, "reply_markup": KEYBOARD},
            timeout=15)
    except Exception as e:
        log.error("Watchdog send failed: %s", e)


def check_youtube_pipeline() -> dict:
    """Check all 4 pipeline stages. Returns status dict."""
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(hours=4)

    result = {
        "timestamp": now.isoformat(),
        "scan_ran": False,
        "scan_time": None,
        "videos_found": 0,
        "analyses_generated": 0,
        "sonnet_count": 0,
        "haiku_count": 0,
        "sent_to_telegram": 0,
        "is_market_hours": MARKET_HOURS_START <= now.hour < MARKET_HOURS_END,
        "healthy": False,
    }

    # 1. Videos found in DB (last 4h)
    try:
        row = execute_one(
            "SELECT COUNT(*) FROM youtube_videos WHERE processed_at > %s",
            (lookback,))
        result["videos_found"] = row[0] if row else 0
    except Exception as e:
        log.error("Watchdog DB check failed: %s", e)

    # 2. Check for analyses with transcript (Sonnet proxy)
    try:
        row = execute_one("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE transcript_text IS NOT NULL AND LENGTH(transcript_text) > 200),
                   MAX(processed_at)
            FROM youtube_videos WHERE processed_at > %s
        """, (lookback,))
        if row:
            result["analyses_generated"] = row[0] or 0
            result["sonnet_count"] = row[1] or 0
            result["haiku_count"] = (row[0] or 0) - (row[1] or 0)
            if row[2]:
                result["scan_ran"] = True
                result["scan_time"] = row[2].isoformat() if hasattr(row[2], 'isoformat') else str(row[2])
    except Exception as e:
        log.error("Watchdog analysis check failed: %s", e)

    # 3. Check scan ran by looking at the most recent video
    if result["videos_found"] > 0:
        result["scan_ran"] = True

    # If we haven't scanned recently, check if there's ANY recent video at all
    if not result["scan_ran"]:
        try:
            row = execute_one("SELECT MAX(processed_at) FROM youtube_videos")
            if row and row[0]:
                last = row[0]
                result["scan_time"] = last.isoformat() if hasattr(last, 'isoformat') else str(last)
                # If last scan was within 6 hours, not completely dead
                if (now - last.replace(tzinfo=timezone.utc if last.tzinfo is None else last.tzinfo)).total_seconds() < 21600:
                    result["scan_ran"] = True
        except Exception:
            pass

    # 4. Estimate sent to Telegram (Sonnet videos get sent)
    # We don't have a send log, so estimate: Sonnet videos = sent
    result["sent_to_telegram"] = result["sonnet_count"]

    # Health assessment
    if result["videos_found"] >= 1 and result["scan_ran"]:
        result["healthy"] = True
    elif not result["is_market_hours"]:
        # Outside market hours, fewer videos expected
        result["healthy"] = result["scan_ran"]

    return result


def run_watchdog(send_alert: bool = True) -> dict:
    """Run the watchdog check. Alert if stalled during market hours."""
    status = check_youtube_pipeline()
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%H:%M UTC")

    log.info("YouTube watchdog: videos=%d analyses=%d sonnet=%d haiku=%d healthy=%s",
             status["videos_found"], status["analyses_generated"],
             status["sonnet_count"], status["haiku_count"], status["healthy"])

    # Build heartbeat line (always logged)
    heartbeat = "YouTube: %d videos %d analyses (%dS/%dH) in 4h" % (
        status["videos_found"], status["analyses_generated"],
        status["sonnet_count"], status["haiku_count"])

    if not status["healthy"] and status["is_market_hours"] and send_alert:
        # ALERT — pipeline stalled during market hours
        scan_time = status["scan_time"] or "NEVER"
        if isinstance(scan_time, str) and len(scan_time) > 19:
            scan_time = scan_time[:19]

        alert = (
            "WARNING: YOUTUBE PIPELINE STALLED\n\n"
            "  Last scan: %s\n"
            "  Videos found (4h): %d\n"
            "  Analyses generated: %d\n"
            "  Sonnet: %d | Haiku: %d\n"
            "  Sent to Telegram: %d\n\n"
            "CHECK: journalctl -u fiery-eyes-v5 --since '4h ago' | grep youtube"
        ) % (scan_time, status["videos_found"], status["analyses_generated"],
             status["sonnet_count"], status["haiku_count"], status["sent_to_telegram"])

        _send(alert)
        log.warning("YouTube watchdog ALERT sent: %s", heartbeat)
    elif send_alert:
        # All good — include in periodic health (not every time)
        pass

    status["heartbeat"] = heartbeat
    return status


def format_heartbeat_line() -> str:
    """One-line status for inclusion in other heartbeats."""
    status = check_youtube_pipeline()
    icon = "+" if status["healthy"] else "!"
    return "[%s] YouTube: %d videos %d analyses (%dS/%dH) 4h | last %s" % (
        icon, status["videos_found"], status["analyses_generated"],
        status["sonnet_count"], status["haiku_count"],
        (status["scan_time"] or "never")[:16])


if __name__ == "__main__":
    status = run_watchdog(send_alert=False)
    import json
    print(json.dumps(status, indent=2, default=str))
    print()
    print("Heartbeat:", format_heartbeat_line())
