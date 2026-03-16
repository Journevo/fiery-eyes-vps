"""YouTube Dashboard — summary view for the Intel > YouTube button."""
import json
from datetime import datetime, timezone
from db.connection import execute
from config import get_logger

log = get_logger("youtube_dashboard")


def generate_youtube_dashboard() -> str:
    """Generate YouTube summary dashboard (not full analyses)."""
    # Get all videos from last 24h
    rows = execute("""
        SELECT channel_name, title, processed_at, analysis_json, relevance_score,
               tokens_mentioned
        FROM youtube_videos
        WHERE processed_at > NOW() - INTERVAL '24 hours'
        ORDER BY relevance_score DESC NULLS LAST, processed_at DESC
    """, fetch=True)

    if not rows:
        return "📺 No YouTube videos analysed in last 24h."

    total = len(rows)
    sonnet = 0
    haiku = 0
    token_counts = {}
    highlights = []
    all_summaries = []

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

    for ch, title, ts, analysis_json, rel, tokens_str in rows:
        is_sonnet = ch in HIGH_PRIORITY
        if is_sonnet:
            sonnet += 1
        else:
            haiku += 1

        # Count token mentions
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

        # Extract one-line takeaway for highlights (Sonnet only, top 5)
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
                    # Get first sentence
                    for line in summary.split("\n"):
                        line = line.strip()
                        if line and not line.startswith("**") and not line.startswith("#") and len(line) > 30:
                            takeaway = line[:100]
                            if len(line) > 100:
                                takeaway = takeaway.rsplit(" ", 1)[0] + "..."
                            break
            if not takeaway:
                takeaway = title[:80] if title else "Analysis available"
            highlights.append((ch, title[:45], takeaway))

    # Build output
    lines = []
    lines.append("📺 YOUTUBE TODAY — %d videos (%d Sonnet, %d Haiku)\n" % (total, sonnet, haiku))

    # Top mentioned tokens
    if token_counts:
        sorted_tokens = sorted(token_counts.items(), key=lambda x: x[1], reverse=True)[:6]
        lines.append("TOP MENTIONS:")
        for sym, count in sorted_tokens:
            lines.append("  %s: %d videos" % (sym, count))
        lines.append("")

    # Sonnet highlights
    if highlights:
        lines.append("SONNET HIGHLIGHTS:")
        for ch, title, takeaway in highlights:
            lines.append("📺 %s — \"%s\"" % (ch, title))
            lines.append("   %s" % takeaway)
        lines.append("")

    lines.append("Full analyses arrive as individual messages (Sonnet)")
    lines.append("Use /analyse [URL] for on-demand deep analysis")

    return "\n".join(lines)
