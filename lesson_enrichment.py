"""lesson_enrichment.py — Auto-enrich lessons from YouTube summaries via keyword matching."""

import json
from db.connection import execute, execute_one
from config import get_logger

log = get_logger("lesson_enrichment")

LESSON_KEYWORDS = {
    ("macro", 2): ["jobless claims", "unemployment", "300k", "300,000", "layoffs", "nonfarm"],
    ("macro", 3): ["inflation", "cpi", "core pce", "stagflation"],
    ("macro", 5): ["10-year", "10y yield", "bond yield", "treasury yield"],
    ("macro", 6): ["yield curve", "2y10y", "inversion", "uninvert"],
    ("macro", 7): ["oil shock", "strait of hormuz", "brent crude", "oil price"],
    ("macro", 8): ["dxy", "dollar index", "strong dollar", "weak dollar"],
    ("macro", 9): ["m2", "net liquidity", "global liquidity", "fed balance"],
    ("macro", 10): ["late cycle", "rotation", "altcoins bleed", "risk curve"],
    ("cycle", 2): ["500 day", "halving", "pre-halving"],
    ("cycle", 3): ["bmsb", "bull market support", "50 week", "50w sma"],
    ("cycle", 4): ["fear and greed", "extreme fear"],
    ("cycle", 7): ["capitulation", "bear market phase"],
    ("ta", 2): ["fibonacci", "786", "886", "78.6", "88.6"],
    ("ta", 5): ["bear flag", "bull flag", "flag pattern"],
    ("ta", 7): ["cvd", "cumulative volume"],
}


def check_enrichment(summary_text: str, voice_name: str, video_date: str):
    """Check if a YouTube summary matches any lesson keywords and enrich."""
    if not summary_text:
        return

    lower = summary_text.lower()

    for (module, lesson_num), keywords in LESSON_KEYWORDS.items():
        for keyword in keywords:
            if keyword not in lower:
                continue

            # Extract first sentence containing the keyword
            sentences = summary_text.replace("\n", ". ").split(". ")
            insight = ""
            for s in sentences:
                if keyword.lower() in s.lower():
                    insight = s.strip()[:200]
                    break

            if not insight:
                insight = keyword

            enrichment = {
                "voice": voice_name,
                "date": video_date,
                "insight": insight,
            }

            # Get current enrichments
            row = execute_one(
                "SELECT enrichments FROM lessons WHERE module = %s AND lesson_number = %s",
                (module, lesson_num))
            if not row:
                break

            try:
                enrichments = json.loads(row[0] or "[]")
            except Exception:
                enrichments = []

            # Check if this voice+date combo already enriched this lesson
            for e in enrichments:
                if e.get("voice") == voice_name and e.get("date") == video_date:
                    break
            else:
                enrichments.append(enrichment)
                enrichments = enrichments[-10:]  # Keep last 10

                execute(
                    "UPDATE lessons SET enrichments = %s, updated_at = NOW() WHERE module = %s AND lesson_number = %s",
                    (json.dumps(enrichments), module, lesson_num))
                log.info("Enriched %s/%d from %s: %s", module, lesson_num, voice_name, keyword)

            break  # One enrichment per lesson per video
