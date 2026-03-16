"""YouTube Health Check — runs every 4h, reports to Telegram."""
import json
from datetime import datetime, timezone
from db.connection import execute
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
import requests

log = get_logger("youtube_health")

# Must match youtube_free.py
HIGH_PRIORITY = {
    "All-In Podcast", "Lex Fridman", "Principles by Ray Dalio",
    "Real Vision Finance", "Real Vision", "Raoul Pal",
    "Impact Theory", "PowerfulJRE",
    "The Diary Of A CEO", "Diary of a CEO",
    "InvestAnswers", "Benjamin Cowen", "Coin Bureau",
    "Bankless", "Crypto Banter", "VirtualBacon", "Virtual Bacon",
    "Mark Moss", "ColinTalksCrypto", "Colin Talks Crypto",
    "Krypto King", "Chart Fanatics", "Crypto Insider",
    "Jack Neel", "Titans of Tomorrow",
}

# Canonical names for display (deduplicated)
SONNET_DISPLAY = [
    "All-In Podcast", "Lex Fridman", "Principles by Ray Dalio",
    "Real Vision Finance", "Raoul Pal", "Impact Theory", "PowerfulJRE",
    "The Diary Of A CEO", "InvestAnswers", "Benjamin Cowen", "Coin Bureau",
    "Bankless", "Crypto Banter", "VirtualBacon", "Mark Moss",
    "ColinTalksCrypto", "Krypto King", "Chart Fanatics", "Crypto Insider",
    "Jack Neel", "Titans of Tomorrow",
]

_KEYBOARD = {
    "keyboard": [["📊 Intel", "🐋 Signals", "🔥 Fiery Eyes"], ["💼 Portfolio", "⚙️ System"]],
    "resize_keyboard": True, "is_persistent": True,
}


def generate_health_report() -> str:
    """Generate YouTube health check report."""
    now = datetime.now(timezone.utc)
    now_str = now.strftime("%H:%M UTC")

    # Get all videos processed today
    rows = execute("""
        SELECT channel_name, title, processed_at, video_id,
               CASE WHEN transcript_text IS NOT NULL AND LENGTH(transcript_text) > 100
                    THEN TRUE ELSE FALSE END as has_transcript
        FROM youtube_videos
        WHERE processed_at > CURRENT_DATE
        ORDER BY processed_at DESC
    """, fetch=True)

    videos = rows or []
    sonnet_videos = []
    haiku_videos = []

    for r in videos:
        ch = r[0] or "?"
        if ch in HIGH_PRIORITY:
            sonnet_videos.append(r)
        else:
            haiku_videos.append(r)

    # Find which Sonnet channels posted today
    channels_with_video = set(r[0] for r in videos if r[0])
    silent_channels = [c for c in SONNET_DISPLAY if c not in channels_with_video]

    lines = []
    lines.append("\U0001f4fa <b>YOUTUBE HEALTH</b> \u2014 %s" % now_str)
    lines.append("Today: %d videos (%d Sonnet, %d Haiku)\n" % (
        len(videos), len(sonnet_videos), len(haiku_videos)))

    if sonnet_videos:
        lines.append("<b>SONNET:</b>")
        for r in sonnet_videos:
            ch, title, ts, vid, has_tx = r
            time_str = ts.strftime("%H:%M") if ts else "?"
            icon = "\u2705" if has_tx else "\u274c"
            lines.append("%s %s \u2014 \"%s\" \u2014 %s" % (icon, ch, (title or "?")[:40], time_str))
        lines.append("")

    if haiku_videos:
        lines.append("<b>HAIKU:</b>")
        for r in haiku_videos[:10]:  # Cap at 10
            ch, title, ts, vid, has_tx = r
            time_str = ts.strftime("%H:%M") if ts else "?"
            icon = "\u2705" if has_tx else "\u274c"
            lines.append("%s %s \u2014 \"%s\" \u2014 %s" % (icon, ch, (title or "?")[:40], time_str))
        if len(haiku_videos) > 10:
            lines.append("  +%d more" % (len(haiku_videos) - 10))
        lines.append("")

    if silent_channels:
        lines.append("Silent today: %s" % ", ".join(silent_channels[:8]))
        if len(silent_channels) > 8:
            lines.append("  +%d more" % (len(silent_channels) - 8))

    return "\n".join(lines)


def send_health_report():
    """Generate and send YouTube health to Telegram."""
    report = generate_health_report()
    log.info("YouTube health report:\n%s", report)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(
            "https://api.telegram.org/bot%s/sendMessage" % TELEGRAM_BOT_TOKEN,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": report,
                  "parse_mode": "HTML", "disable_web_page_preview": True,
                  "reply_markup": _KEYBOARD},
            timeout=15)

    return report


if __name__ == "__main__":
    print(generate_health_report())
