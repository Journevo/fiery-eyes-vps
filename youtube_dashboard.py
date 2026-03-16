"""youtube_dashboard.py — YouTube summary dashboard with emojis."""
import json
from datetime import datetime, timezone
from db.connection import execute
from config import get_logger

log = get_logger("youtube_dashboard")

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


def generate_youtube_dashboard():
    rows = execute("""
        SELECT channel_name, title, processed_at, analysis_json, relevance_score, tokens_mentioned
        FROM youtube_videos WHERE processed_at > NOW() - INTERVAL '24 hours'
        ORDER BY relevance_score DESC NULLS LAST, processed_at DESC
    """, fetch=True)

    if not rows:
        return "\U0001f4fa No YouTube videos analysed in last 24h."

    total = len(rows)
    sonnet = sum(1 for r in rows if r[0] in HIGH_PRIORITY)
    haiku = total - sonnet
    token_counts = {}
    highlights = []

    for ch, title, ts, analysis_json, rel, tokens_str in rows:
        is_sonnet = ch in HIGH_PRIORITY

        if tokens_str:
            try:
                tokens = json.loads(tokens_str) if isinstance(tokens_str, str) else tokens_str
                if isinstance(tokens, list):
                    for t in tokens:
                        sym = t.get("symbol", t) if isinstance(t, dict) else str(t)
                        sym = sym.upper().strip("$")
                        if sym and len(sym) <= 10:
                            token_counts[sym] = token_counts.get(sym, 0) + 1
            except Exception:
                pass

        if is_sonnet and len(highlights) < 5:
            takeaway = ""
            if analysis_json:
                aj = analysis_json if isinstance(analysis_json, dict) else {}
                if isinstance(analysis_json, str):
                    try:
                        aj = json.loads(analysis_json)
                    except Exception:
                        aj = {}
                summary = aj.get("summary", "")
                if summary:
                    for line in summary.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("**") and not line.startswith("#") and len(line) > 30:
                            takeaway = line[:100]
                            if len(line) > 100:
                                takeaway = takeaway.rsplit(" ", 1)[0] + "..."
                            break
            if not takeaway:
                takeaway = (title or "")[:80]
            ts_str = ts.strftime("%H:%M") if ts else "?"
            highlights.append((ch, title[:45], takeaway, ts_str))

    lines = []
    lines.append("\U0001f4fa <b>YOUTUBE TODAY</b> \u2014 %d videos (%d Sonnet, %d Haiku)" % (total, sonnet, haiku))
    lines.append("")

    if token_counts:
        sorted_t = sorted(token_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        lines.append("\U0001f4cc <b>TOP MENTIONS</b>")
        for sym, count in sorted_t:
            e = "\U0001f525" if count >= 5 else "\U0001f53a" if count >= 3 else "\u2022"
            lines.append("  %s %s: %d videos" % (e, sym, count))
        lines.append("")

    if highlights:
        lines.append("\U0001f3ac <b>SONNET HIGHLIGHTS</b>")
        for ch, title, takeaway, ts in highlights:
            lines.append("\U0001f4fa %s \u2014 %s" % (ch, ts))
            lines.append('   \U0001f3ac "%s"' % title)
            lines.append("   %s" % takeaway)
            lines.append("")

    lines.append("\U0001f4e8 Full Sonnet analyses arrive as individual messages")
    lines.append("\U0001f50d Use /analyse [URL] for on-demand deep analysis")

    return "\n".join(lines)
